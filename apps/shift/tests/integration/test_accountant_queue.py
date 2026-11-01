"""The accountant's queue end to end (S01 §3 «ک»/«د»/«ب», owner document «کار حسابدار»).

The accountant owns money and price and never counts, so her queue is the open money work of the clinic
and nothing else: the card-to-card receipts she matches against the bank account, the withdrawals that
left the box, the refund requests waiting for the doctor, and the month's reward list. Purchasing is
SHF-4's and has no section here (M-07).

Two things can hurt the clinic from this page and both are gated here: the page must carry no reference
number and no figure of a shift beyond the item it shows (R-01, R-02, ADR-011), and a role without the
queue must receive a 403 whose body holds no item at all. The decisions are posted exactly as the page's
own script posts them — the phone's existing `/api/v1/events` endpoint with `event.approved` — so the
queue has no second decision path to drift from the one SHF-3 tested.

Every value is invented and the log is synthetic (ADR-006).
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from clocks import FixedClock
from fastapi.testclient import TestClient
from photo_fixtures import RECEIPT_SHA

from event_fixtures import build_event
from shift.app import create_app
from shift.common.clock import TEHRAN, to_tehran
from shift.common.ids import new_ulid
from shift.common.jalali import format_jalali, parse_jalali
from shift.config.settings import Settings
from shift.domain.policy import ShiftKey
from shift.domain.rewards import month_of, previous_month
from shift.domain.shift_clock import covering
from shift.services.accountant_queue import AccountantQueueService
from shift.services.audit import AuditEntry, AuditService
from shift.services.event_store import EventStore, NewEvent
from shift.services.rewards import RewardsService

QUEUE_PATH = "/accountant/queue"
# The clinic's seeded withdrawal cap (m0004) and one record on each side of it.
CAP = 300_000
BELOW_CAP = 250_000
ABOVE_CAP = 500_000
TRANSFER = 150_000
TRANSFER_ADMISSION = "A-1001"
REFUND_ADMISSION = "A-2001"
REASON = "خرید فوری برای درمانگاه"
ABOVE_REASON = "خرید تجهیزات اتاق CPR"
REFUND_REASON = "بیمار از ادامهٔ درمان انصراف داد."
# A frozen reference and a box figure: neither may appear on this page (R-01, R-02, ADR-011).
REFERENCE_CASH = 84_162_931
REFERENCE_ITEM = 73_519_284
BOX_FIGURE = 987_654_321
META_TOKEN = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
PENDING = "در انتظار تصمیم"


@dataclass(frozen=True, slots=True)
class Seeded:
    """The app and the three money records the queue must show, by their own ids."""

    client: TestClient
    transfer: str
    below_cap: str
    above_cap: str
    refund: str


@pytest.fixture()
def accountant_page(settings: Settings) -> Iterator[Seeded]:
    """A synthetic app: the roster, the pages `create_app` wired, and the money records in its log."""
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        yield _seed(client)


@pytest.fixture()
def payroll_client(settings: Settings) -> Iterator[TestClient]:
    """The same app, with the month's reward ledger handed to the queue, as the lifespan will wire it."""
    with TestClient(create_app(settings)) as client:
        state = client.app.state
        seed_roster(state.database, pepper=state.secret_key)
        state.accountant_queue = AccountantQueueService(
            state.database,
            events=state.shift_events,
            settings=state.settings_service,
            rewards=RewardsService(state.database, state.events, clock=state.clock),
            clock=state.clock,
        )
        _seed(client)
        yield client


def _seed(client: TestClient) -> Seeded:
    """A card transfer, two withdrawals (one each side of the cap), a refund, and the frozen reference."""
    state = client.app.state
    # The shift the clinic is in right now, so each row reports the cap that is in force for it.
    shift = covering(to_tehran(state.clock.now_utc()))
    moment = state.clock.now_utc()
    store = EventStore(state.database, clock=state.clock)
    reception = ROLES["reception"].staff_id
    nurse = ROLES["nurse"].staff_id
    manager = ROLES["manager"].staff_id
    seeded: dict[str, str] = {}
    for key, event_type, payload, minutes in (
        (
            "transfer",
            "card.transfer_reported",
            {"amount": TRANSFER, "admission_no": TRANSFER_ADMISSION, "photo_sha256": RECEIPT_SHA},
            9,
        ),
        (
            "below_cap",
            "cash.withdrawn",
            {
                "amount": BELOW_CAP,
                "recipient_id": nurse,
                "reason": REASON,
                "manager_id": None,
                "photo_sha256": RECEIPT_SHA,
            },
            7,
        ),
        (
            "above_cap",
            "cash.withdrawn",
            {"amount": ABOVE_CAP, "recipient_id": nurse, "reason": ABOVE_REASON, "manager_id": manager},
            5,
        ),
        ("refund", "refund.requested", {"admission_no": REFUND_ADMISSION, "reason": REFUND_REASON}, 3),
    ):
        event = _event(event_type, payload, moment - timedelta(minutes=minutes), shift, reception)
        seeded[key] = store.append(event).event.event_id
    # The box's own opening figure and the frozen reference: the page must show neither (R-01, R-02).
    store.append(_event("handover.started", {"opening_cash": BOX_FIGURE}, moment, shift, reception))
    AuditService(state.database, clock=state.clock).record(
        AuditEntry(
            actor_id=reception,
            action="handover.reference.ho-queue",
            subject_id="ho-queue",
            detail={
                "cash": REFERENCE_CASH,
                "items": [{"item_id": "para_500_amp", "location_id": "shelf_reception", "reference": REFERENCE_ITEM}],
            },
        )
    )
    return Seeded(
        client=client,
        transfer=seeded["transfer"],
        below_cap=seeded["below_cap"],
        above_cap=seeded["above_cap"],
        refund=seeded["refund"],
    )


def _event(
    event_type: str,
    payload: dict[str, Any],
    at: datetime,
    shift: ShiftKey,
    actor: str,
    *,
    ref_event_id: str | None = None,
) -> NewEvent:
    """One synthetic phone record of `shift`, stamped with the instant it claims to have happened."""
    return build_event(
        event_type,
        payload=payload,
        shift_id=shift.text,
        actor_id=actor,
        device_id=ROLES["reception"].device_id,
        client_at=at,
        work_date=shift.work_date,
        ref_event_id=ref_event_id,
        idempotency_key=f"queue-{event_type}-{at.isoformat()}-{ref_event_id or ''}",
    )


def _signed_in(seeded: Seeded, role: str = "accountant") -> TestClient:
    """Sign in the real way; the client keeps the session cookie it was issued."""
    return _login(seeded.client, role)


def _login(client: TestClient, role: str = "accountant") -> TestClient:
    """Walk the real login handshake for one role."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return client


def _queue(seeded: Seeded) -> str:
    """The queue page as the accountant reads it."""
    response = _signed_in(seeded).get(QUEUE_PATH)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.text


def _row(body: str, event_id: str) -> str:
    """The slice of the page that belongs to one queue item."""
    start = body.index(f'data-event-id="{event_id}"')
    end = body.find("</article>", start)
    assert end > start, f"the row of {event_id} is not closed"
    return body[start:end]


def _decide(seeded: Seeded, body: str, event_id: str, event_type: str) -> Any:
    """Post a decision exactly as `accountant.js` posts it: the phone's own endpoint and CSRF token."""
    token = META_TOKEN.search(body)
    assert token is not None
    client_event_id = new_ulid()
    return seeded.client.post(
        "/api/v1/events",
        json={
            "type": event_type,
            "ref_event_id": event_id,
            "payload": {},
            "client_at": datetime.now(UTC).isoformat(),
            "client_event_id": client_event_id,
            "idempotency_key": client_event_id,
        },
        headers={"X-CSRFToken": token.group(1)},
    )


def test_the_queue_shows_the_open_money_work_with_its_state_and_cap(accountant_page: Seeded) -> None:
    body = _queue(accountant_page)

    transfer = _row(body, accountant_page.transfer)
    assert "پذیرش یک" in transfer, "the accountant reads who reported it"
    assert TRANSFER_ADMISSION in transfer
    assert f"{TRANSFER:,}" in transfer and "تومان" in transfer
    assert PENDING in transfer

    below = _row(body, accountant_page.below_cap)
    assert f"{BELOW_CAP:,}" in below
    assert "پرستار یک" in below, "a withdrawal names the person who took the money"
    assert REASON in below
    assert "تا سقف" in below
    assert PENDING in below

    above = _row(body, accountant_page.above_cap)
    assert f"{ABOVE_CAP:,}" in above
    assert "بالای سقف" in above, "the over-cap withdrawal is flagged, as the owner's rule asks"
    assert ABOVE_REASON in above
    assert f"سقف همان شیفت: {CAP:,} تومان" in body, "the cap is the versioned setting of that shift"

    # A5: the receipt the record carries is one tap away for the accountant who matches the money.
    assert f'href="/api/v1/photos/{RECEIPT_SHA}"' in transfer and "عکس رسید" in transfer
    assert f'href="/api/v1/photos/{RECEIPT_SHA}"' in below
    assert "عکس رسید" not in above, "a record without a photo shows no dead link"

    refund = _row(body, accountant_page.refund)
    assert REFUND_ADMISSION in refund
    assert REFUND_REASON in refund

    # Only the item this person may actually close carries the decision buttons: a refund belongs to
    # the shift doctor, and this withdrawal to the recipient the record named (E-02, E-04, E-12).
    assert 'data-decision="approve"' in transfer
    for undecidable in (below, above, refund):
        assert "data-decision" not in undecidable


def test_the_accountants_approval_closes_the_transfer_and_leaves_the_queue(accountant_page: Seeded) -> None:
    body = _queue(accountant_page)

    approved = _decide(accountant_page, body, accountant_page.transfer, "event.approved")

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "recorded"
    after = accountant_page.client.get(QUEUE_PATH).text
    assert f'data-event-id="{accountant_page.transfer}"' not in after, "a decided record leaves the queue"
    for still_open in (accountant_page.below_cap, accountant_page.above_cap, accountant_page.refund):
        assert f'data-event-id="{still_open}"' in after


def test_a_role_without_the_queue_gets_a_403_that_carries_no_item(accountant_page: Seeded) -> None:
    response = _signed_in(accountant_page, "reception").get(QUEUE_PATH, headers={"Accept": "text/html"})

    assert response.status_code == 403
    for marker in (TRANSFER_ADMISSION, REFUND_ADMISSION, "پرستار یک", f"{BELOW_CAP:,}"):
        assert marker not in response.text


def test_a_browser_without_a_session_is_sent_to_the_login_page(accountant_page: Seeded) -> None:
    response = accountant_page.client.get(QUEUE_PATH, headers={"Accept": "text/html"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_the_queue_page_carries_no_reference_number_and_no_box_figure(accountant_page: Seeded) -> None:
    """R-01/R-02: the page shows the item itself; a shift's reference and its box stay on the server."""
    _signed_in(accountant_page)

    body = accountant_page.client.get(QUEUE_PATH).text
    for figure in (REFERENCE_CASH, REFERENCE_ITEM, BOX_FIGURE):
        assert str(figure) not in body
        assert f"{figure:,}" not in body
    assert "reference" not in body


def test_the_manager_reads_the_same_queue(accountant_page: Seeded) -> None:
    """The owner granted the queue to the accountant and the manager, so both open the same page."""
    page = _signed_in(accountant_page, "manager").get(QUEUE_PATH)

    assert page.status_code == 200
    assert REFUND_ADMISSION in page.text


def _seed_payroll(client: TestClient) -> str:
    """A task credited this month and a reward confirmed last month that this month takes back.

    The owner's rule (S01 §5): a reward belongs to the month it was confirmed in, and a revocation comes
    off the **next** month's payslip — so the month the accountant is closing carries both a credit and a
    deduction. Every record is synthetic and appended to the log; the ledger derives the rest.
    """
    state = client.app.state
    shift = covering(to_tehran(state.clock.now_utc()))
    now = state.clock.now_utc()
    last_month_day = parse_jalali(f"{previous_month(month_of(now)).replace('-', '/')}/15")
    earlier = datetime.combine(last_month_day, time(9, 0), tzinfo=TEHRAN)
    manager = ROLES["manager"].staff_id
    nurse = ROLES["nurse"].staff_id
    revoked = (
        EventStore(state.database, clock=FixedClock(earlier))
        .append(
            _event(
                "reward.confirmed",
                {"staff_id": nurse, "work_type": "manager_task", "amount_toman": 15_000, "note": None},
                earlier,
                ShiftKey.parse(f"{last_month_day.isoformat()}#1"),
                manager,
            )
        )
        .event.event_id
    )
    EventStore(state.database, clock=FixedClock(earlier)).append(
        _event("reward.revoked", {"reason": "ثبت تکراری بود"}, earlier, shift, manager, ref_event_id=revoked)
    )
    EventStore(state.database, clock=FixedClock(now)).append(
        _event(
            "reward.confirmed",
            {"staff_id": nurse, "work_type": "manager_task", "amount_toman": 40_000, "note": "تعمیر پیگیری شد"},
            now,
            shift,
            manager,
        )
    )
    return f"{format_jalali(to_tehran(now).date())} {to_tehran(now):%H:%M}"


def test_the_month_payroll_carries_its_label_its_name_and_the_payslip_figures(payroll_client: TestClient) -> None:
    """«کار حسابدار»: the owner asks the accountant for the confirmed rewards and the deductions."""
    stamp = _seed_payroll(payroll_client)

    response = _login(payroll_client).get(QUEUE_PATH)

    assert response.status_code == 200, response.text
    body = response.text
    assert "کار ارجاعی مدیر" in body, "the work type reads in the ledger's own Persian label"
    assert "پرستار یک" in body
    assert "40,000" in body and "-15,000" in body, "a deduction keeps the ledger's own sign"
    assert stamp in body, "every reward row carries its Tehran instant"
    assert "ثبت تکراری بود" in body, "a deduction says why it was taken back"
    assert "جمع پاداش: 40,000 تومان · جمع کسر: 15,000 تومان · خالص: 25,000 تومان" in body
    assert "خالص پرستار یک: 25,000 تومان" in body, "the figure the accountant writes on the payslip"
