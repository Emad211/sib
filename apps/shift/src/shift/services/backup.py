"""Verified online snapshots of the live SQLite database, kept in two locations (gap S6, ADR-008).

Only driver-level primitives are used here: `PRAGMA wal_checkpoint` before copying, the `sqlite3`
online backup API (a plain file copy of a WAL database can be corrupt) and `inspect_snapshot` on
the copy before it is accepted. What a snapshot is and how it is verified lives in
`shift.services.snapshot`; this module orchestrates staging, mirroring and retention.
"""

import logging
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from shift.adapters.db.sqlite_copy import CopyRejected, copy_database
from shift.common.clock import Clock, SystemClock
from shift.services.snapshot import (
    SNAPSHOT_PREFIX,
    SNAPSHOT_SUFFIX,
    BackupRejected,
    Snapshot,
    inspect_snapshot,
    snapshot_name,
)

log = logging.getLogger(__name__)

_PARTIAL_SUFFIX = ".partial"
_NAME = re.compile(rf"^{SNAPSHOT_PREFIX}\d{{8}}T\d{{6}}\d{{6}}Z{re.escape(SNAPSHOT_SUFFIX)}$")
_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class BackupReport:
    """What one run produced: the accepted snapshot, its second copy and the pruned files."""

    snapshot: Snapshot
    mirror: Path | None
    pruned: tuple[Path, ...]


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
        """Copy through the online backup API into one file that carries no write-ahead log.

        `copy_database` is the single implementation of "a copy that restores on its own"; the
        pre-migration backup in `adapters.db.core` uses the same one.
        """
        try:
            copy_database(self._database_path, partial)
        except CopyRejected as exc:
            raise BackupRejected(str(exc)) from exc

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
