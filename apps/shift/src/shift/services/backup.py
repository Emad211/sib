"""Verified online snapshots of the live SQLite database, kept in two locations (gap S6, ADR-008).

Only driver-level primitives are used here: the `sqlite3` online backup API (a plain file copy of a
WAL database can be corrupt), `PRAGMA wal_checkpoint` before copying and `PRAGMA integrity_check`
on the copy before it is accepted. Every open is read-only, so a rejected file is never altered.
"""

import logging
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from shift.common.clock import Clock, SystemClock, require_aware

log = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "shift_"
SNAPSHOT_SUFFIX = ".db"
_PARTIAL_SUFFIX = ".partial"
_NAME = re.compile(rf"^{SNAPSHOT_PREFIX}\d{{8}}T\d{{6}}\d{{6}}Z{re.escape(SNAPSHOT_SUFFIX)}$")
_TIMEOUT_SECONDS = 5.0


class BackupRejected(RuntimeError):
    """A snapshot failed verification; it is deleted and never accepted as a backup."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A verified database file: intact, with its schema version and its event count."""

    path: Path
    size_bytes: int
    schema_version: int
    events: int


@dataclass(frozen=True, slots=True)
class BackupReport:
    """What one run produced: the accepted snapshot, its second copy and the pruned files."""

    snapshot: Snapshot
    mirror: Path | None
    pruned: tuple[Path, ...]


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

    Only names this service writes are candidates, so pre-migration snapshots stay untouched.
    """
    folder = Path(directory)
    if not folder.is_dir():
        return []
    snapshots = sorted((path for path in folder.iterdir() if _NAME.match(path.name)), key=lambda path: path.name)
    excess = snapshots[: max(len(snapshots) - keep, 0)]
    for path in excess:
        path.unlink()
        log.info("pruned old snapshot %s", path.name)
    return excess


class BackupService:
    """Snapshots a live WAL database, verifies the copy, mirrors it and applies retention."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        backup_dir: str | Path,
        keep_snapshots: int,
        mirror_dir: str | Path | None = None,
        clock: Clock | None = None,
    ) -> None:
        if keep_snapshots < 1:
            raise ValueError("keep_snapshots must be at least 1")
        self._database_path = Path(database_path)
        self._backup_dir = Path(backup_dir)
        self._mirror_dir = None if mirror_dir is None else Path(mirror_dir)
        self._keep = keep_snapshots
        self._clock = clock or SystemClock()

    def create(self) -> BackupReport:
        """Take one snapshot; nothing is accepted before `inspect_snapshot` returns clean."""
        if not self._database_path.is_file():
            raise BackupRejected(f"database not found: {self._database_path}")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        target = self._backup_dir / snapshot_name(self._clock.now_utc())
        partial = target.with_name(target.name + _PARTIAL_SUFFIX)
        self._checkpoint()
        try:
            self._copy(partial)
            verified = inspect_snapshot(partial)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(target)
        snapshot = replace(verified, path=target)
        mirror = self._write_mirror(snapshot)
        pruned = list(prune_snapshots(self._backup_dir, keep=self._keep))
        if self._mirror_dir is not None:
            pruned += prune_snapshots(self._mirror_dir, keep=self._keep)
        log.info(
            "snapshot %s: %s events, %s bytes, mirror=%s", target.name, snapshot.events, snapshot.size_bytes, mirror
        )
        return BackupReport(snapshot, mirror, tuple(pruned))

    def _checkpoint(self) -> None:
        """Fold the write-ahead log into the main file; a busy or missing WAL is not a failure."""
        try:
            with closing(sqlite3.connect(self._database_path, timeout=_TIMEOUT_SECONDS)) as connection:
                busy = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        except sqlite3.Error as exc:
            log.warning("wal_checkpoint skipped, the copy decides: %s", exc)
            return
        if busy:
            log.warning("wal_checkpoint is busy; the backup API still copies a consistent snapshot")

    def _copy(self, partial: Path) -> None:
        """Copy through the online backup API, which reads one consistent snapshot of a live WAL.

        The copy inherits the source's WAL header, so it is checkpointed and switched to a
        rollback journal: a snapshot must be one self-contained file with no `-wal` beside it.
        """
        try:
            with closing(sqlite3.connect(self._database_path, timeout=_TIMEOUT_SECONDS)) as source:
                with closing(sqlite3.connect(partial)) as destination:
                    source.backup(destination)
                    destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    destination.execute("PRAGMA journal_mode = DELETE")
        except sqlite3.Error as exc:
            raise BackupRejected(f"cannot copy {self._database_path.name}: {exc}") from exc

    def _write_mirror(self, snapshot: Snapshot) -> Path | None:
        """Copy the verified snapshot to the second location and verify it again there."""
        if self._mirror_dir is None:
            return None
        self._mirror_dir.mkdir(parents=True, exist_ok=True)
        target = self._mirror_dir / snapshot.path.name
        partial = target.with_name(target.name + _PARTIAL_SUFFIX)
        try:
            shutil.copyfile(snapshot.path, partial)
            inspect_snapshot(partial)
        except (BackupRejected, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise BackupRejected(
                f"second location rejected the copy ({exc}); {snapshot.path.name} stays intact"
            ) from exc
        partial.replace(target)
        return target
