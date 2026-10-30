"""Snapshot files: how they are named, how one is verified and how retention keeps them (gap S6, ADR-008)."""

import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shift.common.clock import require_aware

log = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "shift_"
SNAPSHOT_SUFFIX = ".db"
PARTIAL_SUFFIX = ".partial"  # a copy is written under it and renamed only after it has verified

_SNAPSHOT_NAME = re.compile(rf"^{SNAPSHOT_PREFIX}\d{{8}}T\d{{6}}\d{{6}}Z{re.escape(SNAPSHOT_SUFFIX)}$")


class BackupRejected(RuntimeError):
    """A snapshot failed verification; it is deleted and never accepted as a backup."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A verified database file: intact, with its schema version and its event count."""

    path: Path
    size_bytes: int
    schema_version: int
    events: int


def snapshot_name(moment: datetime) -> str:
    """File name for a snapshot taken at `moment`; names sort chronologically."""
    return f"{SNAPSHOT_PREFIX}{require_aware(moment).strftime('%Y%m%dT%H%M%S%fZ')}{SNAPSHOT_SUFFIX}"


def inspect_snapshot(path: str | Path) -> Snapshot:
    """Prove a database file is intact and is a shift database, or raise `BackupRejected`."""
    file = Path(path)
    if not file.is_file():
        raise BackupRejected(f"snapshot not found: {file}")
    try:
        with closing(sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if integrity is None or integrity[0] != "ok":
                raise BackupRejected(f"{file.name} failed integrity_check: {integrity!r}")
            if "events" not in tables:
                raise BackupRejected(f"{file.name} has no events table; it is not a shift database")
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            events = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    except sqlite3.Error as exc:
        raise BackupRejected(f"{file.name} cannot be read as SQLite: {exc}") from exc
    return Snapshot(file, file.stat().st_size, schema_version, events)


def prune_snapshots(directory: str | Path, *, keep: int) -> list[Path]:
    """Delete the oldest snapshots beyond `keep`; return what was deleted.

    Only names this service writes are candidates, so pre-migration snapshots and anything an
    operator put there by hand stay untouched.
    """
    folder = Path(directory)
    if not folder.is_dir():
        return []
    snapshots = sorted((path for path in folder.iterdir() if _SNAPSHOT_NAME.match(path.name)), key=lambda p: p.name)
    excess = snapshots[: max(len(snapshots) - keep, 0)]
    for path in excess:
        path.unlink()
        log.info("pruned old snapshot %s", path.name)
    return excess
