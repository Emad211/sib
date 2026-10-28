"""Versioned migrations: fresh, idempotent, backed up and rollback-safe (step 2)."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import URL

from shift.adapters.db import migrations
from shift.adapters.db.core import Database
from shift.adapters.db.migrations import MigrationError, run_migrations


def _tables(database: Database) -> set[str]:
    with database.read() as connection:
        rows = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        return {str(row[0]) for row in rows}


def _version(database: Database) -> int:
    with database.read() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def test_fresh_database_is_created_at_latest_version(tmp_path: Path) -> None:
    database = Database(tmp_path / "fresh.db")
    try:
        report = database.migrate()

        assert report.from_version == 0
        assert report.to_version == migrations.LATEST
        assert report.applied == [f"{v:04d}_{name}" for v, name, _ in migrations.MIGRATIONS]
        assert report.backup is None
        assert _version(database) == migrations.LATEST
        assert {"meta_projection", "proj_stock", "proj_cash"} <= _tables(database)
    finally:
        database.close()


def test_migrations_are_idempotent_and_do_not_back_up_again(database: Database, tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    report = database.migrate(backup_dir=backups)

    assert report.applied == []
    assert report.backup is None
    assert not backups.exists() or not any(backups.iterdir())


def test_existing_database_is_backed_up_before_migrating(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
    legacy.execute("INSERT INTO legacy (note) VALUES ('keep me')")
    legacy.commit()
    legacy.close()

    backups = tmp_path / "backups"
    database = Database(path)
    try:
        report = database.migrate(backup_dir=backups)

        assert report.backup is not None
        saved_path = Path(report.backup)
        assert saved_path.exists()
        saved = sqlite3.connect(saved_path)
        try:
            assert saved.execute("PRAGMA user_version").fetchone()[0] == 0
            assert saved.execute("SELECT note FROM legacy").fetchone()[0] == "keep me"
        finally:
            saved.close()
        assert _version(database) == migrations.LATEST
        with database.read() as connection:
            assert connection.exec_driver_sql("SELECT note FROM legacy").scalar_one() == "keep me"
    finally:
        database.close()


def test_failed_migration_rolls_back_and_raises(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(connection: Connection) -> None:
        connection.exec_driver_sql("ALTER TABLE proj_stock ADD COLUMN half_done INTEGER")
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS, (migrations.LATEST + 1, "broken", broken)])

    with pytest.raises(MigrationError, match="rolled back"):
        database.migrate()

    assert _version(database) == migrations.LATEST
    with database.read() as connection:
        columns = {str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(proj_stock)")}
    assert "half_done" not in columns


def test_backup_failure_stops_the_upgrade(tmp_path: Path) -> None:
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(tmp_path / "empty.db")))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        with pytest.raises(MigrationError, match="backup failed"):
            run_migrations(connection, backup=lambda: (_ for _ in ()).throw(OSError("disk full")))
        assert int(connection.exec_driver_sql("PRAGMA user_version").scalar_one()) == 0
    engine.dispose()


def test_database_newer_than_code_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "newer.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {migrations.LATEST + 5}")
    raw.commit()
    raw.close()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
        assert report.newer_than_code is True
        assert report.applied == []
        assert report.backup is None
        assert _version(database) == migrations.LATEST + 5
    finally:
        database.close()
