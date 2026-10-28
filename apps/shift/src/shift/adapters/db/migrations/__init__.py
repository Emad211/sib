"""Versioned, additive-only schema migrations tracked by `PRAGMA user_version`."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import Connection

from shift.adapters.db.migrations import m0001_baseline, m0005_audit_log

log = logging.getLogger(__name__)

MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "baseline", m0001_baseline.up),
    (5, "audit_log", m0005_audit_log.up),
]
LATEST = MIGRATIONS[-1][0]


class MigrationError(RuntimeError):
    """The schema could not be brought up to date; callers must not start serving."""


@dataclass
class MigrationReport:
    from_version: int
    to_version: int
    applied: list[str] = field(default_factory=list)
    backup: str | None = None
    newer_than_code: bool = False


def current_version(connection: Connection) -> int:
    """Read the schema version stored in the database header."""
    return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def run_migrations(
    connection: Connection,
    backup: Callable[[], str | None] | None = None,
) -> MigrationReport:
    """Apply pending migrations, each in its own transaction, after one backup."""
    start = current_version(connection)
    report = MigrationReport(from_version=start, to_version=start)
    if start > LATEST:
        report.newer_than_code = True
        log.warning("database schema v%s is newer than this code v%s; not migrating", start, LATEST)
        return report

    pending = [migration for migration in MIGRATIONS if migration[0] > start]
    if not pending:
        return report

    if backup is not None:
        try:
            report.backup = backup()
        except Exception as exc:
            raise MigrationError(f"pre-migration backup failed, nothing was changed: {exc}") from exc

    for version, name, up in pending:
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            up(connection)
            connection.exec_driver_sql(f"PRAGMA user_version = {int(version)}")
            connection.exec_driver_sql("COMMIT")
        except Exception as exc:
            try:
                connection.exec_driver_sql("ROLLBACK")
            except Exception:  # noqa: BLE001 — the original failure is the useful one
                log.debug("rollback after failed migration also failed", exc_info=True)
            raise MigrationError(f"migration {version:04d}_{name} failed and was rolled back: {exc}") from exc
        report.applied.append(f"{version:04d}_{name}")
        report.to_version = version
        log.info("migration %04d_%s applied", version, name)
    return report
