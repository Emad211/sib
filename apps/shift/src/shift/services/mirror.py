"""The second snapshot location, and what happens while it cannot be reached (ADR-008).

A mirror is a copy, never a condition of a good backup: a snapshot that has been verified in the
primary backups directory stands on its own, so a second location that refuses it is logged,
remembered and tried again on a later pass instead of failing the backup (review F1).
This module only moves and keeps copies; when a snapshot is taken belongs to `shift.runtime`.
"""

import logging
import shutil
from pathlib import Path

from shift.services.snapshot import PARTIAL_SUFFIX, BackupRejected, Snapshot, inspect_snapshot, prune_snapshots

log = logging.getLogger(__name__)


class SnapshotMirror:
    """Copies accepted snapshots into a second directory and retries the one it could not deliver."""

    def __init__(self, directory: str | Path, *, keep: int) -> None:
        self._directory = Path(directory)
        self._keep = keep
        self._pending: Snapshot | None = None
        self._failure: str | None = None

    @property
    def error(self) -> str | None:
        """Why the second location last refused a copy, or `None` when it holds them all."""
        return self._failure

    def copy(self, snapshot: Snapshot) -> Path | None:
        """Copy `snapshot` there and verify it again; a refusal is recorded, never raised.

        A newer snapshot replaces whatever was waiting, because the second location only has to
        hold the newest copies; an old backlog is never replayed after the disk comes back.
        """
        try:
            mirror = self._write(snapshot)
        except (BackupRejected, OSError) as exc:
            self._pending = snapshot
            if str(exc) != self._failure:
                self._failure = str(exc)
                log.warning("second location unavailable; the primary stands and the copy is retried: %s", exc)
            else:
                log.debug("second location still unavailable: %s", exc)
            return None
        self._pending = None
        self._failure = None
        return mirror

    def retry(self) -> Path | None:
        """Deliver the snapshot the second location refused earlier, without taking a new one."""
        pending = self._pending
        if pending is None:
            return None
        if not pending.path.is_file():
            log.info("the snapshot waiting for the second location is gone: %s", pending.path.name)
            self._pending = None
            self._failure = None
            return None
        mirror = self.copy(pending)
        if mirror is not None:
            self.prune()
            log.info("second location caught up: %s", mirror.name)
        return mirror

    def prune(self) -> list[Path]:
        """Retention in the second location; an unreachable disk is already reported by `copy`."""
        try:
            return prune_snapshots(self._directory, keep=self._keep)
        except OSError as exc:
            log.warning("cannot prune the second location: %s", exc)
            return []

    def _write(self, snapshot: Snapshot) -> Path:
        """Copy a verified snapshot into the second directory and prove the copy there too."""
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._directory / snapshot.path.name
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
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
