"""The handover service end to end: one persisted flow, its blind counts and its events (H-01..H-13).

Every test runs on a synthetic database with the reference catalog seeded from
`tools/seed_reference_data.py` and with a synthetic accounting copy from
`tools/make_synthetic_acc_db.py`, because a handover refuses to start without a readable one (H-03).
Each flow is opened the way the clinic opens one: the manager's baseline first, so the box and the
shelves have a reference and the outgoing person is derived rather than typed (SHF-7.1).
Nothing here reads a production database.
"""

import json
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from clocks import FixedClock
from make_synthetic_acc_db import build
from synthetic_accounting import close_open_invoices, empty_accounting
from synthetic_catalog import seed

from event_fixtures import build_event
from shift.adapters.accounting.views import parse_time
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN
from shift.domain.handover_counts import CountStatus
from shift.domain.handover_formulas import HandoverError
from shift.domain.handover_fsm import HandoverFlow, HandoverStep, Signer
from shift.domain.handover_payloads import CountTarget
from shift.services.accounting_bridge import AccountingBridge
from shift.services.audit import AuditService
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.handover import NO_ACCOUNTING, Caller, HandoverService
from shift.services.projections import ProjectionEngine

DAY = date(2026, 10, 28)
MOMENT = datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN)  # inside the morning shift
OUTGOING = "staff-out"
INCOMING = "staff-in"
CALLER = Caller(actor_id=INCOMING, device_id="device-1")
# S03 §3: the outgoing person signs from their own phone, so the service sees their own caller.
OUTGOING_CALLER = Caller(actor_id=OUTGOING, device_id="device-2")
SECOND_CALLER = Caller(actor_id="staff-in-2", device_id="device-3")
CASH = CountTarget(scope="cash")
SHELF = "shelf_reception"
PARA = "para_500_amp"
DICLO = "diclofenac_75_amp"
SALINE = "saline_500_bag"


def _item(item_id: str, location: str = SHELF) -> CountTarget:
    return CountTarget(scope="item", item_id=item_id, location_id=location)


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(MOMENT)


@pytest.fixture()
def seeded(database: Database) -> Database:
    """The synthetic reference catalog, so item steps know their item lists."""
    seed(database)
    return database


@pytest.fixture()
def store(seeded: Database, clock: FixedClock) -> EventStore:
    return EventStore(seeded, clock=clock)


@pytest.fixture()
def catalog(seeded: Database, clock: FixedClock) -> CatalogService:
    return CatalogService(seeded, clock=clock)


@pytest.fixture()
def projections(seeded: Database, clock: FixedClock) -> ProjectionEngine:
    """The engine the service syncs through, so a test can append a cash movement and catch it up."""
    return ProjectionEngine(seeded, clock=clock)


def _service(
    database: Database,
    store: EventStore,
    catalog: CatalogService,
    clock: FixedClock,
    projections: ProjectionEngine,
    *,
    bridge: AccountingBridge | None,
    accounting_path: Path | None,
) -> HandoverService:
    return HandoverService(
        database,
        store,
        projections=projections,
        catalog=catalog,
        audit=AuditService(database, clock=clock),
        clock=clock,
        bridge=bridge,
        accounting_path=None if accounting_path is None else str(accounting_path),
    )


@pytest.fixture()
def accounting(tmp_path: Path) -> Path:
    """The readable accounting copy every start reads (H-03)."""
    return empty_accounting(tmp_path / "accounting.db")


@pytest.fixture()
def service(
    seeded: Database,
    store: EventStore,
    catalog: CatalogService,
    clock: FixedClock,
    projections: ProjectionEngine,
    accounting: Path,
) -> HandoverService:
    bridge = AccountingBridge(seeded, store, clock=clock)
    return _service(seeded, store, catalog, clock, projections, bridge=bridge, accounting_path=accounting)


def _types(database: Database, handover_id: str) -> list[str]:
    with database.read() as connection:
        rows = connection.exec_driver_sql("SELECT type FROM events WHERE handover_id = ? ORDER BY seq", (handover_id,))
        return [str(row[0]) for row in rows]


def _payloads(database: Database, handover_id: str, event_type: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT payload FROM events WHERE handover_id = ? AND type = ? ORDER BY seq",
            (handover_id, event_type),
        ).mappings()
        return [json.loads(row["payload"]) for row in rows]


def _baseline(
    service: HandoverService,
    flow: HandoverFlow = HandoverFlow.RECEPTION,
    *,
    opening_cash: int | None,
    stock: Mapping[str, int] | None = None,
    custodian: str = OUTGOING,
) -> None:
    """The manager's opening count: the box figure and the shelf quantities this test cares about."""
    location = "cpr_room" if flow is HandoverFlow.NURSING else SHELF
    items = [
        {"item_id": item_id, "location_id": location, "quantity": quantity}
        for item_id, quantity in (stock or {}).items()
    ]
    if not items:
        items = [{"item_id": SALINE if flow is HandoverFlow.NURSING else PARA, "location_id": location, "quantity": 0}]
    service.record_baseline(
        flow=flow,
        custodian_id=custodian,
        manager_id="manager-1",
        opening_cash=opening_cash,
        items=items,
    )


def _start(
    service: HandoverService,
    flow: HandoverFlow = HandoverFlow.RECEPTION,
    *,
    opening_cash: int | None,
    stock: Mapping[str, int] | None = None,
    baseline: bool = True,
    incoming: str = INCOMING,
):
    """The manager's baseline (once per flow kind), then the flow that derives its outgoing person."""
    if baseline:
        _baseline(service, flow, opening_cash=opening_cash, stock=stock)
    return service.start(flow, incoming_id=incoming, caller=CALLER)


def _settle_cash(service: HandoverService, handover_id: str, counted: int) -> None:
    assert service.count(handover_id, CASH, counted, caller=CALLER) is CountStatus.INCOMING
    assert service.count(handover_id, CASH, counted, caller=CALLER) is CountStatus.MATCHED


def _close_reception(
    service: HandoverService, handover_id: str, *, incoming: Caller = CALLER, outgoing: Caller
) -> None:
    """Walk a reception flow whose cash already settled to its closing signature."""
    assert service.advance(handover_id, caller=incoming).step is HandoverStep.SHELF
    for item in (PARA, DICLO):
        assert service.count(handover_id, _item(item), 0, caller=incoming) is CountStatus.MATCHED
    for step in (HandoverStep.CHECKLISTS, HandoverStep.PARCELS, HandoverStep.DUAL_SIGN):
        assert service.advance(handover_id, caller=incoming).step is step
    service.sign(handover_id, caller=incoming)
    assert service.sign(handover_id, caller=outgoing).closed is True


def _movement(store: EventStore, projections: ProjectionEngine, event_type: str, amount: int, *, at: datetime) -> None:
    """A cash receipt or withdrawal stamped `at`, the way accounting or the shift screen records one."""
    store.append(build_event(event_type, payload={"amount": amount}, client_at=at))
    projections.sync()


def _exceptions(database: Database) -> list[dict[str, Any]]:
    """The handover exceptions the manager is told about; the only place a staff receipt may rest."""
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT subject_id, detail FROM audit_log WHERE action = 'handover.cash_exception' ORDER BY id"
        ).mappings()
        return [{"subject_id": row["subject_id"], "detail": json.loads(row["detail"])} for row in rows]


def test_a_reception_handover_counts_cash_every_item_the_lists_and_both_signatures(
    service: HandoverService, database: Database
) -> None:
    state = _start(service, opening_cash=1_000_000, stock={PARA: 10, DICLO: 4})
    handover_id = state.handover_id
    assert (state.step, state.cash_status, state.item_statuses) == (HandoverStep.CASH, CountStatus.FIRST, {})

    _settle_cash(service, handover_id, 1_000_000)
    state = service.advance(handover_id, caller=CALLER)
    assert state.step is HandoverStep.SHELF
    assert state.item_statuses == {PARA: CountStatus.FIRST, DICLO: CountStatus.FIRST}

    assert service.count(handover_id, _item(PARA), 10, caller=CALLER) is CountStatus.MATCHED
    assert service.count(handover_id, _item(DICLO), 4, caller=CALLER) is CountStatus.MATCHED
    state = service.advance(handover_id, caller=CALLER)
    assert state.step is HandoverStep.CHECKLISTS

    service.answer_checklist(handover_id, "store_lock_ok", ok=True, caller=CALLER)
    state = service.advance(handover_id, caller=CALLER)
    assert state.step is HandoverStep.PARCELS
    service.hand_parcel(handover_id, "parcel-1", 2, caller=CALLER)
    state = service.advance(handover_id, caller=CALLER)
    assert state.step is HandoverStep.DUAL_SIGN

    service.sign(handover_id, caller=CALLER)
    state = service.sign(handover_id, caller=OUTGOING_CALLER)

    assert (state.closed, state.step, state.signatures) == (True, None, frozenset(Signer))
    assert _payloads(database, handover_id, "handover.started") == [{"opening_cash": None}]
    assert _payloads(database, handover_id, "handover.count_settled") == [
        {
            "scope": "cash",
            "item_id": None,
            "location_id": None,
            "counted": 1_000_000,
            "basis": "agreeing_blind",
            "carry": 0,
        },
        {
            "scope": "item",
            "item_id": PARA,
            "location_id": SHELF,
            "counted": 10,
            "basis": "matched",
            "carry": None,
        },
        {
            "scope": "item",
            "item_id": DICLO,
            "location_id": SHELF,
            "counted": 4,
            "basis": "matched",
            "carry": None,
        },
    ]
    assert _payloads(database, handover_id, "discrepancy.recorded") == []
    assert [step for step in _payloads(database, handover_id, "handover.step_advanced")] == [
        {"step": "shelf"},
        {"step": "checklists"},
        {"step": "parcels"},
        {"step": "dual_sign"},
    ]
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 1_000_000, "counted": 1_000_000, "variance": 0}
        ]
        assert projections_repo.stock_rows(connection) == [
            {"item_id": DICLO, "location_id": SHELF, "qty": 4},
            {"item_id": PARA, "location_id": SHELF, "qty": 10},
        ]
        signed_at = connection.exec_driver_sql(
            "SELECT signed_at FROM handover WHERE handover_id = ?", (handover_id,)
        ).scalar_one()
    assert signed_at is not None


def test_a_mismatch_walks_to_the_joint_count_and_records_one_named_discrepancy(
    service: HandoverService, database: Database
) -> None:
    handover_id = _start(service, opening_cash=0, stock={PARA: 10}).handover_id
    _settle_cash(service, handover_id, 0)
    service.advance(handover_id, caller=CALLER)

    statuses = [
        service.count(handover_id, _item(PARA), 8, caller=CALLER),
        service.count(handover_id, _item(PARA), 9, caller=CALLER),
        service.count(handover_id, _item(PARA), 7, caller=CALLER),
        service.joint(handover_id, _item(PARA), 9, caller=CALLER),
    ]

    assert statuses == [CountStatus.RECOUNT, CountStatus.OUTGOING, CountStatus.JOINT, CountStatus.SETTLED]
    assert all(not any(character.isdigit() for character in status.value) for status in statuses)
    # Only the settlement writes the shelf: the three blind counts and the joint figure left it alone.
    assert [
        payload for payload in _payloads(database, handover_id, "handover.count_settled") if payload["scope"] == "item"
    ] == [
        {
            "scope": "item",
            "item_id": PARA,
            "location_id": SHELF,
            "counted": 9,
            "basis": "joint",
            "carry": None,
        }
    ]
    assert _payloads(database, handover_id, "discrepancy.recorded") == [
        {
            "scope": "item",
            "item_id": PARA,
            "location_id": SHELF,
            "difference": 1,
            "culprit_id": OUTGOING,
        }
    ]
    kinds = _types(database, handover_id)
    assert kinds.count("discrepancy.recorded") == 1
    # the recount is requested when the chain needs the other person: the outgoing blind count and
    # the joint count, not the incoming person's own recount.
    assert kinds.count("handover.recount_requested") == 2
    with database.read() as connection:
        assert projections_repo.stock_rows(connection)[-1] == {"item_id": PARA, "location_id": SHELF, "qty": 9}


def test_the_nursing_flow_has_no_cash_step(service: HandoverService, database: Database) -> None:
    state = _start(service, HandoverFlow.NURSING, opening_cash=None)

    assert (state.step, state.cash_status) == (HandoverStep.CPR_ROOM, None)
    assert state.item_statuses == {SALINE: CountStatus.FIRST, "oxygen_mask": CountStatus.FIRST}
    with pytest.raises(HandoverError, match="cash step"):
        service.count(state.handover_id, CASH, 0, caller=CALLER)
    assert _payloads(database, state.handover_id, "handover.started") == [{"opening_cash": None}]

    assert service.count(state.handover_id, _item(SALINE, "cpr_room"), 0, caller=CALLER) is CountStatus.MATCHED


def test_an_emergency_start_names_no_culprit_and_needs_one_signature(
    service: HandoverService, database: Database, clock: FixedClock
) -> None:
    handover_id = _start(service, opening_cash=1_000_000).handover_id
    assert service.report_early_leave(handover_id, caller=CALLER).early_left is True
    with pytest.raises(HandoverError, match="H-08"):
        service.advance(handover_id, caller=CALLER)

    state = service.confirm_emergency_start(handover_id, "manager-1", caller=CALLER)
    assert (state.emergency_by, state.emergency_at) == ("manager-1", clock.moment)

    assert service.count(handover_id, CASH, 900_000, caller=CALLER) is CountStatus.RECOUNT
    assert service.count(handover_id, CASH, 900_000, caller=CALLER) is CountStatus.SETTLED
    assert _payloads(database, handover_id, "discrepancy.recorded") == [
        {"scope": "cash", "item_id": None, "location_id": None, "difference": 100_000, "culprit_id": None}
    ]
    service.advance(handover_id, caller=CALLER)
    for item in (PARA, DICLO):
        assert service.count(handover_id, _item(item), 0, caller=CALLER) is CountStatus.MATCHED
    for step in (HandoverStep.CHECKLISTS, HandoverStep.PARCELS, HandoverStep.DUAL_SIGN):
        assert service.advance(handover_id, caller=CALLER).step is step

    state = service.sign(handover_id, caller=CALLER)
    assert (state.closed, state.signatures) == (True, frozenset({Signer.INCOMING}))


def test_a_flow_does_not_start_while_accounting_still_shows_an_open_invoice(
    tmp_path: Path,
    seeded: Database,
    store: EventStore,
    catalog: CatalogService,
    clock: FixedClock,
    projections: ProjectionEngine,
) -> None:
    scenario = build(tmp_path / "accounting.db")
    bridge = AccountingBridge(seeded, store, clock=clock)
    service = _service(seeded, store, catalog, clock, projections, bridge=bridge, accounting_path=scenario.path)

    with pytest.raises(HandoverError, match="H-03"):
        _start(service, opening_cash=1_000_000)


def test_a_flow_does_not_start_without_an_accounting_copy(
    seeded: Database, store: EventStore, catalog: CatalogService, clock: FixedClock, projections: ProjectionEngine
) -> None:
    """H-03: without the copy neither the open-invoice guard nor a reference can be computed."""
    service = _service(seeded, store, catalog, clock, projections, bridge=None, accounting_path=None)

    with pytest.raises(HandoverError) as refusal:
        _start(service, opening_cash=1_000_000)

    assert refusal.value.args[0] == NO_ACCOUNTING
    assert not any(character.isdigit() for character in str(refusal.value))
    with seeded.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM events WHERE handover_id IS NOT NULL").scalar_one() == 0
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM handover").scalar_one() == 0


def test_a_flow_does_not_start_when_the_accounting_copy_is_unreachable(
    tmp_path: Path,
    seeded: Database,
    store: EventStore,
    catalog: CatalogService,
    clock: FixedClock,
    projections: ProjectionEngine,
) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database, it is a text file")
    bridge = AccountingBridge(seeded, store, clock=clock)
    service = _service(seeded, store, catalog, clock, projections, bridge=bridge, accounting_path=broken)

    with pytest.raises(HandoverError) as refusal:
        _start(service, opening_cash=1_000_000)

    assert refusal.value.args[0] == NO_ACCOUNTING
    with seeded.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM handover").scalar_one() == 0


def test_both_flows_of_one_shift_run_side_by_side(service: HandoverService) -> None:
    reception = _start(service, opening_cash=0)
    nursing = _start(service, HandoverFlow.NURSING, opening_cash=None)

    assert reception.handover_id != nursing.handover_id
    assert service.state(nursing.handover_id).step is HandoverStep.CPR_ROOM
    with pytest.raises(HandoverError, match="H-12"):
        service.start(HandoverFlow.RECEPTION, incoming_id=INCOMING, caller=CALLER)

    service.count(nursing.handover_id, _item(SALINE, "cpr_room"), 0, caller=CALLER)
    assert service.state(reception.handover_id).step is HandoverStep.CASH
    assert service.state(reception.handover_id).item_statuses == {}


def test_a_cash_receipt_stamped_inside_a_flow_is_reported_to_the_manager_and_carried_on_top(
    tmp_path: Path,
    seeded: Database,
    store: EventStore,
    catalog: CatalogService,
    clock: FixedClock,
    projections: ProjectionEngine,
) -> None:
    """H-11: the reference is frozen at the flow's own start, so a later receipt cannot change it.

    The owner's rule says the incoming person takes no money during a handover, so the 999 000 a
    paper patient paid at 14:20 — after the 14:05 cutoff and before the flow closed — is the next
    shift's cash. It stays on top of the settled figure, and the manager hears about it in the audit
    trail alone (R-02): no response, header, public log or client event carries the number.
    """
    scenario = build(tmp_path / "accounting.db")
    close_open_invoices(scenario.path, "2026-09-24 13:50:00")
    clock.moment = parse_time("2026-09-24 14:05:00")  # before the 14:20 invoice is closed
    bridge = AccountingBridge(seeded, store, clock=clock)
    service = _service(seeded, store, catalog, clock, projections, bridge=bridge, accounting_path=scenario.path)
    handover_id = _start(service, opening_cash=0).handover_id  # the box held nothing at install

    clock.moment = parse_time("2026-09-24 15:00:00")  # the 999 000 receipt lands after the cutoff
    assert bridge.poll(scenario.path).events == 1

    _settle_cash(service, handover_id, 215_000)  # what the box held at the cutoff

    assert _payloads(seeded, handover_id, "handover.count_settled") == [
        {
            "scope": "cash",
            "item_id": None,
            "location_id": None,
            "counted": 215_000,
            "basis": "agreeing_blind",
            "carry": 999_000,
        }
    ]
    assert _payloads(seeded, handover_id, "discrepancy.recorded") == []
    with seeded.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 1_214_000, "counted": 215_000, "variance": -999_000}
        ]
    assert _exceptions(seeded) == [{"subject_id": handover_id, "detail": {"amount": 999_000, "flow": "reception"}}]
    assert "999000" not in str(service.state(handover_id))


def test_receipts_stamped_between_flows_leave_no_discrepancy_on_any_shift(
    service: HandoverService,
    database: Database,
    store: EventStore,
    projections: ProjectionEngine,
    clock: FixedClock,
) -> None:
    """The owner's normal sequence: a paper patient pays after a handover ends, before the next starts.

    Each receipt lands after its flow's cutoff but while no flow is running, so the next flow's
    frozen reference already contains it and no shift is charged with a gap it never had (H-11).
    """
    clock.moment = datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN)
    first = _start(service, opening_cash=1_000_000).handover_id
    _settle_cash(service, first, 1_000_000)
    _close_reception(service, first, outgoing=OUTGOING_CALLER)

    clock.moment = datetime(2026, 10, 28, 9, 45, tzinfo=TEHRAN)  # after the handover, before any flow
    _movement(store, projections, "acc.cash_receipt", 50_000, at=clock.moment)

    clock.moment = datetime(2026, 10, 28, 14, 0, tzinfo=TEHRAN)  # the evening shift's own handover
    second = _start(service, opening_cash=None, baseline=False, incoming="staff-in-2").handover_id
    _settle_cash(service, second, 1_050_000)
    # The second flow's outgoing person is the first flow's incoming person (custody, SHF-7.1).
    _close_reception(service, second, incoming=SECOND_CALLER, outgoing=CALLER)
    clock.moment = datetime(2026, 10, 28, 14, 20, tzinfo=TEHRAN)
    _movement(store, projections, "acc.cash_receipt", 30_000, at=clock.moment)

    clock.moment = datetime(2026, 10, 28, 20, 0, tzinfo=TEHRAN)  # the night shift
    third = _start(service, opening_cash=None, baseline=False, incoming=INCOMING).handover_id
    _settle_cash(service, third, 1_080_000)
    _close_reception(service, third, outgoing=SECOND_CALLER)

    for handover_id in (first, second, third):
        assert _payloads(database, handover_id, "discrepancy.recorded") == []
        cash = [
            payload
            for payload in _payloads(database, handover_id, "handover.count_settled")
            if payload["scope"] == "cash"
        ]
        assert [payload["carry"] for payload in cash] == [0]
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 1_080_000, "counted": 1_080_000, "variance": 0}
        ]
    assert _exceptions(database) == []


def test_a_withdrawal_during_a_flow_is_carried_negatively_and_flagged_like_a_receipt(
    service: HandoverService,
    database: Database,
    store: EventStore,
    projections: ProjectionEngine,
    clock: FixedClock,
) -> None:
    """H-11: the carry is symmetric, and the owner's no-money-during-handover rule flags both signs.

    The cash really left the box, so the withdrawal is accepted and stays on top of the agreed figure
    as a negative carry; the manager still has to see that money moved during a handover, exactly as
    for a receipt that landed inside the window.
    """
    clock.moment = datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN)
    handover_id = _start(service, opening_cash=1_000_000).handover_id
    assert service.count(handover_id, CASH, 1_000_000, caller=CALLER) is CountStatus.INCOMING

    clock.moment = datetime(2026, 10, 28, 9, 31, tzinfo=TEHRAN)
    _movement(store, projections, "cash.withdrawn", 20_000, at=clock.moment)

    assert service.count(handover_id, CASH, 1_000_000, caller=CALLER) is CountStatus.MATCHED

    assert _payloads(database, handover_id, "handover.count_settled")[0]["carry"] == -20_000
    assert _payloads(database, handover_id, "discrepancy.recorded") == []
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 980_000, "counted": 1_000_000, "variance": 20_000}
        ]
    assert _exceptions(database) == [
        {"subject_id": handover_id, "detail": {"flow": "reception", "amount": 0, "outflow": 20_000}}
    ]


def test_the_flow_log_refuses_a_skipped_step(service: HandoverService, database: Database, store: EventStore) -> None:
    state = _start(service, opening_cash=0)
    forged = build_event(
        "handover.step_advanced",
        payload={"step": "checklists"},
        shift_id=state.shift_id,
        handover_id=state.handover_id,
    )
    store.append(forged)

    with pytest.raises(HandoverError, match="skips or repeats"):
        service.state(state.handover_id)


def test_the_flow_log_refuses_a_step_past_the_last_one(service: HandoverService, store: EventStore) -> None:
    """A forged advance at the dual-sign step is a broken log, never an IndexError (S03 §3, ADR-010)."""
    state = _start(service, opening_cash=0)
    handover_id = state.handover_id
    _settle_cash(service, handover_id, 0)
    service.advance(handover_id, caller=CALLER)
    for item in (PARA, DICLO):
        service.count(handover_id, _item(item), 0, caller=CALLER)
    for step in (HandoverStep.CHECKLISTS, HandoverStep.PARCELS, HandoverStep.DUAL_SIGN):
        assert service.advance(handover_id, caller=CALLER).step is step
    store.append(
        build_event(
            "handover.step_advanced",
            payload={"step": "dual_sign"},
            shift_id=state.shift_id,
            handover_id=handover_id,
        )
    )

    with pytest.raises(HandoverError, match="skips or repeats"):
        service.state(handover_id)


def test_the_steps_are_left_only_when_their_work_is_settled(service: HandoverService) -> None:
    handover_id = _start(service, opening_cash=0, stock={PARA: 10, DICLO: 4}).handover_id
    with pytest.raises(HandoverError, match="counted and settled"):
        service.advance(handover_id, caller=CALLER)

    service.count(handover_id, CASH, 1, caller=CALLER)  # one mismatching blind count settles nothing
    with pytest.raises(HandoverError, match="counted and settled"):
        service.advance(handover_id, caller=CALLER)

    assert service.count(handover_id, CASH, 1, caller=CALLER) is CountStatus.SETTLED
    service.advance(handover_id, caller=CALLER)
    service.count(handover_id, _item(PARA), 10, caller=CALLER)
    with pytest.raises(HandoverError, match="not counted and settled"):
        service.advance(handover_id, caller=CALLER)

    service.count(handover_id, _item(DICLO), 4, caller=CALLER)
    assert service.advance(handover_id, caller=CALLER).step is HandoverStep.CHECKLISTS


def test_a_count_is_refused_on_a_step_that_does_not_own_its_target(service: HandoverService) -> None:
    state = _start(service, opening_cash=0)

    with pytest.raises(HandoverError, match="current step"):
        service.count(state.handover_id, _item(PARA), 1, caller=CALLER)
    with pytest.raises(HandoverError, match="no joint count"):
        service.joint(state.handover_id, CASH, 0, caller=CALLER)


def test_the_reference_map_is_audited_and_never_returned(service: HandoverService, database: Database) -> None:
    state = _start(service, opening_cash=1_000_000)

    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT action, subject_id, detail FROM audit_log WHERE action LIKE 'handover.reference.%'"
        ).mappings()
        entries = [dict(row) for row in rows]

    assert [entry["subject_id"] for entry in entries] == [state.handover_id]
    detail: Mapping[str, Any] = json.loads(entries[0]["detail"])
    assert detail["cash"] == 1_000_000
    assert {item["item_id"] for item in detail["items"]} == {PARA, DICLO}
    assert "1000000" not in str(state)
