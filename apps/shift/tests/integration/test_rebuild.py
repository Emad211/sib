"""Rebuild detects projection drift, never mutates in dry-run mode, and repairs on apply."""

from rebuild_projections import main

from event_fixtures import random_events
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine


def _snapshot(database: Database):
    with database.read() as connection:
        return (
            projections_repo.stock_rows(connection),
            projections_repo.cash_rows(connection),
            {name: projections_repo.meta_row(connection, name) for name in projections_repo.PROJECTIONS},
        )


def _events():
    return random_events(seed=7, count=120)


def test_dry_run_compares_without_changing_live_tables(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append_many(_events())
    projections.sync()
    before = _snapshot(database)

    report = projections.rebuild(dry_run=True)

    assert report.applied is False
    assert report.drift == ()
    assert report.live_checksums == report.rebuilt_checksums
    assert _snapshot(database) == before


def test_apply_rebuild_replaces_live_tables_and_records_the_checksum(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append_many(_events())
    projections.sync()
    before = _snapshot(database)

    report = projections.rebuild()

    assert report.applied is True
    assert report.drift == ()
    after = _snapshot(database)
    assert after[0] == before[0]
    assert after[1] == before[1]
    with database.read() as connection:
        for name in projections_repo.PROJECTIONS:
            meta = projections_repo.meta_row(connection, name)
            assert meta["last_seq"] == report.last_seq
            assert meta["checksum"] == report.rebuilt_checksums[name]
            assert meta["built_at"]


def test_injected_drift_is_reported_and_repaired(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    store.append_many(_events())
    projections.sync()
    with database.write() as connection:
        connection.exec_driver_sql("UPDATE proj_stock SET qty = qty + 1")
        connection.exec_driver_sql("UPDATE proj_cash SET expected = expected + 1")
    with database.read() as connection:
        tampered = (projections_repo.stock_rows(connection), projections_repo.cash_rows(connection))

    dry = projections.rebuild(dry_run=True)

    assert set(dry.drift) == set(projections_repo.PROJECTIONS)
    with database.read() as connection:
        assert (projections_repo.stock_rows(connection), projections_repo.cash_rows(connection)) == tampered
        assert (
            projections_repo.checksum(connection, projections_repo.STOCK, projections_repo.STOCK)
            == dry.live_checksums[projections_repo.STOCK]
        )
    applied = projections.rebuild()
    assert set(applied.drift) == set(projections_repo.PROJECTIONS)
    with database.read() as connection:
        assert (
            projections_repo.checksum(connection, projections_repo.STOCK, projections_repo.STOCK)
            == applied.rebuilt_checksums[projections_repo.STOCK]
        )


def test_rebuild_cli_exit_codes_and_apply(
    database: Database, store: EventStore, projections: ProjectionEngine, capsys
) -> None:
    store.append_many(_events())
    projections.sync()

    assert main(["--db", str(database.path)]) == 0
    assert "projections ok" in capsys.readouterr().out

    with database.write() as connection:
        connection.exec_driver_sql("UPDATE proj_stock SET qty = qty + 1")
    assert main(["--db", str(database.path)]) == 1
    assert "drift detected" in capsys.readouterr().out

    assert main(["--db", str(database.path), "--apply"]) == 0
    assert main(["--db", str(database.path)]) == 0


def test_rebuild_cli_missing_database_exits_one(tmp_path, capsys) -> None:
    assert main(["--db", str(tmp_path / "missing.db")]) == 1
    assert "database not found" in capsys.readouterr().out
