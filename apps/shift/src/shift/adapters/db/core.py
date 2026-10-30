"""SQLAlchemy Core access to the shift SQLite database."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.pool import QueuePool

from shift.adapters.db.migrations import MigrationReport, run_migrations
from shift.adapters.db.sqlite_copy import copy_database

log = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 5000
SQLITE_TIMEOUT_SECONDS = 5.0


def _apply_pragmas(dbapi_connection: Any, _record: Any, *, query_only: bool = False) -> None:
    """Set the WAL writer contract on every pooled connection (gap S2)."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        if query_only:
            # Read connections cannot write, so no errant write bypasses the single writer.
            cursor.execute("PRAGMA query_only = 1")
    finally:
        cursor.close()


def _create_engine(path: Path, *, single_writer: bool) -> Engine:
    options: dict[str, object] = {}
    if single_writer:
        options = {
            "poolclass": QueuePool,
            "pool_size": 1,
            "max_overflow": 0,
            "pool_timeout": 30,
        }
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"timeout": SQLITE_TIMEOUT_SECONDS, "check_same_thread": False},
        **options,
    )
    event.listen(engine, "connect", partial(_apply_pragmas, query_only=not single_writer))
    return engine


class Database:
    """A read pool plus one serialized write connection per process (gap S2)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._read_engine = _create_engine(self.path, single_writer=False)
        self._write_engine = _create_engine(self.path, single_writer=True)

    @contextmanager
    def read(self) -> Iterator[Connection]:
        """Yield a read-only connection; SQLite itself rejects writes through it."""
        with self._read_engine.connect() as connection:
            yield connection

    @contextmanager
    def write(self, *, commit: bool = True) -> Iterator[Connection]:
        """Yield the single writer inside `BEGIN IMMEDIATE`, commit or roll back."""
        with self._write_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.exec_driver_sql("COMMIT" if commit else "ROLLBACK")
            except BaseException:
                try:
                    connection.exec_driver_sql("ROLLBACK")
                except Exception:  # noqa: BLE001 — the original failure is the useful one
                    log.debug("rollback after failed write also failed", exc_info=True)
                raise

    def migrate(self, backup_dir: str | Path | None = None) -> MigrationReport:
        """Bring the database to the latest schema, backing up first when it has data."""
        fresh = not self.path.exists() or self.path.stat().st_size == 0
        destination = Path(backup_dir) if backup_dir is not None else self.path.parent / "backups"
        backup = None if fresh else lambda: str(_backup_database(self.path, destination))
        with self._write_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            return run_migrations(connection, backup=backup)

    def close(self) -> None:
        """Release every pooled SQLite handle."""
        self._read_engine.dispose()
        self._write_engine.dispose()


def _backup_database(source_path: Path, backup_dir: Path) -> Path:
    """Take a verified online backup before a migration (ADR-008).

    The copy comes from `copy_database`, the same primitive the periodic snapshot uses, so a
    pre-migration file is one self-contained database that restores without its source's WAL.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination_path = backup_dir / f"shift_pre_migration_{stamp}.db"
    copy_database(source_path, destination_path)
    with closing(sqlite3.connect(destination_path)) as copy:
        result = copy.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        destination_path.unlink(missing_ok=True)
        raise RuntimeError(f"pre-migration backup failed integrity_check: {result!r}")
    return destination_path
