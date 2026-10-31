"""The handover service end to end: one persisted flow, blind counts and the events (H-01..H-13).

Every test runs on a synthetic database with the reference catalog seeded from
`tools/seed_reference_data.py` and, where accounting matters, the synthetic accounting copy from
`tools/make_synthetic_acc_db.py`. Nothing here reads a production database.
"""

import json
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from clocks import FixedClock
from make_synthetic_acc_db import build
from seed_reference_data import seed

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
from shift.services.handover import Caller, HandoverService
from shift.services.projections import ProjectionEngine

DAY = date(2026, 10, 28)
MOMENT = datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN)  # inside the morning shift
OUTGOING = "staff-out"
INCOMING = "staff-in"
CALLER = Caller(actor_id=INCOMING, device_id="device-1")
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


def _service(
    database: Database,
    store: EventStore,
    catalog: CatalogService,
    clock: FixedClock,
    *,
    bridge: AccountingBridge | None = None,
    accounting_path: Path | None = None,
) -> HandoverService:
    return HandoverService(
        database,
        store,
        projections=ProjectionEngine(database, clock=clock),
        catalog=catalog,
        audit=AuditService(database, clock=clock),
        clock=clock,
        bridge=bridge,
        accounting_path=None if accounting_path is None else str(accounting_path),
    )


@pytest.fixture()
def service(seeded: Database, store: EventStore, catalog: CatalogService, clock: FixedClock) -> HandoverService:
    return _service(seeded, store, catalog, clock)


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


def _close_every_open_invoice(path: Path) -> None:
    """Accounting's own close route would do this; here it just clears the H-03 guard."""
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE invoices SET status = 'closed', closed_at = '2026-09-24 13:50:00' WHERE status = 'open'"
        )
        connection.commit()
    finally:
        connection.close()


def _stock(store: EventStore, item_id: str, counted: int) -> None:
    """A manager's weekly count, so the shelf has a real base figure to hand over."""
    payload = {"item_id": item_id, "location_id": SHELF, "counted": counted}
    store.append(build_event("stock.weekly_count", payload=payload, shift_id=None))


def _start(service: HandoverService, flow: HandoverFlow = HandoverFlow.RECEPTION, *, opening_cash: int | None):
    return service.start(
        flow,
        outgoing_id=OUTGOING,
        incoming_id=INCOMING,
        caller=CALLER,
        opening_cash=opening_cash,
    )


def _settle_cash(service: HandoverService, handover_id: str, counted: int) -> None:
    assert service.count(handover_id, CASH, counted, caller=CALLER) is CountStatus.INCOMING
    assert service.count(handover_id, CASH, counted, caller=CALLER) is CountStatus.MATCHED


def test_a_reception_handover_counts_cash_every_item_the_lists_and_both_signatures(
    service: HandoverService, database: Database, store: EventStore
) -> None:
    _stock(store, PARA, 10)
    _stock(store, DICLO, 4)
    state = _start(service, opening_cash=1_000_000)
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

    service.sign(handover_id, Signer.INCOMING, caller=CALLER)
    state = service.sign(handover_id, Signer.OUTGOING, caller=CALLER)

    assert (state.closed, state.step, state.signatures) == (True, None, frozenset(Signer))
    assert _payloads(database, handover_id, "handover.started") == [{"opening_cash": 1_000_000}]
    assert _payloads(database, handover_id, "handover.count_settled") == [
        {"scope": "cash", "item_id": None, "location_id": None, "counted": 1_000_000, "basis": "agreeing_blind"}
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
    service: HandoverService, database: Database, store: EventStore
) -> None:
    _stock(store, PARA, 10)
    handover_id = _start(service, opening_cash=0).handover_id
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
    service: HandoverService, database: Database, store: EventStore
) -> None:
    handover_id = _start(service, opening_cash=1_000_000).handover_id
    assert service.report_early_leave(handover_id, caller=CALLER).early_left is True
    with pytest.raises(HandoverError, match="H-08"):
        service.advance(handover_id, caller=CALLER)

    state = service.confirm_emergency_start(handover_id, "manager-1", caller=CALLER)
    assert state.emergency_by == "manager-1"

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

    state = service.sign(handover_id, Signer.INCOMING, caller=CALLER)
    assert (state.closed, state.signatures) == (True, frozenset({Signer.INCOMING}))


def test_a_flow_does_not_start_while_accounting_still_shows_an_open_invoice(
    tmp_path: Path, seeded: Database, store: EventStore, catalog: CatalogService, clock: FixedClock
) -> None:
    scenario = build(tmp_path / "accounting.db")
    bridge = AccountingBridge(seeded, store, clock=clock)
    service = _service(seeded, store, catalog, clock, bridge=bridge, accounting_path=scenario.path)

    with pytest.raises(HandoverError, match="H-03"):
        _start(service, opening_cash=1_000_000)


def test_both_flows_of_one_shift_run_side_by_side(service: HandoverService) -> None:
    reception = _start(service, opening_cash=0)
    nursing = _start(service, HandoverFlow.NURSING, opening_cash=None)

    assert reception.handover_id != nursing.handover_id
    assert service.state(nursing.handover_id).step is HandoverStep.CPR_ROOM
    with pytest.raises(HandoverError, match="H-12"):
        _start(service, opening_cash=None)

    service.count(nursing.handover_id, _item(SALINE, "cpr_room"), 0, caller=CALLER)
    assert service.state(reception.handover_id).step is HandoverStep.CASH
    assert service.state(reception.handover_id).item_statuses == {}


def test_a_cash_receipt_stamped_after_the_flow_cutoff_is_not_in_its_reference(
    tmp_path: Path, seeded: Database, store: EventStore, catalog: CatalogService, clock: FixedClock
) -> None:
    """H-11: the reference is frozen at the flow's own start, so a later receipt cannot change it."""
    scenario = build(tmp_path / "accounting.db")
    _close_every_open_invoice(scenario.path)
    clock.moment = parse_time("2026-09-24 14:05:00")  # before the 14:20 invoice is closed
    bridge = AccountingBridge(seeded, store, clock=clock)
    service = _service(seeded, store, catalog, clock, bridge=bridge, accounting_path=scenario.path)
    handover_id = _start(service, opening_cash=None).handover_id

    clock.moment = parse_time("2026-09-24 15:00:00")  # the 999 000 receipt lands after the cutoff
    assert bridge.poll(scenario.path).events == 1

    _settle_cash(service, handover_id, 215_000)  # what the box held at the cutoff

    assert _payloads(seeded, handover_id, "handover.count_settled") == [
        {"scope": "cash", "item_id": None, "location_id": None, "counted": 215_000, "basis": "agreeing_blind"}
    ]
    assert _payloads(seeded, handover_id, "discrepancy.recorded") == []


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


def test_the_steps_are_left_only_when_their_work_is_settled(service: HandoverService, store: EventStore) -> None:
    _stock(store, PARA, 10)
    _stock(store, DICLO, 4)
    handover_id = _start(service, opening_cash=0).handover_id
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
