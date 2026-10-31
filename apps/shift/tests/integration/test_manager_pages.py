"""The manager's pages end to end: the card, its history, the settings screen (SHF-6.1, S01 §6).

A manager's card carries the handover cash total and the discrepancy gaps, so exactly two things can
hurt the clinic from this screen, and both are gated here: a non-manager must never receive a section
or a figure (403 with a body that holds no card data at all), and no page may repeat a frozen
reference number (R-02, ADR-011). The rest of the module pins what the manager came for — the
emergency start read first, the unnamed discrepancy named as unnamed, and the settings change that
reaches the existing API.

One synthetic Monday feeds one stored card; every value is invented and no production database or
patient data is read (ADR-006).
"""

import re
from collections.abc import Iterator
from datetime import date, datetime

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from clocks import FixedClock
from fastapi.testclient import TestClient

from event_fixtures import build_event
from shift.adapters.db import handover_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.config.settings import Settings
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.services.audit import AuditEntry, AuditService
from shift.services.event_store import EventStore

# Tuesday's card covers Monday; Wednesday's card covers a day with no work at all.
CARD_DAY = date(2026, 10, 27)
NEXT_DAY = date(2026, 10, 28)
CARD_PAGE = f"/manager/card/{CARD_DAY.isoformat()}"
NEXT_PAGE = f"/manager/card/{NEXT_DAY.isoformat()}"
FRIDAY_PAGE = "/manager/card/2026-10-30"  # ADR-018: Friday has no card at all
PAGES = ("/manager/card", "/manager/cards", CARD_PAGE, "/manager/settings")

CASH_IN_HANDOVER = 450_000
SECOND_CASH = 120_000
NAMED_GAP = -91_111  # the log keeps the sign; the card shows the size of the gap
UNNAMED_GAP = 5_000
# Frozen references live in `audit_log` and nowhere else (R-02, ADR-011).
CASH_REFERENCE = 84_162_931
ITEM_REFERENCE = 73_519_284
FIGURES = (CASH_IN_HANDOVER, SECOND_CASH, abs(NAMED_GAP), UNNAMED_GAP, CASH_REFERENCE, ITEM_REFERENCE)
CARD_DATA = ("ho-reception", "ho-nursing", "ho-open", "پرستار یک", "مدیر درمانگاه", "پذیرش یک")
# The card page renders these five headings, in this order, and only when the stored card has them.
SECTIONS = (
    "شروع اضطراری تحویل",
    "نقد ثبت‌شده در تحویل",
    "مغایرت‌ها",
    "شیفت‌ها و تحویل‌های روز",
    "تحویل‌های باز",
)
EMPTY_SECTION = "در این روز چیزی ثبت نشده است."
SETTING_LABELS = (
    "ساعت کارت روزانه",
    "مهلت تحویل گرفتن (دقیقه)",
    "سقف برداشت از صندوق (تومان)",
    "اعتبار نشست (ساعت)",
    "ساعت هشدار شمارش هفتگی",
    "روز شمارش هفتگی",
)
META_TOKEN = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


def _variants(figure: int) -> tuple[str, ...]:
    """Every spelling a figure could take on a page: plain and grouped by a comma."""
    return (str(figure), f"{figure:,}")


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TEHRAN)


@pytest.fixture()
def manager_client(settings: Settings) -> Iterator[TestClient]:
    """One app with a synthetic Monday, a rich card for Tuesday and an empty card for Wednesday."""
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        _synthetic_monday(client.app.state.database)
        assert client.app.state.daily_card.regenerate(CARD_DAY) is not None
        assert client.app.state.daily_card.regenerate(NEXT_DAY) is not None
        yield client


def _signed_in(client: TestClient, role: str = "manager") -> TestClient:
    """Sign in the real way; the client keeps the session cookie it was issued."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return client


def _flow(
    database: Database,
    *,
    handover_id: str,
    shift_id: str,
    flow: HandoverFlow,
    opened_at: datetime,
    outgoing: str,
    incoming: str,
    signed_at: datetime | None = None,
) -> None:
    with database.write() as connection:
        handover_repo.insert(
            connection,
            handover_id=handover_id,
            shift_id=shift_id,
            flow=flow,
            opened_at=opened_at,
            work_date=ShiftKey.parse(shift_id).work_date,
            outgoing_id=outgoing,
            incoming_id=incoming,
            created_at=opened_at,
        )
        if signed_at is not None:
            handover_repo.mark_signed(connection, handover_id, signed_at)


def _synthetic_monday(database: Database) -> None:
    """Two flows of Monday, one flow nobody closed, one emergency start, two gaps and two receipts."""
    clock = FixedClock(_at(CARD_DAY, 7, 31))
    store = EventStore(database, clock=clock)
    audit = AuditService(database, clock=clock)
    reception = ROLES["reception"].staff_id
    nurse = ROLES["nurse"].staff_id
    incoming = ROLES["multi_role"].staff_id
    manager = ROLES["manager"].staff_id
    monday = date(2026, 10, 26)
    _flow(
        database,
        handover_id="ho-open",
        shift_id="2026-10-25#3",
        flow=HandoverFlow.NURSING,
        opened_at=_at(date(2026, 10, 25), 20, 0),
        outgoing=reception,
        incoming=incoming,
    )
    _flow(
        database,
        handover_id="ho-reception",
        shift_id="2026-10-26#1",
        flow=HandoverFlow.RECEPTION,
        opened_at=_at(monday, 8, 5),
        outgoing=reception,
        incoming=incoming,
        signed_at=_at(monday, 8, 40),
    )
    _flow(
        database,
        handover_id="ho-nursing",
        shift_id="2026-10-26#2",
        flow=HandoverFlow.NURSING,
        opened_at=_at(monday, 14, 0),
        outgoing=nurse,
        incoming=incoming,
        signed_at=_at(monday, 14, 30),
    )
    for at, event_type, handover_id, shift_id, actor, payload in (
        (
            _at(monday, 14, 10),
            "handover.emergency_start_confirmed",
            "ho-nursing",
            "2026-10-26#2",
            nurse,
            {"manager_id": manager},
        ),
        (
            _at(monday, 9, 15),
            "discrepancy.recorded",
            "ho-reception",
            "2026-10-26#1",
            incoming,
            {
                "scope": "item",
                "item_id": "para_500_amp",
                "location_id": "shelf_reception",
                "difference": NAMED_GAP,
                "culprit_id": reception,
            },
        ),
        (
            _at(monday, 14, 20),
            "discrepancy.recorded",
            "ho-nursing",
            "2026-10-26#2",
            incoming,
            {"scope": "cash", "difference": UNNAMED_GAP, "culprit_id": None},
        ),
    ):
        clock.moment = at
        store.append(
            build_event(
                event_type,
                payload=payload,
                handover_id=handover_id,
                shift_id=shift_id,
                actor_id=actor,
                client_at=at,
                work_date=ShiftKey.parse(shift_id).work_date,
            )
        )
    for at, amount in ((_at(monday, 10, 0), CASH_IN_HANDOVER), (_at(monday, 16, 0), SECOND_CASH)):
        clock.moment = at
        audit.record(
            AuditEntry(
                actor_id=incoming,
                action="handover.cash_exception",
                subject_id="ho-reception",
                detail={"flow": "reception", "amount": amount},
            )
        )
    # The frozen reference of the flow that recorded its discrepancy: the pages must never repeat it.
    clock.moment = _at(monday, 8, 6)
    audit.record(
        AuditEntry(
            actor_id=incoming,
            action="handover.reference.ho-reception",
            subject_id="ho-reception",
            detail={
                "cash": CASH_REFERENCE,
                "items": [{"item_id": "para_500_amp", "location_id": "shelf_reception", "reference": ITEM_REFERENCE}],
            },
        )
    )


def test_a_manager_opens_every_page_and_the_card_reads_the_emergency_first(manager_client: TestClient) -> None:
    _signed_in(manager_client)
    pages = {path: manager_client.get(path) for path in PAGES}

    assert {path: page.status_code for path, page in pages.items()} == dict.fromkeys(PAGES, 200)
    for page in pages.values():
        # A manager's card must not sit in a shared cache, and the shell's security headers stay.
        assert page.headers["cache-control"] == "no-store"
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"

    card = pages[CARD_PAGE].text
    positions = [card.index(heading) for heading in SECTIONS]
    assert positions == sorted(positions), "the emergency start must lead the card (H-08)"
    # Who recorded it, which manager they named, and when: Jalali date + Tehran time.
    assert "ثبت‌کننده: پرستار یک" in card
    assert "مدیر تأییدکننده: مدیر درمانگاه" in card
    assert "1405/08/04 14:10" in card
    # H-08/H-07: the unnamed discrepancy says so; the named one carries its person.
    assert "بدون مقصر" in card
    assert "به نام پذیرش یک" in card
    # A gap reads as short or over, never as a bare magnitude (the card's own `direction`).
    assert "کسری" in card and "مازاد" in card
    assert f"{CASH_IN_HANDOVER:,}" in card
    assert f"{SECOND_CASH:,}" in card
    # The history lists both cards, newest first, and links to each one by date.
    history = pages["/manager/cards"].text
    assert history.index(NEXT_PAGE) < history.index(CARD_PAGE)
    # The newest card is the one /manager/card shows, and an empty section is a line, not a figure.
    assert NEXT_PAGE not in pages["/manager/card"].text, "the newest page is not the history"
    assert "ثبت‌کننده:" not in pages["/manager/card"].text
    assert EMPTY_SECTION in pages["/manager/card"].text
    # An unknown day and a malformed one are the 404 page, never a 500.
    for missing in (FRIDAY_PAGE, "/manager/card/not-a-date"):
        response = manager_client.get(missing, headers={"Accept": "text/html"})
        assert response.status_code == 404
        assert "text/html" in response.headers["content-type"]


@pytest.mark.parametrize("role", ["reception", "nurse"])
def test_a_non_manager_gets_a_403_that_carries_no_card_data(manager_client: TestClient, role: str) -> None:
    _signed_in(manager_client, role)
    for path in PAGES:
        response = manager_client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 403, path
        text = response.text
        assert all(heading not in text for heading in SECTIONS), path
        assert all(variant not in text for figure in FIGURES for variant in _variants(figure)), path
        assert all(marker not in text for marker in CARD_DATA), path


def test_a_browser_without_a_session_is_sent_to_the_login_page(manager_client: TestClient) -> None:
    for path in PAGES:
        response = manager_client.get(path, headers={"Accept": "text/html"}, follow_redirects=False)

        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/login")


def test_no_manager_page_repeats_a_reference_figure(manager_client: TestClient) -> None:
    """The card payload carries no reference; this is the page's own gate if that ever changes."""
    _signed_in(manager_client)
    with manager_client.app.state.database.read() as connection:
        detail = connection.exec_driver_sql(
            "SELECT detail FROM audit_log WHERE action = 'handover.reference.ho-reception'"
        ).scalar_one()
    assert str(CASH_REFERENCE) in str(detail)

    for path in PAGES:
        response = manager_client.get(path)
        assert response.status_code == 200, path
        assert all(
            variant not in response.text for figure in (CASH_REFERENCE, ITEM_REFERENCE) for variant in _variants(figure)
        )
        assert "reference" not in response.text


def test_the_settings_page_changes_a_setting_through_the_existing_api(manager_client: TestClient) -> None:
    _signed_in(manager_client)
    page = manager_client.get("/manager/settings")

    assert page.status_code == 200
    assert all(label in page.text for label in SETTING_LABELS)
    assert 'value="07:30"' in page.text, "the card time is shown with its current value"
    # Drive the API exactly as the page's own script does: same session, token from the meta tag.
    token = META_TOKEN.search(page.text)
    assert token is not None
    changed = manager_client.put(
        "/api/v1/settings/card_time",
        json={"value": "08:15"},
        headers={"X-CSRFToken": token.group(1)},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["value"] == "08:15"
    # R-09: a saved change takes effect from the next shift, and the page says which value is waiting.
    reloaded = manager_client.get("/manager/settings").text
    assert "08:15" in reloaded
    assert "تغییر در انتظار" in reloaded

    rejected = manager_client.put(
        "/api/v1/settings/session_ttl_hours",
        json={"value": "99"},
        headers={"X-CSRFToken": token.group(1)},
    )
    assert rejected.status_code == 400, "a refused value is answered with its reason, not a 200"
    assert rejected.json()["error"]["message"]


def test_a_fresh_install_shows_when_the_next_card_arrives(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        _signed_in(client)
        response = client.get("/manager/card")

    assert response.status_code == 200, "an install without a card yet is not an error page"
    assert "هنوز کارتی ساخته نشده است." in response.text
    assert "کارت بعدی" in response.text
    assert "07:30" in response.text, "the empty state says when the next card arrives"
