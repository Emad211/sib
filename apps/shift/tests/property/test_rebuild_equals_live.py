"""SHF-1.2 acceptance: 10 000 random events rebuild to the live projection state.

The handover slice adds a second acceptance: a finished flow — blind counts, a joint count, its
discrepancies and both signatures — rebuilds to exactly the live stock and cash projections.
"""

from datetime import datetime
from pathlib import Path

from clocks import FixedClock
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from synthetic_catalog import seed
from synthetic_accounting import empty_accounting

from event_fixtures import build_event, random_events
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.handover_payloads import CountTarget
from shift.services.accounting_bridge import AccountingBridge
from shift.services.audit import AuditService
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.handover import Caller, HandoverService
from shift.services.projections import ProjectionEngine

MOMENT = datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN)


@given(seed=st.integers(min_value=0, max_value=10_000))
@settings(
    max_examples=1,
    deadline=None,
    derandomize=True,
    suppress_health_check=list(HealthCheck),
)
def test_rebuild_equals_live_over_10_000_random_events(
    seed: int, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    events = random_events(seed, 10_000)
    assert len(events) == 10_000

    results = store.append_many(events)
    assert len(results) == 10_000
    assert all(result.created for result in results)
    assert projections.sync().processed == 10_000

    with database.read() as connection:
        live_stock = projections_repo.stock_rows(connection)
        live_cash = projections_repo.cash_rows(connection)
        live_checksums = {
            name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS
        }

    check = projections.rebuild(dry_run=True)

    assert check.events == 10_000
    assert check.drift == ()
    assert check.live_checksums == live_checksums
    assert check.rebuilt_checksums == live_checksums

    applied = projections.rebuild()

    assert applied.events == 10_000
    assert applied.drift == ()
    with database.read() as connection:
        assert projections_repo.stock_rows(connection) == live_stock
        assert projections_repo.cash_rows(connection) == live_cash
        assert {
            name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS
        } == live_checksums


def test_a_finished_handover_rebuilds_to_the_live_projections(database: Database, tmp_path: Path) -> None:
    """Counts, a joint count, a discrepancy and both signatures replay to the same two tables."""
    seed(database)
    clock = FixedClock(MOMENT)
    store = EventStore(database, clock=clock)
    projections = ProjectionEngine(database, clock=clock)
    service = HandoverService(
        database,
        store,
        projections=projections,
        catalog=CatalogService(database, clock=clock),
        audit=AuditService(database, clock=clock),
        clock=clock,
        bridge=AccountingBridge(database, store, clock=clock),
        accounting_path=str(empty_accounting(tmp_path / "accounting.db")),
    )
    store.append(
        build_event(
            "stock.weekly_count",
            payload={"item_id": "para_500_amp", "location_id": "shelf_reception", "counted": 10},
            shift_id=None,
        )
    )
    caller = Caller(actor_id="staff-in", device_id="device-1")
    # S03 §3: the outgoing person signs from their own phone, with their own caller.
    outgoing_caller = Caller(actor_id="staff-out", device_id="device-2")
    cash = CountTarget(scope="cash")
    shelf = CountTarget(scope="item", item_id="para_500_amp", location_id="shelf_reception")
    diclo = CountTarget(scope="item", item_id="diclofenac_75_amp", location_id="shelf_reception")
    service.record_baseline(
        flow=HandoverFlow.RECEPTION,
        custodian_id="staff-out",
        manager_id="manager-1",
        opening_cash=1_000_000,
        items=[{"item_id": "para_500_amp", "location_id": "shelf_reception", "quantity": 10}],
    )
    handover_id = service.start(
        HandoverFlow.RECEPTION,
        incoming_id="staff-in",
        caller=caller,
    ).handover_id

    service.count(handover_id, cash, 990_000, caller=caller)
    service.count(handover_id, cash, 995_000, caller=caller)
    service.joint(handover_id, cash, 997_000, caller=caller)
    service.advance(handover_id, caller=caller)
    service.count(handover_id, shelf, 8, caller=caller)
    service.count(handover_id, shelf, 12, caller=caller)
    service.count(handover_id, shelf, 9, caller=caller)
    service.joint(handover_id, shelf, 9, caller=caller)
    service.count(handover_id, diclo, 0, caller=caller)
    service.advance(handover_id, caller=caller)
    service.answer_checklist(handover_id, "store_lock_ok", ok=True, caller=caller)
    service.advance(handover_id, caller=caller)
    service.hand_parcel(handover_id, "parcel-1", 2, caller=caller)
    service.advance(handover_id, caller=caller)
    service.sign(handover_id, caller=caller)
    assert service.sign(handover_id, caller=outgoing_caller).closed is True

    with database.read() as connection:
        live = (
            projections_repo.stock_rows(connection),
            projections_repo.cash_rows(connection),
            {name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS},
        )

    assert projections.rebuild(dry_run=True).drift == ()
    assert projections.rebuild().drift == ()
    with database.read() as connection:
        assert (
            projections_repo.stock_rows(connection),
            projections_repo.cash_rows(connection),
            {name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS},
        ) == live
    assert live[1] == [{"cash_box_id": "reception", "expected": 997_000, "counted": 997_000, "variance": 0}]


def test_a_settlement_carry_rebuilds_to_the_live_cash_projection(database: Database, tmp_path: Path) -> None:
    """H-11: the carry is stored in the settle event, so a replay rebases the box exactly as live did."""
    seed(database)
    clock = FixedClock(MOMENT)
    store = EventStore(database, clock=clock)
    projections = ProjectionEngine(database, clock=clock)
    service = HandoverService(
        database,
        store,
        projections=projections,
        catalog=CatalogService(database, clock=clock),
        audit=AuditService(database, clock=clock),
        clock=clock,
        bridge=AccountingBridge(database, store, clock=clock),
        accounting_path=str(empty_accounting(tmp_path / "accounting.db")),
    )
    caller = Caller(actor_id="staff-in", device_id="device-1")
    service.record_baseline(
        flow=HandoverFlow.RECEPTION,
        custodian_id="staff-out",
        manager_id="manager-1",
        opening_cash=1_000_000,
        items=[{"item_id": "para_500_amp", "location_id": "shelf_reception", "quantity": 10}],
    )
    handover_id = service.start(
        HandoverFlow.RECEPTION,
        incoming_id="staff-in",
        caller=caller,
    ).handover_id
    store.append(
        build_event(
            "acc.cash_receipt",
            payload={"amount": 250_000},
            client_at=datetime(2026, 10, 28, 9, 40, tzinfo=TEHRAN),
        )
    )
    projections.sync()
    cash = CountTarget(scope="cash")
    service.count(handover_id, cash, 1_000_000, caller=caller)
    service.count(handover_id, cash, 1_000_000, caller=caller)

    with database.read() as connection:
        live = projections_repo.cash_rows(connection)

    assert live == [{"cash_box_id": "reception", "expected": 1_250_000, "counted": 1_000_000, "variance": -250_000}]
    assert projections.rebuild(dry_run=True).drift == ()
    assert projections.rebuild().drift == ()
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == live


def test_the_managers_baseline_rebuilds_to_the_live_projections(database: Database, tmp_path: Path) -> None:
    """SHF-7.1: one event sets many shelves and seeds the box, and a replay lands on the same two tables."""
    seed(database)
    clock = FixedClock(MOMENT)
    store = EventStore(database, clock=clock)
    projections = ProjectionEngine(database, clock=clock)
    service = HandoverService(
        database,
        store,
        projections=projections,
        catalog=CatalogService(database, clock=clock),
        audit=AuditService(database, clock=clock),
        clock=clock,
        bridge=AccountingBridge(database, store, clock=clock),
        accounting_path=str(empty_accounting(tmp_path / "accounting.db")),
    )
    service.record_baseline(
        flow=HandoverFlow.RECEPTION,
        custodian_id="staff-out",
        manager_id="manager-1",
        opening_cash=1_000_000,
        items=[
            {"item_id": "para_500_amp", "location_id": "shelf_reception", "quantity": 10},
            {"item_id": "diclofenac_75_amp", "location_id": "shelf_reception", "quantity": 4},
        ],
    )
    service.record_baseline(
        flow=HandoverFlow.NURSING,
        custodian_id="nurse-1",
        manager_id="manager-1",
        opening_cash=None,
        items=[{"item_id": "saline_500_bag", "location_id": "cpr_room", "quantity": 6}],
    )

    with database.read() as connection:
        live = (
            projections_repo.stock_rows(connection),
            projections_repo.cash_rows(connection),
            {name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS},
        )

    assert live[1] == [{"cash_box_id": "reception", "expected": 1_000_000, "counted": 1_000_000, "variance": 0}]
    assert projections.rebuild(dry_run=True).drift == ()
    assert projections.rebuild().drift == ()
    with database.read() as connection:
        assert (
            projections_repo.stock_rows(connection),
            projections_repo.cash_rows(connection),
            {name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS},
        ) == live
