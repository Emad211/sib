"""The cutoff of a cash event is when it happened, not when it arrived (H-11, SHF-3 finding 1).

A phone that was offline hands its records over hours later. The cash, though, moved at the counter:
a withdrawal that happened before a flow's cutoff has to sit inside the figure the two people count,
and a receipt or a withdrawal that happened after the cutoff is the next shift's money and stays on
top of the agreed figure. The tests below run the critic's exact sequence (a pre-cutoff withdrawal
arriving during the flow), several consecutive shifts, the rebuild, and an event that only reaches
the server after its flow has already settled.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from clocks import FixedClock
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed

from event_fixtures import build_event
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import TEHRAN, parse_iso
from shift.domain.cash_cutoff import signed_cash
from shift.domain.handover_counts import CountStatus
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.handover_payloads import CountTarget
from shift.services.accounting_bridge import AccountingBridge
from shift.services.audit import AuditService
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.handover import Caller, HandoverService
from shift.services.late_cash import notice_late_cash
from shift.services.projections import ProjectionEngine

MOMENT = datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN)  # inside the morning shift
OUTGOING = "staff-out"
INCOMING = "staff-in"
CALLER = Caller(actor_id=INCOMING, device_id="device-1")
CASH = CountTarget(scope="cash")
SHELF = "shelf_reception"
PARA = "para_500_amp"
DICLO = "diclofenac_75_amp"
OPENING_CASH = 500_000
OPENING_SHELVES = [
    {"item_id": PARA, "location_id": SHELF, "quantity": 10},
    {"item_id": DICLO, "location_id": SHELF, "quantity": 4},
]


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(MOMENT)


@pytest.fixture()
def store(database: Database, clock: FixedClock) -> EventStore:
    """The store the test appends money with, so an arrival order can be staged by hand."""
    seed(database)
    return EventStore(database, clock=clock)


@pytest.fixture()
def service(
    database: Database, store: EventStore, clock: FixedClock, projections: ProjectionEngine, tmp_path: Path
) -> HandoverService:
    """A handover service on the seeded synthetic database and a readable synthetic accounting copy."""
    accounting = empty_accounting(tmp_path / "accounting.db")
    return HandoverService(
        database,
        store,
        projections=projections,
        catalog=CatalogService(database, clock=clock),
        audit=AuditService(database, clock=clock),
        clock=clock,
        bridge=AccountingBridge(database, store, clock=clock),
        accounting_path=str(accounting),
    )


def _start(service: HandoverService, *, opening_cash: int | None = None, incoming: str = INCOMING) -> str:
    """Open a reception flow, with the manager's baseline only when the test has none yet."""
    if opening_cash is not None:
        service.record_baseline(
            flow=HandoverFlow.RECEPTION,
            custodian_id=OUTGOING,
            manager_id="manager-1",
            opening_cash=opening_cash,
            items=OPENING_SHELVES,
        )
    return service.start(HandoverFlow.RECEPTION, incoming_id=incoming, caller=CALLER).handover_id


def _settle(service: HandoverService, handover_id: str, counted: int) -> None:
    assert service.count(handover_id, CASH, counted, caller=CALLER) is CountStatus.INCOMING
    assert service.count(handover_id, CASH, counted, caller=CALLER) in {CountStatus.MATCHED, CountStatus.SETTLED}


def _move(
    store: EventStore, projections: ProjectionEngine, event_type: str, amount: int, *, at: datetime
) -> EventRecord:
    """Record money stamped `at`: what the box did, whatever order it reaches the server in."""
    record = store.append(build_event(event_type, payload={"amount": amount}, client_at=at)).event
    projections.sync()
    return record


def _payloads(database: Database, event_type: str, handover_id: str | None = None) -> list[dict[str, Any]]:
    """Every stored payload of one type, in server order, optionally for one flow."""
    sql = "SELECT payload FROM events WHERE type = ?"
    parameters: tuple[Any, ...] = (event_type,)
    if handover_id is not None:
        sql += " AND handover_id = ?"
        parameters += (handover_id,)
    with database.read() as connection:
        rows = connection.exec_driver_sql(f"{sql} ORDER BY seq", parameters).mappings()
        return [json.loads(row["payload"]) for row in rows]


def _box(database: Database) -> int:
    """The reception box's expected figure, the number the next shift starts from."""
    with database.read() as connection:
        rows = projections_repo.cash_rows(connection)
    return next(int(row["expected"]) for row in rows if row["cash_box_id"] == "reception")


def test_a_withdrawal_that_happened_before_the_cutoff_moves_the_reference_not_the_carry(
    service: HandoverService, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    """The critic's sequence: the offline withdrawal arrives during the flow and the count still agrees."""
    handover_id = _start(service, opening_cash=OPENING_CASH)
    _move(store, projections, "cash.withdrawn", 100_000, at=MOMENT - timedelta(hours=1))

    _settle(service, handover_id, 400_000)

    assert _payloads(database, "handover.cash_reference_adjusted", handover_id) == [{"delta": -100_000}]
    settled = [row for row in _payloads(database, "handover.count_settled", handover_id) if row["scope"] == "cash"]
    assert settled == [
        {
            "scope": "cash",
            "item_id": None,
            "location_id": None,
            "counted": 400_000,
            "basis": "agreeing_blind",
            "carry": 0,
        }
    ]
    assert _payloads(database, "discrepancy.recorded", handover_id) == []
    assert _box(database) == 400_000


def test_several_consecutive_shifts_carry_only_the_money_that_happened_after_each_cutoff(
    service: HandoverService, database: Database, store: EventStore, clock: FixedClock, projections: ProjectionEngine
) -> None:
    """Receipts and withdrawals after a cutoff stay on top of the agreed figure, shift after shift."""
    first = _start(service, opening_cash=OPENING_CASH)
    # A patient pays 50k after the first flow's cutoff: the next shift's money, not this count's.
    _move(store, projections, "acc.cash_receipt", 50_000, at=MOMENT + timedelta(minutes=10))
    _settle(service, first, 500_000)
    assert _box(database) == 550_000

    clock.moment = MOMENT + timedelta(hours=8)
    second = _start(service, incoming="staff-in-2")
    # A withdrawal happens after the second flow's cutoff and arrives while it is being counted.
    _move(store, projections, "cash.withdrawn", 100_000, at=clock.moment + timedelta(minutes=5))
    _settle(service, second, 550_000)
    assert _box(database) == 450_000

    clock.moment = clock.moment + timedelta(hours=8)
    third = _start(service, incoming="staff-in-3")
    _settle(service, third, 450_000)
    assert _box(database) == 450_000
    carries = [row["carry"] for row in _payloads(database, "handover.count_settled", second) if row["scope"] == "cash"]
    assert carries == [-100_000]


def test_rebuild_equals_live_after_a_late_pre_cutoff_arrival(
    service: HandoverService, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    """The internal records are ordinary events, so replaying the log gives the same projections."""
    handover_id = _start(service, opening_cash=OPENING_CASH)
    _move(store, projections, "cash.withdrawn", 100_000, at=MOMENT - timedelta(hours=1))
    _settle(service, handover_id, 400_000)

    report = projections.rebuild()

    assert report.drift == ()
    assert report.applied is True
    assert _box(database) == 400_000


def test_a_withdrawal_during_an_open_flow_is_accepted_and_flagged_for_the_manager(
    service: HandoverService, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    """The cash really moved: the record is kept, the carry takes it off, and the manager is told."""
    handover_id = _start(service, opening_cash=OPENING_CASH)
    _move(store, projections, "cash.withdrawn", 100_000, at=MOMENT + timedelta(minutes=5))

    _settle(service, handover_id, 500_000)

    assert _box(database) == 400_000
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT detail FROM audit_log WHERE action = 'handover.cash_exception' ORDER BY id"
        ).mappings()
        details = [json.loads(row["detail"]) for row in rows]
    assert details == [{"flow": "reception", "amount": 0, "outflow": 100_000}]


def test_a_late_event_after_the_settle_is_kept_flagged_and_the_box_keeps_its_figure(
    service: HandoverService, database: Database, store: EventStore, clock: FixedClock, projections: ProjectionEngine
) -> None:
    """The count already saw the money leave, so the late record must not move the settled box again."""
    handover_id = _start(service, opening_cash=OPENING_CASH)
    _settle(service, handover_id, 400_000)
    assert _payloads(database, "discrepancy.recorded", handover_id) != []

    late = _move(store, projections, "cash.withdrawn", 100_000, at=MOMENT - timedelta(hours=1))
    assert _box(database) == 300_000

    notice = notice_late_cash(database, store, late, clock=clock)
    projections.sync()

    assert notice is not None
    stored = _payloads(database, "handover.late_cash_recorded")
    assert len(stored) == 1
    assert stored[0]["handover_id"] == handover_id
    assert stored[0]["amount"] == -100_000
    assert parse_iso(stored[0]["occurred_at"]) == MOMENT - timedelta(hours=1)
    with database.read() as connection:
        linked = connection.exec_driver_sql(
            "SELECT handover_id FROM events WHERE type = 'handover.late_cash_recorded'"
        ).scalar_one()
    assert linked == handover_id, "the notice belongs to the flow's own log, not only to its payload"
    assert _box(database) == 400_000
    report = projections.rebuild()
    assert report.drift == ()
    assert _box(database) == 400_000


def test_a_late_notice_is_written_once_for_one_event(
    service: HandoverService, database: Database, store: EventStore, clock: FixedClock, projections: ProjectionEngine
) -> None:
    """A retry, or a second writer, never cancels the same movement twice."""
    handover_id = _start(service, opening_cash=OPENING_CASH)
    _settle(service, handover_id, 500_000)
    late = _move(store, projections, "cash.withdrawn", 25_000, at=MOMENT - timedelta(hours=2))

    assert notice_late_cash(database, store, late, clock=clock) is not None
    assert notice_late_cash(database, store, late, clock=clock) is None
    assert len(_payloads(database, "handover.late_cash_recorded")) == 1


def test_only_a_receipt_or_a_withdrawal_moves_the_box() -> None:
    """The money sign comes from the event registry; a counting event moves no expected figure."""
    assert signed_cash("cash.withdrawn", {"amount": 5_000}, "shift-1") == -5_000
    assert signed_cash("cash.deposited", {"amount": 5_000}, "shift-1") == -5_000
    assert signed_cash("acc.cash_receipt", {"amount": 5_000}, "shift-1") == 5_000
    assert signed_cash("acc.cash_reversal", {"amount": 5_000, "reason": "voided"}, "shift-1") == -5_000
    assert signed_cash("handover.cash_counted", {"counted": 5_000}, "shift-1") == 0


def test_a_late_record_from_the_previous_flow_never_moves_the_next_flow_reference(
    service: HandoverService, database: Database, store: EventStore, clock: FixedClock, projections: ProjectionEngine
) -> None:
    """H-11: money an earlier flow already saw is not counted a second time by the flow after it.

    The stale withdrawal belongs to the first flow: that flow's count already read the empty box and
    its late-cash record put the box back. The second flow's reference must stay where the settlement
    left it, or an honest count would be charged a surplus against the outgoing person.
    """
    first = _start(service, opening_cash=OPENING_CASH)
    _settle(service, first, 400_000)

    clock.moment = MOMENT + timedelta(hours=8)
    second = _start(service, incoming="staff-in-2")
    late = _move(store, projections, "cash.withdrawn", 100_000, at=MOMENT - timedelta(hours=1))
    assert notice_late_cash(database, store, late, clock=clock) is not None
    projections.sync()
    assert _box(database) == 400_000

    _settle(service, second, 400_000)

    assert _payloads(database, "handover.cash_reference_adjusted", second) == []
    assert _payloads(database, "discrepancy.recorded", second) == []
    assert _box(database) == 400_000


def test_a_bank_deposit_that_happened_before_the_cutoff_is_no_false_discrepancy(
    service: HandoverService, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    """B2: the accountant carried 150k to the bank; the box counts 350k and the count still agrees."""
    handover_id = _start(service, opening_cash=OPENING_CASH)
    _move(store, projections, "cash.deposited", 150_000, at=MOMENT - timedelta(hours=1))

    _settle(service, handover_id, 350_000)

    assert _payloads(database, "handover.cash_reference_adjusted", handover_id) == [{"delta": -150_000}]
    assert _payloads(database, "discrepancy.recorded", handover_id) == []
    assert _box(database) == 350_000
    assert projections.rebuild().drift == ()
    assert _box(database) == 350_000


def test_a_bank_deposit_after_the_cutoff_stays_on_top_of_the_agreed_figure(
    service: HandoverService, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    """A deposit made after the count began is the next shift's money, whenever it reaches the server."""
    handover_id = _start(service, opening_cash=OPENING_CASH)
    _move(store, projections, "cash.deposited", 150_000, at=MOMENT + timedelta(minutes=5))

    _settle(service, handover_id, 500_000)

    carries = [row["carry"] for row in _payloads(database, "handover.count_settled", handover_id)]
    assert -150_000 in carries
    assert _box(database) == 350_000
    assert _payloads(database, "discrepancy.recorded", handover_id) == []
