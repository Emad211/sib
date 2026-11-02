"""The manager's initial baseline: the opening count the app starts from (SHF-7.1).

A baseline is not a staff handover: one event seeds the cash box, sets every shelf it names and records
who holds them, and nothing is recorded against anyone by it. These tests run on a synthetic database
with the reference catalog seeded and a synthetic accounting copy, because a flow still refuses to
start without a readable one (H-03). No production database is touched.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from clocks import FixedClock
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed
from turns import count_on_turn

from shift.adapters.db import events_repo, handover_repo, projections_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN
from shift.domain.handover_formulas import HandoverError
from shift.domain.handover_fsm import HandoverFlow, HandoverStep
from shift.domain.handover_payloads import CountTarget
from shift.services.accounting_bridge import AccountingBridge
from shift.services.audit import AuditService
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.handover import NO_BASELINE, Caller, HandoverService
from shift.services.projections import ProjectionEngine

MOMENT = datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN)
CUSTODIAN = "staff-out"
MANAGER = "manager-1"
INCOMING = "staff-in"
CALLER = Caller(actor_id=INCOMING, device_id="device-1")
# S03 §3: the outgoing person signs from their own phone, so the service sees their own caller.
OUTGOING_CALLER = Caller(actor_id=CUSTODIAN, device_id="device-2")
SHELF = "shelf_reception"
PARA = "para_500_amp"
DICLO = "diclofenac_75_amp"
CASH = CountTarget(scope="cash")
ITEMS = [
    {"item_id": PARA, "location_id": SHELF, "quantity": 10},
    {"item_id": DICLO, "location_id": SHELF, "quantity": 4},
]


def _item(item_id: str) -> CountTarget:
    return CountTarget(scope="item", item_id=item_id, location_id=SHELF)


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(MOMENT)


@pytest.fixture()
def seeded(database: Database) -> Database:
    seed(database)
    return database


@pytest.fixture()
def service(seeded: Database, clock: FixedClock, tmp_path: Path) -> HandoverService:
    store = EventStore(seeded, clock=clock)
    return HandoverService(
        seeded,
        store,
        projections=ProjectionEngine(seeded, clock=clock),
        catalog=CatalogService(seeded, clock=clock),
        audit=AuditService(seeded, clock=clock),
        clock=clock,
        bridge=AccountingBridge(seeded, store, clock=clock),
        accounting_path=str(empty_accounting(tmp_path / "accounting.db")),
    )


def _baseline(
    service: HandoverService, *, opening_cash: int | None = 1_000_000, items: list[dict[str, Any]] = ITEMS
) -> Any:
    return service.record_baseline(
        flow=HandoverFlow.RECEPTION,
        custodian_id=CUSTODIAN,
        manager_id=MANAGER,
        opening_cash=opening_cash,
        items=items,
    )


def _baseline_events(database: Database) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT payload, actor_id FROM events WHERE type = 'handover.baseline_recorded' ORDER BY seq"
        ).mappings()
        return [{"payload": json.loads(row["payload"]), "actor_id": row["actor_id"]} for row in rows]


def test_a_baseline_seeds_the_box_the_shelves_and_the_custody(service: HandoverService, database: Database) -> None:
    """One manager's act gives the first blind count a reference and names who holds the box now."""
    record = _baseline(service)

    assert (record.flow, record.custodian_id, record.manager_id, record.opening_cash) == (
        HandoverFlow.RECEPTION,
        CUSTODIAN,
        MANAGER,
        1_000_000,
    )
    assert record.shelves == 2
    assert _baseline_events(database) == [
        {
            "payload": {
                "flow": "reception",
                "custodian_id": CUSTODIAN,
                "manager_id": MANAGER,
                "opening_cash": 1_000_000,
                "items": ITEMS,
            },
            "actor_id": MANAGER,
        }
    ]
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 1_000_000, "counted": 1_000_000, "variance": 0}
        ]
        assert projections_repo.stock_rows(connection) == [
            {"item_id": DICLO, "location_id": SHELF, "qty": 4},
            {"item_id": PARA, "location_id": SHELF, "qty": 10},
        ]
        assert handover_repo.custodian(connection, HandoverFlow.RECEPTION) == CUSTODIAN
        # Nothing is recorded against anyone at baseline time: no flow, no discrepancy, no reference.
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM handover").scalar_one()) == 0
        assert (
            int(connection.exec_driver_sql("SELECT COUNT(*) FROM events WHERE handover_id IS NOT NULL").scalar_one())
            == 0
        )
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM audit_log").scalar_one()) == 0


def test_a_second_baseline_is_refused_before_anything_is_written(service: HandoverService, database: Database) -> None:
    _baseline(service)

    with pytest.raises(HandoverError, match="already recorded its baseline"):
        _baseline(service, opening_cash=2_000_000, items=[{"item_id": PARA, "location_id": SHELF, "quantity": 1}])

    assert len(_baseline_events(database)) == 1
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 1_000_000, "counted": 1_000_000, "variance": 0}
        ]
        assert projections_repo.stock_rows(connection)[-1] == {"item_id": PARA, "location_id": SHELF, "qty": 10}
        assert handover_repo.custodian(connection, HandoverFlow.RECEPTION) == CUSTODIAN


def test_a_flow_without_a_baseline_is_refused_with_the_managers_message(
    service: HandoverService, database: Database
) -> None:
    """No baseline means nothing to count against, so staff are sent to the manager (R-02: no number)."""
    assert service.custodian(HandoverFlow.RECEPTION) is None

    with pytest.raises(HandoverError) as refusal:
        service.start(HandoverFlow.RECEPTION, incoming_id=INCOMING, caller=CALLER)

    assert refusal.value.args[0] == NO_BASELINE
    assert "مدیر" in NO_BASELINE
    assert not any(character.isdigit() for character in str(refusal.value))
    with database.read() as connection:
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM handover").scalar_one()) == 0
        assert events_repo.count_events(connection) == 0


def test_the_first_flows_outgoing_person_is_the_custodian_and_a_close_hands_custody_on(
    service: HandoverService, database: Database
) -> None:
    _baseline(service)

    state = service.start(HandoverFlow.RECEPTION, incoming_id=INCOMING, caller=CALLER)

    with database.read() as connection:
        row = handover_repo.get(connection, state.handover_id)
    assert row is not None and row.outgoing_id == CUSTODIAN

    count_on_turn(service, state.handover_id, CASH, 1_000_000, caller=CALLER)
    assert service.advance(state.handover_id, caller=CALLER).step is HandoverStep.SHELF
    for item, quantity in ((PARA, 10), (DICLO, 4)):
        count_on_turn(service, state.handover_id, _item(item), quantity, caller=CALLER)
    for step in (HandoverStep.CHECKLISTS, HandoverStep.DUAL_SIGN):
        assert service.advance(state.handover_id, caller=CALLER).step is step
    service.sign(state.handover_id, caller=CALLER)
    assert service.sign(state.handover_id, caller=OUTGOING_CALLER).closed is True

    assert service.custodian(HandoverFlow.RECEPTION) == INCOMING
    with database.read() as connection:
        assert handover_repo.custodian(connection, HandoverFlow.RECEPTION) == INCOMING
