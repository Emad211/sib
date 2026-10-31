"""Incremental projection arithmetic for stock and cash (S03 §5)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection

from event_fixtures import build_event
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.services import projections as projections_module
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


def test_cash_projection_keys_on_the_box_and_an_opening_never_overwrites_it(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append_many(
        [
            build_event("handover.started", shift_id="shift-1", payload={"opening_cash": 1_000_000}),
            build_event("acc.cash_receipt", shift_id="shift-1", payload={"amount": 200_000}),
            build_event("cash.withdrawn", shift_id="shift-1", payload={"amount": 50_000}),
            build_event("cash.deposited", shift_id="shift-1", payload={"amount": 100_000}),
            build_event("handover.cash_counted", shift_id="shift-1", payload={"counted": 900_000}),
            # The next shift opens the same box and the nursing flow opens no cash at all (H-12).
            build_event("handover.started", shift_id="shift-2", payload={"opening_cash": 500_000}),
            build_event("handover.started", shift_id="shift-2", payload={"opening_cash": None}),
        ]
    )

    projections.sync()

    with database.read() as connection:
        rows = projections_repo.cash_rows(connection)
    assert rows == [{"cash_box_id": "reception", "expected": 1_050_000, "counted": 900_000, "variance": -150_000}]


def test_a_settled_cash_count_rebases_the_box_so_the_next_shifts_start_from_the_agreed_figure(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    """H-13: the agreed figure is the next `N0`, so an old gap cannot become a phantom discrepancy."""

    def cash() -> list[dict[str, object]]:
        with database.read() as connection:
            return projections_repo.cash_rows(connection)

    store.append_many(
        [
            build_event("handover.started", shift_id="2026-10-28#1", payload={"opening_cash": 1_000_000}),
            build_event("acc.cash_receipt", shift_id="2026-10-28#1", payload={"amount": 200_000}),
            build_event("handover.cash_counted", shift_id="2026-10-28#1", payload={"counted": 1_150_000}),
            build_event(
                "handover.count_settled",
                shift_id="2026-10-28#1",
                payload={"scope": "cash", "counted": 1_150_000, "basis": "agreeing_blind", "carry": 0},
            ),
            build_event(
                "discrepancy.recorded",
                shift_id="2026-10-28#1",
                payload={"scope": "cash", "difference": 50_000, "culprit_id": "staff-out"},
            ),
        ]
    )
    projections.sync()
    assert cash() == [{"cash_box_id": "reception", "expected": 1_150_000, "counted": 1_150_000, "variance": 0}]

    store.append_many(
        [
            build_event("handover.started", shift_id="2026-10-28#2", payload={"opening_cash": 500_000}),
            build_event("acc.cash_receipt", shift_id="2026-10-28#2", payload={"amount": 100_000}),
        ]
    )
    projections.sync()
    assert cash() == [{"cash_box_id": "reception", "expected": 1_250_000, "counted": 1_150_000, "variance": -100_000}]

    store.append_many(
        [
            build_event(
                "handover.count_settled",
                shift_id="2026-10-28#2",
                payload={"scope": "cash", "counted": 1_250_000, "basis": "joint", "carry": 0},
            ),
            build_event("handover.started", shift_id="2026-10-28#3", payload={"opening_cash": None}),
            build_event("cash.withdrawn", shift_id="2026-10-28#3", payload={"amount": 50_000}),
            build_event(
                "handover.count_settled",
                shift_id="2026-10-28#3",
                payload={"scope": "cash", "counted": 1_200_000, "basis": "agreeing_blind", "carry": 0},
            ),
        ]
    )
    projections.sync()
    settled = [{"cash_box_id": "reception", "expected": 1_200_000, "counted": 1_200_000, "variance": 0}]
    assert cash() == settled

    assert projections.rebuild(dry_run=True).drift == ()
    assert projections.rebuild().drift == ()
    assert cash() == settled


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
            {"cash_box_id": "reception", "expected": 1_000_000, "counted": 1_000_000, "variance": 0}
        ]


def test_sync_streams_events_instead_of_materialising_the_log(
    store: EventStore, projections: ProjectionEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.append_many(
        [
            build_event(
                "handover.item_counted",
                payload={"item_id": f"item-{index}", "location_id": "loc-1", "counted": index},
            )
            for index in range(1, 4)
        ]
    )
    real_iter_events = projections_module.events_repo.iter_events
    real_apply = ProjectionEngine._apply
    applied: list[int] = []
    applied_when_yielded: list[int] = []

    def spy_apply(
        self: ProjectionEngine,
        connection: Connection,
        event: EventRecord,
        stock_table: str,
        cash_table: str,
        *,
        include_stock: bool = True,
        include_cash: bool = True,
    ) -> None:
        applied.append(event.seq)
        real_apply(
            self,
            connection,
            event,
            stock_table,
            cash_table,
            include_stock=include_stock,
            include_cash=include_cash,
        )

    def instrumented_iter_events(connection: Connection, after_seq: int = 0) -> Iterator[EventRecord]:
        for event in real_iter_events(connection, after_seq):
            applied_when_yielded.append(len(applied))
            yield event

    monkeypatch.setattr(ProjectionEngine, "_apply", spy_apply)
    monkeypatch.setattr(projections_module.events_repo, "iter_events", instrumented_iter_events)

    report = projections.sync()

    # 0, 0, 0 would mean every event was pulled into a list before the first apply.
    assert applied_when_yielded == [0, 1, 2]
    assert report.processed == 3


def test_projection_error_names_the_event_seq(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append(build_event("handover.started"))
    with database.write() as connection:
        connection.exec_driver_sql("DROP TRIGGER events_no_update")
        connection.exec_driver_sql("UPDATE events SET type = 'unknown.event' WHERE seq = 1")

    with pytest.raises(ProjectionError, match="seq 1"):
        projections.sync()
