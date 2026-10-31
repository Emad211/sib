"""«گزارش کمپین» end to end (S01 §3 «ت», review finding F8).

The owner's event table sends a campaign discount to a «گزارش کمپین», and the manager reads it for one
reason: did the campaign that was defined actually give anything away, and how much. So the report
counts the discounts the log holds in the window, prices each one from the campaign calendar, and shows
a campaign that has none — a silent campaign is the finding, not an empty screen. A discount whose
campaign the calendar no longer holds still counts (it happened) and is labelled by its id, and its
price is stated as unknown instead of invented.

The whole page is one manager-facing read: a nurse must receive a 403 whose body carries no campaign
name and no figure at all, and the page must never sit in a shared cache. Every value here is invented
and the log is synthetic (ADR-006).
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from clocks import FixedClock
from fastapi.testclient import TestClient

from event_fixtures import build_event
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.config.settings import Settings
from shift.services.campaign_report import CampaignReportService
from shift.services.campaigns import CampaignService
from shift.services.event_store import EventStore

REPORT_PATH = "/accountant/campaigns"
# The Jalali month 1405/08 as its two ISO days: first day, last day (a 30-day month).
MONTH_FIRST = date(2026, 10, 23)
MONTH_LAST = date(2026, 11, 21)
SPRING_ID = "cmp-spring"
SPRING_NAME = "تخفیف ویزیت بهار"
SPRING_AMOUNT = 50_000
SILENT_ID = "cmp-silent"
SILENT_NAME = "تخفیف دندان‌پزشکی"
SILENT_AMOUNT = 20_000
# A campaign the calendar no longer holds: its discounts happened and are counted by id.
GONE_ID = "cmp-gone"
INSIDE = (date(2026, 10, 25), date(2026, 11, 2))
OUTSIDE = date(2026, 10, 22)
GONE_AT = date(2026, 11, 5)
DEFAULT_INSTANT = datetime(2026, 10, 28, 9, 0, tzinfo=TEHRAN)


@pytest.fixture()
def report_client(settings: Settings) -> Iterator[TestClient]:
    """A synthetic app: two campaigns in the calendar and four discounts in the log."""
    with TestClient(create_app(settings)) as client:
        state = client.app.state
        seed_roster(state.database, pepper=state.secret_key)
        _campaign(state.campaigns, SPRING_ID, name=SPRING_NAME, amount=SPRING_AMOUNT)
        _campaign(state.campaigns, SILENT_ID, name=SILENT_NAME, amount=SILENT_AMOUNT)
        for day, campaign_id, admission_no in (
            (INSIDE[0], SPRING_ID, "A-2001"),
            (INSIDE[1], SPRING_ID, "A-2002"),
            (OUTSIDE, SPRING_ID, "A-2003"),
            (GONE_AT, GONE_ID, "A-2004"),
        ):
            _discount(state.database, day, campaign_id, admission_no)
        yield client


def _campaign(campaigns: CampaignService, campaign_id: str, *, name: str, amount: int) -> None:
    """One campaign of the clinic's own calendar, covering the whole window and a week either side."""
    campaigns.save(
        campaign_id,
        name=name,
        service="ویزیت",
        amount_toman=amount,
        starts_on=MONTH_FIRST - timedelta(days=7),
        ends_on=MONTH_LAST + timedelta(days=7),
        actor_id=ROLES["manager"].staff_id,
    )


def _discount(database: Database, day: date, campaign_id: str, admission_no: str) -> None:
    """One «ت» discount appended at a fixed instant, so the window is the test's, never the clock's."""
    moment = datetime(day.year, day.month, day.day, 10, 30, tzinfo=TEHRAN)
    EventStore(database, clock=FixedClock(moment)).append(
        build_event(
            "campaign.discount_applied",
            payload={"campaign_id": campaign_id, "admission_no": admission_no},
            shift_id=f"{day.isoformat()}#1",
            actor_id=ROLES["reception"].staff_id,
            device_id=ROLES["reception"].device_id,
            client_at=moment,
            work_date=day,
            idempotency_key=f"discount-{campaign_id}-{admission_no}",
        )
    )


def _signed_in(client: TestClient, role: str = "manager") -> TestClient:
    """Sign in the real way; the client keeps the session cookie it was issued."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return client


def _row(body: str, campaign_id: str) -> str:
    """The slice of the page that belongs to one campaign row."""
    start = body.index(f'data-campaign-id="{campaign_id}"')
    end = body.find("</article>", start)
    assert end > start, f"the row of {campaign_id} is not closed"
    return body[start:end]


def test_the_report_counts_prices_and_names_a_silent_campaign(report_client: TestClient) -> None:
    response = _signed_in(report_client).get(
        f"{REPORT_PATH}?from={MONTH_FIRST.isoformat()}&to={MONTH_LAST.isoformat()}"
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.text

    spring = _row(body, SPRING_ID)
    assert SPRING_NAME in spring
    assert "تعداد تخفیف: 2" in spring, "the discount a day before the window is not this month's"
    assert f"مبلغ کمپین: {SPRING_AMOUNT:,}" in spring
    assert f"جمع تخفیف: {SPRING_AMOUNT * 2:,}" in spring, "the total is count × amount, never a guess"

    silent = _row(body, SILENT_ID)
    assert SILENT_NAME in silent, "a campaign with no discount is the finding the manager came for"
    assert "تعداد تخفیف: 0" in silent
    assert "بدون تخفیف در این بازه" in silent, "a silent campaign says so in words, not in a bare zero"

    gone = _row(body, GONE_ID)
    assert GONE_ID in gone, "a discount whose campaign left the calendar is labelled by its id"
    assert "تعداد تخفیف: 1" in gone, "it happened, so it counts"
    assert "نامشخص" in gone, "its amount is unknown, and the report never invents one"

    assert f"جمع کل: {SPRING_AMOUNT * 2:,} تومان" in body
    assert "تعداد تخفیف‌ها: 3" in body, "the unknown-priced discount is counted, only its price is not"
    assert "1 تخفیف بی‌مبلغ" in body, "the manager is told one discount could not be priced"


def test_the_default_window_is_the_current_jalali_month(database: Database) -> None:
    """No `from`/`to` asks for the month the accountant is closing, not the last thirty days."""
    clock = FixedClock(DEFAULT_INSTANT)
    service = CampaignReportService(database, campaigns=CampaignService(database, clock=clock), clock=clock)

    report = service.report()

    assert (report.first_day, report.last_day) == (MONTH_FIRST, MONTH_LAST)


def test_a_nurse_gets_a_403_that_carries_no_campaign_name(report_client: TestClient) -> None:
    response = _signed_in(report_client, "nurse").get(REPORT_PATH, headers={"Accept": "text/html"})

    assert response.status_code == 403
    for marker in (SPRING_NAME, SILENT_NAME, SPRING_ID, GONE_ID, "تخفیف", f"{SPRING_AMOUNT:,}"):
        assert marker not in response.text


def test_a_browser_without_a_session_is_sent_to_the_login_page(report_client: TestClient) -> None:
    response = report_client.get(REPORT_PATH, headers={"Accept": "text/html"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
