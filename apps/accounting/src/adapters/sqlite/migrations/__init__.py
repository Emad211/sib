"""Versioned, additive-only schema migrations (ADR-002, ACC-1.1).

The schema version lives in `PRAGMA user_version` (production on 2026-09-24 = 0).
Rules (docs/accounting/A03):
  * only additive changes; the previous exe must keep working on a migrated database;
  * a verified backup is taken before the first pending migration runs;
  * each migration runs in its own transaction; on failure everything is rolled back
    and MigrationError is raised — the app must not start on a half-migrated schema;
  * a database newer than this code (user_version > LATEST) is opened as-is.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from . import m0001_baseline, m0002_performance_indexes

log = logging.getLogger(__name__)

MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "baseline", m0001_baseline.up),
    (2, "performance_indexes", m0002_performance_indexes.up),
]
LATEST = MIGRATIONS[-1][0]


class MigrationError(RuntimeError):
    """The database could not be brought to the expected schema. Message is Persian."""


@dataclass
class MigrationReport:
    from_version: int
    to_version: int
    applied: list[str] = field(default_factory=list)
    backup: str | None = None
    newer_than_code: bool = False


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def run_migrations(conn: sqlite3.Connection, backup: Callable[[], str | None] | None = None) -> MigrationReport:
    """Bring `conn` to LATEST. `backup` is called once, before the first pending step."""
    try:
        start = current_version(conn)
    except sqlite3.OperationalError as e:
        raise MigrationError(f"دیتابیس در دسترس نیست (احتمالاً برنامهٔ دیگری آن را قفل کرده است): {e}") from e
    report = MigrationReport(start, start)
    if start > LATEST:
        report.newer_than_code = True
        log.warning("database schema v%s is newer than this program (v%s); no migration run", start, LATEST)
        return report
    pending = [m for m in MIGRATIONS if m[0] > start]
    if not pending:
        return report
    if backup is not None:
        try:
            report.backup = backup()
        except Exception as e:  # noqa: BLE001 — any failure here must stop the upgrade
            raise MigrationError(f"پشتیبان پیش از به‌روزرسانی دیتابیس گرفته نشد: {e}") from e

    saved_isolation = conn.isolation_level
    conn.isolation_level = None  # manual transaction control (DDL included)
    try:
        for version, name, up in pending:
            try:
                conn.execute("BEGIN IMMEDIATE")
                up(conn)
                conn.execute(f"PRAGMA user_version = {int(version)}")
                conn.execute("COMMIT")
            except Exception as e:
                if conn.in_transaction:  # BEGIN itself may have failed (locked database)
                    conn.execute("ROLLBACK")
                raise MigrationError(
                    f"به‌روزرسانی دیتابیس در مرحلهٔ {version} ({name}) شکست خورد و هیچ تغییری ثبت نشد: {e}"
                ) from e
            report.applied.append(f"{version:04d}_{name}")
            report.to_version = version
            log.info("migration %04d_%s applied", version, name)
    finally:
        conn.isolation_level = saved_isolation
    return report
