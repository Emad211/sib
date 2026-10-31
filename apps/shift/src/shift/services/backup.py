"""Verified online snapshots of the live SQLite database, kept in two locations (gap S6, ADR-008).

Only driver-level primitives are used here: `PRAGMA wal_checkpoint` before copying, the `sqlite3`
online backup API (a plain file copy of a WAL database can be corrupt) and `inspect_snapshot` on
the copy before it is accepted. What a snapshot is lives in `shift.services.snapshot`, the second
location in `shift.services.mirror`; this module owns the order: verify, place, prune the primary,
then copy to the second location. The primary directory is what a backup means, so it is pruned on
every run and a mirror that refuses the copy never undoes or spools a snapshot (review F1).
"""

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from shift.adapters.db.sqlite_copy import CopyRejected, copy_database
from shift.common.clock import Clock, SystemClock
from shift.services.mirror import SnapshotMirror
from shift.services.photos import copy_missing_photos
from shift.services.snapshot import (
    PARTIAL_SUFFIX,
    BackupRejected,
    Snapshot,
    inspect_snapshot,
    prune_snapshots,
    snapshot_name,
)

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class BackupReport:
    """What one run produced: the accepted snapshot, its second copy and the pruned files.

    `mirror_error` says why a configured second location holds no copy of this snapshot; the
    snapshot itself is verified in the primary directory, so the backup still counts (review F1).
    """

    snapshot: Snapshot
    mirror: Path | None
    pruned: tuple[Path, ...]
    mirror_error: str | None = None
    # Receipt photos the backup did not hold yet (SHF-7); they are immutable, so each is copied once.
    photos_copied: int = 0


class BackupService:
    """Snapshots a live WAL database, verifies the copy and applies retention in both locations."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        backup_dir: str | Path,
        keep_snapshots: int,
        mirror_dir: str | Path | None = None,
        photos_dir: str | Path | None = None,
        clock: Clock | None = None,
    ) -> None:
        if keep_snapshots < 1:
            raise ValueError("keep_snapshots must be at least 1")
        self._database_path = Path(database_path)
        self._backup_dir = Path(backup_dir)
        self._mirror_dir = None if mirror_dir is None else Path(mirror_dir)
        self._mirror = None if mirror_dir is None else SnapshotMirror(mirror_dir, keep=keep_snapshots)
        self._photos_dir = None if photos_dir is None else Path(photos_dir)
        self._keep = keep_snapshots
        self._clock = clock or SystemClock()

    def create(self) -> BackupReport:
        """Take one snapshot; the primary is kept and pruned whatever the second location does."""
        if not self._database_path.is_file():
            raise BackupRejected(f"database not found: {self._database_path}")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        target = self._backup_dir / snapshot_name(self._clock.now_utc())
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        self._checkpoint()
        try:
            self._copy(partial)
            verified = inspect_snapshot(partial)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(target)
        snapshot = replace(verified, path=target)
        # The photos are content-addressed and immutable: whatever a backup does not hold yet is
        # copied once, in both locations, and never pruned — the event log still points at them.
        photos_copied = 0
        if self._photos_dir is not None:
            photos_copied += copy_missing_photos(self._photos_dir, self._backup_dir / "photos")
            if self._mirror_dir is not None:
                # The second location is a copy, never a condition of a good backup: an unreachable
                # mirror must not stop the primary's photos or the snapshot (review F1).
                try:
                    photos_copied += copy_missing_photos(self._photos_dir, self._mirror_dir / "photos")
                except OSError as exc:
                    log.warning("second location refused the photos; the primary copy stands: %s", exc)
        # Retention runs before the second copy: an unreachable mirror must not spool the primary.
        pruned = prune_snapshots(self._backup_dir, keep=self._keep)
        mirror = None
        if self._mirror is not None:
            mirror = self._mirror.copy(snapshot)
            pruned += self._mirror.prune()
        log.info(
            "snapshot %s: %s events, %s bytes, %s photos, mirror=%s",
            target.name,
            snapshot.events,
            snapshot.size_bytes,
            photos_copied,
            mirror,
        )
        return BackupReport(
            snapshot=snapshot,
            mirror=mirror,
            pruned=tuple(pruned),
            mirror_error=None if self._mirror is None else self._mirror.error,
            photos_copied=photos_copied,
        )

    def retry_mirror(self) -> Path | None:
        """Deliver the snapshot a second location refused earlier; nothing new is snapshotted."""
        return None if self._mirror is None else self._mirror.retry()

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
