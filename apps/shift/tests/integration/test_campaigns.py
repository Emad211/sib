"""The campaign calendar behind the shift event «ت»: a manager defines the discount beforehand (S01 §3).

A campaign is configuration, not an event: fixing a typo saves the same id again, the row is replaced
and the event log stays untouched. Two things can hurt the clinic from this screen, and both are gated
here — a discount that would count outside the days the manager set, and a door a reception phone could
open. The rest pins what the manager came for: the list reads in Jalali with Tehran stamps, and the
form posts through the same manager-only API the page's own script uses.

Every value is invented; no production database and no patient data is read (ADR-006).
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from clocks import FixedClock
from fastapi import Request
from fastapi.testclient import TestClient

from shift.adapters.db.core import Database
from shift.api import deps, routes_campaigns
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.common.jalali import format_jalali
from shift.config.settings import Settings
from shift.domain.permissions import Permission
from shift.services.campaigns import MAX_AMOUNT_TOMAN, CampaignError, CampaignService

MANAGER = ROLES["manager"].staff_id
CAMPAIGN_ID = "campaign-autumn"
SHIFT_CAMPAIGN: dict[str, Any] = {
    "name": "تخفیف پاییزهٔ ویزیت",
    "service": "ویزیت پزشک عمومی",
    "amount_toman": 50_000,
    "starts_on": "2026-10-26",
    "ends_on": "2026-11-05",
}
BEFORE = date(2026, 10, 25)
FIRST_DAY = date(2026, 10, 26)
LAST_DAY = date(2026, 11, 5)
AFTER = date(2026, 11, 6)
STAMP = re.compile(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}")


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TEHRAN)


def _save(campaigns: CampaignService, campaign_id: str = CAMPAIGN_ID, **changes: Any) -> Any:
    return campaigns.save(campaign_id, **{**SHIFT_CAMPAIGN, **changes}, actor_id=MANAGER)


# --- the service: the discount exists only where the manager put it -------------------------------


def test_a_campaign_is_saved_ahead_of_time_and_covers_only_its_own_days(database: Database) -> None:
    service = CampaignService(database, clock=FixedClock(_at(BEFORE, 9)))

    saved = _save(service)

    assert saved.campaign_id == CAMPAIGN_ID
    assert saved.name == SHIFT_CAMPAIGN["name"]
    assert saved.service == SHIFT_CAMPAIGN["service"]
    assert saved.amount_toman == 50_000
    assert (saved.starts_on, saved.ends_on) == (FIRST_DAY, LAST_DAY)
    assert saved.created_by == MANAGER
    assert service.get(CAMPAIGN_ID) == saved
    assert service.get("campaign-nobody-defined") is None
    assert service.get("not an id") is None, "an id that cannot name a row is simply not found"
    # S01 §3 «ت»: the phone may offer a campaign only on the days it runs.
    assert service.active_on(BEFORE) == []
    assert [campaign.campaign_id for campaign in service.active_on(FIRST_DAY)] == [CAMPAIGN_ID]
    assert [campaign.campaign_id for campaign in service.active_on(LAST_DAY)] == [CAMPAIGN_ID]
    assert service.active_on(AFTER) == []


def test_two_campaigns_on_the_same_day_are_offered_by_name(database: Database) -> None:
    service = CampaignService(database, clock=FixedClock(_at(BEFORE, 9)))
    _save(service, "campaign-zeta", name="تخفیف پاییزهٔ ویزیت")
    _save(service, "campaign-alpha", name="تخفیف آزمایشگاه")

    assert [campaign.name for campaign in service.active_on(FIRST_DAY)] == [
        "تخفیف آزمایشگاه",
        "تخفیف پاییزهٔ ویزیت",
    ]


def test_the_list_is_newest_first_and_a_fix_replaces_the_same_row(database: Database) -> None:
    clock = FixedClock(_at(date(2026, 10, 20), 9))
    service = CampaignService(database, clock=clock)
    for offset, campaign_id in enumerate(("campaign-oldest", "campaign-middle", "campaign-newest")):
        clock.moment = _at(date(2026, 10, 20 + offset), 9)
        _save(service, campaign_id, amount_toman=10_000 + offset)
    original = service.get("campaign-oldest")
    assert original is not None

    clock.moment = _at(BEFORE, 9)
    fixed = _save(service, "campaign-oldest", name="تخفیف پاییزهٔ ویزیت (اصلاح‌شده)", amount_toman=60_000)

    assert [campaign.campaign_id for campaign in service.all()] == [
        "campaign-newest",
        "campaign-middle",
        "campaign-oldest",
    ]
    assert len(service.all()) == 3, "a fix replaces the row instead of adding a second one"
    assert fixed.name == "تخفیف پاییزهٔ ویزیت (اصلاح‌شده)"
    assert fixed.amount_toman == 60_000
    assert fixed.created_at == original.created_at, "the row keeps the stamp it was created with"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"starts_on": "2026-11-06"}, "a range that runs backwards"),
        ({"name": "   "}, "a blank name"),
        ({"service": ""}, "a blank service"),
        ({"amount_toman": -1}, "a negative amount"),
        ({"amount_toman": 50_000.5}, "a fractional amount"),
        ({"amount_toman": "50,000"}, "an amount that is not a whole number"),
        ({"amount_toman": MAX_AMOUNT_TOMAN + 1}, "an amount above the ceiling"),
        ({"starts_on": "1405/08/06"}, "a date that is not ISO"),
    ],
)
def test_a_campaign_the_clinic_would_turn_away_writes_nothing(
    database: Database, changes: dict[str, Any], reason: str
) -> None:
    service = CampaignService(database, clock=FixedClock(_at(BEFORE, 9)))

    with pytest.raises(CampaignError):
        _save(service, **changes)

    assert service.all() == [], reason


# --- the API: manager-only, and the page is the same door -----------------------------------------


def _campaigns(request: Request) -> CampaignService:
    """The app factory will wire `app.state.campaigns`; this test builds the same door itself."""
    return request.app.state.campaigns


@pytest.fixture()
def campaigns_client(settings: Settings) -> Iterator[TestClient]:
    """The real app plus the campaign router, with a real session per role."""
    app = create_app(settings)
    app.include_router(
        routes_campaigns.build_campaign_router(
            guard=deps.require(Permission.CATALOG_MANAGE),
            page_guard=deps.require_page(Permission.SETTINGS_CHANGE),
            campaigns=_campaigns,
        )
    )
    with TestClient(app) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        client.app.state.campaigns = CampaignService(client.app.state.database)
        yield client


def _signed_in(client: TestClient, role: str = "manager") -> dict[str, str]:
    """Sign in the real way and return the CSRF header that session's writes need."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return {"X-CSRFToken": response.json()["csrf_token"]}


def test_a_manager_saves_lists_and_fixes_a_campaign(campaigns_client: TestClient) -> None:
    headers = _signed_in(campaigns_client)

    saved = campaigns_client.put(f"/api/v1/campaigns/{CAMPAIGN_ID}", json=SHIFT_CAMPAIGN, headers=headers)

    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "campaign_id": CAMPAIGN_ID,
        **SHIFT_CAMPAIGN,
        "created_by": MANAGER,
        "created_at": saved.json()["created_at"],
    }
    assert campaigns_client.get("/api/v1/campaigns").json() == [saved.json()]
    # A refused range is answered with its reason, never a 500 and never a half-written row.
    refused = campaigns_client.put(
        f"/api/v1/campaigns/{CAMPAIGN_ID}",
        json={**SHIFT_CAMPAIGN, "starts_on": "2026-11-05", "ends_on": "2026-10-26"},
        headers=headers,
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["message"]
    # The id is addressed in the path, so a manager's typo there is refused with its reason too.
    malformed = campaigns_client.put("/api/v1/campaigns/not%20an%20id", json=SHIFT_CAMPAIGN, headers=headers)
    assert malformed.status_code == 400
    assert campaigns_client.get("/api/v1/campaigns").json() == [saved.json()]


@pytest.mark.parametrize("role", ["reception", "nurse"])
def test_a_staff_phone_never_reaches_the_campaign_calendar(campaigns_client: TestClient, role: str) -> None:
    headers = _signed_in(campaigns_client, role)

    assert campaigns_client.get("/api/v1/campaigns").status_code == 403
    assert (
        campaigns_client.put(f"/api/v1/campaigns/{CAMPAIGN_ID}", json=SHIFT_CAMPAIGN, headers=headers).status_code
        == 403
    )
    assert campaigns_client.get("/manager/campaigns", headers={"Accept": "text/html"}).status_code == 403


def test_a_browser_without_a_session_is_sent_to_the_login_page(campaigns_client: TestClient) -> None:
    assert campaigns_client.get("/api/v1/campaigns").status_code == 401

    page = campaigns_client.get("/manager/campaigns", headers={"Accept": "text/html"}, follow_redirects=False)

    assert page.status_code == 303
    assert page.headers["location"].startswith("/login")


def test_the_manager_page_lists_the_campaign_in_jalali_and_keeps_it_out_of_caches(
    campaigns_client: TestClient,
) -> None:
    headers = _signed_in(campaigns_client)
    saved = campaigns_client.put(f"/api/v1/campaigns/{CAMPAIGN_ID}", json=SHIFT_CAMPAIGN, headers=headers)
    assert saved.status_code == 200, saved.text

    page = campaigns_client.get("/manager/campaigns")

    assert page.status_code == 200
    # A manager's screen must not sit in a shared cache, and the shell's security headers stay.
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert SHIFT_CAMPAIGN["name"] in page.text
    assert format_jalali(FIRST_DAY) in page.text and format_jalali(LAST_DAY) in page.text
    assert "50,000" in page.text
    assert STAMP.search(page.text), "the creation stamp is Jalali date and Tehran time"
    assert "/static/manager-campaigns.js" in page.text
    # R-02, ADR-011: the page carries no reference number, and nothing that reads like one.
    assert "reference" not in page.text
