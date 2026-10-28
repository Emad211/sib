"""Incremental projection arithmetic for stock and cash (S03 §5)."""

import pytest

from event_fixtures import build_event
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine, ProjectionError


def test_stock_projection_uses_counts_restock_consumption_withdrawal_and_waste(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append_many(
        [
            build_event("handover.item_counted", payload={"item_id": "item-1", "location_id": "loc-1", "counted": 10}),
            build_event("stock.shelf_restocked", payload={"item_id": "item-1", "location_id": "loc-1", "quantity": 5}),
            build_event("acc.consumption", payload={"item_id": "item-1", "location_id": "loc-1", "quantity": 3}),
            build_event(
                "stock.emergency_withdrawal", payload={"item_id": "item-1", "location_id": "loc-1", "quantity": 2}
            ),
            build_event("stock.waste_reported", payload={"item_id": "item-1", "location_id": "loc-1", "quantity": 1}),
            build_event("handover.item_counted", payload={"item_id": "item-2", "location_id": "loc-2", "counted": 4}),
        ]
    )

    report = projections.sync()

    assert report.processed == 6
    with database.read() as connection:
        rows = projections_repo.stock_rows(connection)
    assert rows == [
        {"item_id": "item-1", "location_id": "loc-1", "qty": 9},
        {"item_id": "item-2", "location_id": "loc-2", "qty": 4},
    ]


def test_cash_projection_uses_opening_receipts_withdrawals_deposits_and_count(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append_many(
        [
            build_event("handover.started", shift_id="shift-1", payload={"opening_cash": 1_000_000}),
            build_event("acc.cash_receipt", shift_id="shift-1", payload={"amount": 200_000}),
            build_event("cash.withdrawn", shift_id="shift-1", payload={"amount": 50_000}),
            build_event("cash.deposited", shift_id="shift-1", payload={"amount": 100_000}),
            build_event("handover.cash_counted", shift_id="shift-1", payload={"counted": 900_000}),
            build_event("handover.started", shift_id="shift-2", payload={"opening_cash": 500_000}),
        ]
    )

    projections.sync()

    with database.read() as connection:
        rows = projections_repo.cash_rows(connection)
    assert rows == [
        {"shift_id": "shift-1", "expected": 1_050_000, "counted": 900_000, "variance": -150_000},
        {"shift_id": "shift-2", "expected": 500_000, "counted": 500_000, "variance": 0},
    ]


def test_sync_is_incremental_and_advances_meta(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append(build_event("handover.started", shift_id="shift-1"))

    first = projections.sync()
    second = projections.sync()

    assert first.processed == 1
    assert second.processed == 0
    with database.read() as connection:
        for name in projections_repo.PROJECTIONS:
            assert projections_repo.meta_row(connection, name)["last_seq"] == 1
            assert projections_repo.meta_row(connection, name)["checksum"] is None


def test_a_lagging_projection_catches_up_without_double_applying_the_other(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append_many(
        [
            build_event("handover.started", shift_id="shift-1", payload={"opening_cash": 1_000_000}),
            build_event("handover.item_counted", payload={"item_id": "item-1", "location_id": "loc-1", "counted": 10}),
        ]
    )
    projections.sync()
    with database.write() as connection:
        connection.exec_driver_sql("UPDATE meta_projection SET last_seq = 0 WHERE name = 'proj_stock'")
    store.append(
        build_event("stock.shelf_restocked", payload={"item_id": "item-1", "location_id": "loc-1", "quantity": 5})
    )

    projections.sync()

    with database.read() as connection:
        assert projections_repo.stock_rows(connection) == [{"item_id": "item-1", "location_id": "loc-1", "qty": 15}]
        assert projections_repo.cash_rows(connection) == [
            {"shift_id": "shift-1", "expected": 1_000_000, "counted": 1_000_000, "variance": 0}
        ]


def test_projection_error_names_the_event_seq(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append(build_event("handover.started"))
    with database.write() as connection:
        connection.exec_driver_sql("DROP TRIGGER events_no_update")
        connection.exec_driver_sql("UPDATE events SET type = 'unknown.event' WHERE seq = 1")

    with pytest.raises(ProjectionError, match="seq 1"):
        projections.sync()
