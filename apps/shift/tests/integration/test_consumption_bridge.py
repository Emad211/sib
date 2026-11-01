"""A1: accounting consumption takes stock off the shelves, once, and gives it back when accounting does.

The shelf reference is `E = C0 + R - D - U - W`, and `D` is what accounting consumed. The bridge turns
each consumption line into an `acc.consumption` event on the shelf of the catalog item that owns the
line's accounting code, attributed to that shelf's own flow like cash. These tests drive the synthetic
accounting database through the sequences that matter (a code entered later, a void, a reopen, a line
accounting rewrote, a count that already saw the consumption) and assert the stock projection, the
idempotency of re-polling, and that a rebuild reproduces the live projection.
"""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from clocks import FixedClock
from make_synthetic_acc_db import SyntheticAccounting, build

from shift.adapters.accounting import views
from shift.adapters.accounting.views import parse_time
from shift.adapters.db import catalog_repo, handover_repo, projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore, NewEvent
from shift.services.projections import ProjectionEngine

SYRINGE = ("syringe_5", "shelf_reception")
SERUM = ("serum_half", "cpr_room")
CONSUMPTION_VIEW = views.ViewName.CONSUMPTION.value


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def bridge(database: Database, store: EventStore, scenario: SyntheticAccounting) -> AccountingBridge:
    return AccountingBridge(database, store, clock=FixedClock(parse_time(scenario.cutoff_at)))


def _install_catalog(database: Database, *, serum_code: str | None = "SER-500") -> None:
    """Two counted items: the syringe on the reception shelf, the half-litre serum in the CPR room."""
    baseline = ShiftKey.baseline().text
    with database.write() as connection:
        for location_id in (SYRINGE[1], SERUM[1]):
            if catalog_repo.find_location_latest(connection, location_id) is None:
                catalog_repo.insert_location_version(
                    connection,
                    location_id=location_id,
                    name=location_id,
                    retired=False,
                    version=1,
                    effective_from_shift=baseline,
                    changed_at="2026-09-01T00:00:00+00:00",
                    changed_by="tests",
                )
        for item_id, location_id, code in (
            (SYRINGE[0], SYRINGE[1], "SYR-5"),
            (SERUM[0], SERUM[1], serum_code),
        ):
            if code is None or catalog_repo.find_item_latest(connection, item_id) is not None:
                continue
            catalog_repo.insert_item_version(
                connection,
                item_id=item_id,
                name=item_id,
                kind="counted",
                location_id=location_id,
                unit="عدد",
                minimum=1,
                acc_code=code,
                stock_factor=1,
                retired=False,
                version=1,
                effective_from_shift=baseline,
                changed_at="2026-09-01T00:00:00+00:00",
                changed_by="tests",
            )


def _count(store: EventStore, item: tuple[str, str], counted: int) -> None:
    """The shelf's starting figure: a weekly count SETs it."""
    store.append(
        NewEvent(
            client_event_id=new_ulid(),
            type="stock.weekly_count",
            payload={"item_id": item[0], "location_id": item[1], "counted": counted},
            actor_id="staff-1",
            device_id="device-1",
            work_date=date(2026, 9, 20),
            client_at=datetime.fromisoformat("2026-09-20T08:00:00+00:00"),
            idempotency_key=f"seed:{item[0]}",
        )
    )


def _stock(database: Database, projections: ProjectionEngine) -> dict[tuple[str, str], int]:
    projections.sync()
    with database.read() as connection:
        return {
            (row["item_id"], row["location_id"]): int(row["qty"]) for row in projections_repo.stock_rows(connection)
        }


def _events(database: Database, event_type: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT event_id, shift_id, payload, ref_event_id, idempotency_key FROM events WHERE type = ? ORDER BY seq",
            (event_type,),
        ).mappings()
        return [
            {
                "event_id": str(row["event_id"]),
                "shift_id": row["shift_id"],
                "payload": json.loads(row["payload"]),
                "ref": row["ref_event_id"],
                "key": str(row["idempotency_key"]),
            }
            for row in rows
        ]


def _sql(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement, parameters in statements:
            connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture()
def stocked(database: Database, store: EventStore) -> None:
    """Catalog installed and both shelves counted: the syringe at 20, the serum at 10."""
    _install_catalog(database)
    _count(store, SYRINGE, 20)
    _count(store, SERUM, 10)


def test_mapped_consumption_takes_stock_off_its_shelf(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    stocked: None,
) -> None:
    report = bridge.poll(scenario.path)

    # 3 syringes; 4 serums with a stock factor of 0.5 are 2 half-litre bags.
    assert _stock(database, projections) == {SYRINGE: 17, SERUM: 8}
    assert [event["payload"]["quantity"] for event in _events(database, "acc.consumption")] == [3, 2]
    assert report.events >= 2
    # Gauze has no accounting code and the gloves no tariff at all: they wait in the mirror.
    assert report.unmapped_consumption == 2


def test_repolling_and_a_lost_watermark_change_nothing(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    stocked: None,
) -> None:
    bridge.poll(scenario.path)
    first = _stock(database, projections)

    assert bridge.poll(scenario.path).events == 0
    with database.write() as connection:
        connection.exec_driver_sql("DELETE FROM acc_watermark WHERE view_name = ?", (CONSUMPTION_VIEW,))
    assert bridge.poll(scenario.path).events == 0

    assert _stock(database, projections) == first
    assert len(_events(database, "acc.consumption")) == 2


def test_a_line_waits_until_the_manager_enters_the_code(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    store: EventStore,
) -> None:
    _install_catalog(database, serum_code=None)  # the serum item has no accounting code yet
    _count(store, SYRINGE, 20)
    _count(store, SERUM, 10)
    first = bridge.poll(scenario.path)
    assert _stock(database, projections) == {SYRINGE: 17, SERUM: 10}
    assert first.unmapped_consumption == 3

    _install_catalog(database)  # the manager enters the code
    second = bridge.poll(scenario.path)

    assert second.events == 1
    assert second.unmapped_consumption == 2
    assert _stock(database, projections) == {SYRINGE: 17, SERUM: 8}


def test_a_fractional_quantity_rounds_half_up_once(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    stocked: None,
) -> None:
    _sql(
        scenario.path,
        [
            (
                "INSERT INTO consumables_ledger (patient_id, item_name, category, quantity, total_cost, patient_provided, "
                "usage_date, shift, work_date, invoice_id) VALUES (1, 'سرم نیم‌لیتری', 'drug', 3, 0, 0, "
                "'2026-09-24 08:10:00', 'morning', '2026-09-24', ?)",
                (scenario.cash_invoice,),
            )
        ],
    )

    bridge.poll(scenario.path)

    assert [event["payload"]["quantity"] for event in _events(database, "acc.consumption")] == [3, 2, 2]  # 1.5 -> 2
    assert _stock(database, projections)[SERUM] == 10 - 2 - 2


def test_each_item_follows_its_own_flow(
    scenario: SyntheticAccounting, database: Database, store: EventStore, stocked: None
) -> None:
    flows: list[HandoverFlow] = []

    def resolve(instant: datetime, flow: HandoverFlow) -> ShiftKey:
        flows.append(flow)
        return ShiftKey.parse("2026-09-24#1")

    AccountingBridge(database, store, clock=FixedClock(parse_time(scenario.cutoff_at)), resolve_shift=resolve).poll(
        scenario.path
    )

    # The shelf follows the reception flow and the CPR room follows nursing, never the poll's own clock.
    assert HandoverFlow.NURSING in flows
    assert {event["shift_id"] for event in _events(database, "acc.consumption")} == {"2026-09-24#1"}


def test_a_void_gives_the_stock_back_once(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    stocked: None,
) -> None:
    bridge.poll(scenario.path)
    _sql(
        scenario.path,
        [
            (
                "INSERT INTO invoice_voids (invoice_id, kind, amount, refund_method, reason, voided_by, voided_at) "
                "VALUES (?, 'void', 0, NULL, 'ثبت آزمایشی', 'manager1', '2026-09-24 13:30:00')",
                (scenario.cash_invoice,),
            ),
            ("UPDATE invoices SET status = 'voided' WHERE id = ?", (scenario.cash_invoice,)),
        ],
    )

    assert bridge.poll(scenario.path).events >= 2
    assert _stock(database, projections) == {SYRINGE: 20, SERUM: 10}
    reversals = _events(database, "acc.consumption_reversal")
    consumed = _events(database, "acc.consumption")
    assert [event["ref"] for event in reversals] == [event["event_id"] for event in consumed]
    assert {event["payload"]["reason"] for event in reversals} == {"voided"}

    assert bridge.poll(scenario.path).events == 0
    assert _stock(database, projections) == {SYRINGE: 20, SERUM: 10}


def test_a_reopened_invoice_gives_stock_back_and_the_new_close_takes_it_again(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    stocked: None,
) -> None:
    bridge.poll(scenario.path)
    _sql(
        scenario.path,
        [("UPDATE invoices SET status = 'open', closed_at = NULL WHERE id = ?", (scenario.cash_invoice,))],
    )
    bridge.poll(scenario.path)
    assert _stock(database, projections) == {SYRINGE: 20, SERUM: 10}  # open invoices consume nothing

    _sql(
        scenario.path,
        [
            (
                "UPDATE consumables_ledger SET quantity = 5 WHERE invoice_id = ? AND item_name = 'سرنگ ۵ سیسی'",
                (scenario.cash_invoice,),
            ),
            (
                "UPDATE invoices SET status = 'closed', closed_at = '2026-09-24 13:45:00' WHERE id = ?",
                (scenario.cash_invoice,),
            ),
        ],
    )
    bridge.poll(scenario.path)

    assert _stock(database, projections) == {SYRINGE: 15, SERUM: 8}
    assert bridge.poll(scenario.path).events == 0


def test_a_line_accounting_rewrote_between_polls_is_reversed_and_applied_again(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    stocked: None,
) -> None:
    bridge.poll(scenario.path)
    _sql(
        scenario.path,
        [
            (
                "UPDATE consumables_ledger SET quantity = 6 WHERE invoice_id = ? AND item_name = 'سرنگ ۵ سیسی'",
                (scenario.cash_invoice,),
            ),
            ("UPDATE invoices SET closed_at = '2026-09-24 13:50:00' WHERE id = ?", (scenario.cash_invoice,)),
        ],
    )

    bridge.poll(scenario.path)

    assert _stock(database, projections)[SYRINGE] == 20 - 6
    assert bridge.poll(scenario.path).events == 0


def test_a_consumption_the_count_already_saw_is_not_taken_twice(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    store: EventStore,
    stocked: None,
) -> None:
    """The reception flow settled the syringe shelf before the bridge saw a line stamped before its cutoff (H-11)."""
    opened_at = parse_time(scenario.cutoff_at)
    with database.write() as connection:
        handover_repo.insert(
            connection,
            handover_id="flow-1",
            shift_id="2026-09-24#1",
            flow=HandoverFlow.RECEPTION,
            opened_at=opened_at,
            work_date=date(2026, 9, 24),
            outgoing_id="staff-out",
            incoming_id="staff-in",
            created_at=opened_at,
        )
    store.append(
        NewEvent(
            client_event_id=new_ulid(),
            type="handover.count_settled",
            payload={
                "scope": "item",
                "item_id": SYRINGE[0],
                "location_id": SYRINGE[1],
                "counted": 12,
                "basis": "matched",
            },
            actor_id="staff-in",
            device_id="device-1",
            work_date=date(2026, 9, 24),
            client_at=opened_at,
            shift_id="2026-09-24#1",
            handover_id="flow-1",
            idempotency_key="settle-1",
        )
    )

    bridge.poll(scenario.path)

    assert _stock(database, projections)[SYRINGE] == 12  # the count's figure stands, nothing taken again
    assert [event["payload"]["item_id"] for event in _events(database, "acc.consumption")] == [SERUM[0]]
    assert bridge.poll(scenario.path).events == 0


def test_rebuild_equals_the_live_projection_after_all_of_it(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    database: Database,
    projections: ProjectionEngine,
    stocked: None,
) -> None:
    bridge.poll(scenario.path)
    _sql(
        scenario.path,
        [
            ("UPDATE invoices SET status = 'open', closed_at = NULL WHERE id = ?", (scenario.cash_invoice,)),
        ],
    )
    bridge.poll(scenario.path)
    _sql(
        scenario.path,
        [
            (
                "UPDATE invoices SET status = 'closed', closed_at = '2026-09-24 13:55:00' WHERE id = ?",
                (scenario.cash_invoice,),
            )
        ],
    )
    bridge.poll(scenario.path)
    projections.sync()

    assert projections.rebuild(dry_run=True).drift == ()
    assert projections.rebuild().drift == ()
    assert _stock(database, projections) == {SYRINGE: 17, SERUM: 8}
