"""Snapshots of a live WAL database are consistent, verified, mirrored and pruned (step 14, gap S6)."""

import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from backup_now import main as backup_now_main
from restore_check import check_backup
from restore_check import main as restore_check_main

from event_fixtures import build_event
from shift.adapters.db import migrations
from shift.adapters.db.core import Database
from shift.services import backup as backup_module
from shift.services import mirror as mirror_module
from shift.services.backup import BackupRejected, BackupService, inspect_snapshot, prune_snapshots, snapshot_name
from shift.services.event_store import EventStore

FROZEN_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


class FrozenClock:
    """A clock pinned to one instant so snapshot names and periods are deterministic."""

    def now_utc(self) -> datetime:
        return FROZEN_NOW


def _service(database: Database, directory: Path, *, keep: int = 3, mirror: Path | None = None) -> BackupService:
    return BackupService(
        database.path,
        backup_dir=directory / "backups",
        keep_snapshots=keep,
        mirror_dir=mirror,
        clock=FrozenClock(),
    )


def _unavailable(_source: Path, _destination: Path) -> None:
    """Stand in for a second disk that is no longer connected."""
    raise OSError("the second disk is not connected")


def test_snapshot_of_a_live_wal_database_is_consistent(database: Database, store: EventStore, tmp_path: Path) -> None:
    """A writer commits throughout the copy; the copy still replays as one unbroken chain (gap S6)."""
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            store.append(build_event("handover.started"))
            time.sleep(0.01)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        report = _service(database, tmp_path).create()
    finally:
        stop.set()
        thread.join(timeout=10)

    snapshot = report.snapshot
    assert snapshot.events >= 1
    assert inspect_snapshot(snapshot.path) == snapshot
    verified = check_backup(snapshot.path)
    assert verified.ok
    assert verified.chain is not None and verified.chain.checked == snapshot.events
    with database.read() as connection:
        live = int(connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar_one())
    assert snapshot.events <= live
    assert list(snapshot.path.parent.glob("*.partial*")) == []
    assert not Path(f"{snapshot.path}-wal").exists()
    assert not Path(f"{snapshot.path}-shm").exists()


def test_checkpoint_folds_the_write_ahead_log_before_the_copy(
    database: Database, store: EventStore, tmp_path: Path
) -> None:
    store.append_many([build_event("handover.started"), build_event("acc.cash_receipt")])
    wal = Path(f"{database.path}-wal")
    assert wal.stat().st_size > 0

    _service(database, tmp_path).create()

    assert wal.stat().st_size == 0


def test_corrupt_file_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corrupt = tmp_path / snapshot_name(FROZEN_NOW)
    corrupt.write_bytes(b"this is not a sqlite database, it is a truncated copy" * 8)

    with pytest.raises(BackupRejected, match="cannot be read as SQLite"):
        inspect_snapshot(corrupt)

    assert not check_backup(corrupt).ok
    assert restore_check_main(["--backup", str(corrupt)]) == 1
    assert "not restorable" in capsys.readouterr().out


def test_a_database_without_the_shift_schema_is_rejected(tmp_path: Path) -> None:
    foreign = tmp_path / "accounting_copy.db"
    raw = sqlite3.connect(foreign)
    raw.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY)")
    raw.commit()
    raw.close()

    with pytest.raises(BackupRejected, match="has no events table"):
        inspect_snapshot(foreign)


def test_a_snapshot_with_a_damaged_page_is_rejected(tmp_path: Path) -> None:
    """`integrity_check` can report damage in a row instead of raising; the file is still refused."""
    damaged = tmp_path / snapshot_name(FROZEN_NOW)
    raw = sqlite3.connect(damaged)
    raw.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, note TEXT)")
    raw.execute("CREATE UNIQUE INDEX idx_note ON events (note)")
    raw.executemany("INSERT INTO events (note) VALUES (?)", [(f"note-{index}",) for index in range(20)])
    raw.commit()
    raw.execute("PRAGMA writable_schema = ON")
    raw.execute("DELETE FROM sqlite_master WHERE name = 'idx_note'")
    raw.commit()
    raw.close()

    with pytest.raises(BackupRejected, match="failed integrity_check"):
        inspect_snapshot(damaged)


def test_a_missing_snapshot_or_directory_is_handled(tmp_path: Path) -> None:
    with pytest.raises(BackupRejected, match="snapshot not found"):
        inspect_snapshot(tmp_path / "nothing.db")

    assert prune_snapshots(tmp_path / "no-such-directory", keep=2) == []


def test_snapshot_verification_lives_apart_from_the_backup_service() -> None:
    """`Snapshot` and `inspect_snapshot` have a module of their own; the service stays under 150 lines."""
    from shift.services import snapshot as snapshot_module

    service_lines = Path(backup_module.__file__).read_text(encoding="utf-8").splitlines()
    assert len(service_lines) <= 150
    assert snapshot_module.inspect_snapshot is backup_module.inspect_snapshot
    assert snapshot_module.Snapshot is backup_module.Snapshot


def test_retention_must_keep_at_least_one_snapshot(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        BackupService(tmp_path / "shift.db", backup_dir=tmp_path / "backups", keep_snapshots=0)


def test_a_file_that_is_not_a_database_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken" / "shift.db"
    broken.parent.mkdir()
    broken.write_bytes(b"garbage written over a database" * 64)

    with pytest.raises(BackupRejected, match="cannot copy"):
        BackupService(broken, backup_dir=tmp_path / "backups", keep_snapshots=1).create()

    assert list((tmp_path / "backups").iterdir()) == []


def test_a_busy_write_ahead_log_is_a_warning_not_a_failure(
    database: Database, store: EventStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store.append(build_event("handover.started"))
    reader = sqlite3.connect(f"{database.path.as_uri()}?mode=ro", uri=True)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM events").fetchone()
        with caplog.at_level(logging.WARNING, logger="shift.services.backup"):
            report = _service(database, tmp_path).create()
    finally:
        reader.rollback()
        reader.close()

    assert report.snapshot.events == 1
    assert "wal_checkpoint is busy" in caplog.text


def test_a_snapshot_that_fails_verification_is_not_kept(
    database: Database, store: EventStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.append(build_event("handover.started"))

    def reject(_path: Path) -> None:
        raise BackupRejected("integrity_check failed: simulated corruption")

    monkeypatch.setattr(backup_module, "inspect_snapshot", reject)

    with pytest.raises(BackupRejected, match="simulated"):
        _service(database, tmp_path).create()
    assert list((tmp_path / "backups").iterdir()) == []


def test_snapshot_is_copied_to_the_second_location(database: Database, store: EventStore, tmp_path: Path) -> None:
    store.append(build_event("handover.started"))
    mirror = tmp_path / "mirror"

    report = _service(database, tmp_path, mirror=mirror).create()

    assert report.mirror == mirror / report.snapshot.path.name
    assert report.mirror.read_bytes() == report.snapshot.path.read_bytes()
    assert inspect_snapshot(report.mirror).events == 1


def test_an_unverifiable_second_copy_degrades_the_backup_and_the_primary_stays(
    database: Database, store: EventStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second location that refuses the copy never rejects the snapshot it already holds (review F1)."""
    store.append(build_event("handover.started"))
    monkeypatch.setattr(
        mirror_module.shutil, "copyfile", lambda _source, destination: Path(destination).write_bytes(b"truncated")
    )

    report = _service(database, tmp_path, mirror=tmp_path / "mirror").create()

    assert report.mirror is None
    assert report.mirror_error is not None and "second location rejected" in report.mirror_error
    primary = next((tmp_path / "backups").glob("*.db"))
    assert inspect_snapshot(primary).events == 1
    assert list((tmp_path / "mirror").glob("*.db")) == []


def test_a_second_location_that_cannot_be_reached_leaves_the_primary_snapshot(
    database: Database, store: EventStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unplugged mirror is a degraded backup, logged once, not a failed backup (review F1)."""
    store.append(build_event("handover.started"))
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"a file where the mount point used to be")

    with caplog.at_level(logging.WARNING, logger="shift.services.mirror"):
        report = _service(database, tmp_path, keep=2, mirror=blocked / "mirror").create()

    assert report.mirror is None
    assert report.mirror_error is not None
    assert inspect_snapshot(report.snapshot.path).events == 1
    assert [path.name for path in report.snapshot.path.parent.glob("shift_*.db")] == [report.snapshot.path.name]
    assert "second location unavailable" in caplog.text


def test_a_failing_second_location_still_prunes_the_primary(
    database: Database, store: EventStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention runs on the primary whatever the mirror does; otherwise the disk fills (review F1)."""
    store.append(build_event("handover.started"))
    backups, mirror = tmp_path / "backups", tmp_path / "mirror"
    backups.mkdir()
    mirror.mkdir()
    for day in range(1, 6):
        (backups / f"shift_2026100{day}T000000000000Z.db").write_bytes(b"older snapshot")
    monkeypatch.setattr(mirror_module.shutil, "copyfile", _unavailable)

    report = _service(database, tmp_path, keep=3, mirror=mirror).create()

    kept = sorted(path.name for path in backups.iterdir())
    assert len(kept) == 3
    assert kept[-1] == report.snapshot.path.name
    assert len(report.pruned) == 3


def test_a_failed_second_copy_is_retried_without_a_new_snapshot(
    database: Database, store: EventStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The snapshot the mirror refused waits and is copied once the disk is back (review F1)."""
    store.append(build_event("handover.started"))
    monkeypatch.setattr(mirror_module.shutil, "copyfile", _unavailable)
    service = _service(database, tmp_path, keep=2, mirror=tmp_path / "mirror")

    report = service.create()
    monkeypatch.undo()
    mirrored = service.retry_mirror()

    assert report.mirror is None and report.mirror_error is not None
    assert mirrored == tmp_path / "mirror" / report.snapshot.path.name
    assert inspect_snapshot(mirrored).events == 1
    assert [path.name for path in (tmp_path / "backups").glob("shift_*.db")] == [report.snapshot.path.name]
    assert service.retry_mirror() is None


def test_retention_keeps_the_newest_snapshots_in_both_locations(
    database: Database, store: EventStore, tmp_path: Path
) -> None:
    backups, mirror = tmp_path / "backups", tmp_path / "mirror"
    backups.mkdir()
    mirror.mkdir()
    for day in range(1, 6):
        name = f"shift_2026100{day}T000000000000Z.db"
        (backups / name).write_bytes(b"older snapshot")
        (mirror / name).write_bytes(b"older snapshot")
    (backups / "shift_pre_migration_20261001T000000000000Z.db").write_bytes(b"migration trail")

    report = _service(database, tmp_path, keep=3, mirror=mirror).create()

    kept = sorted(path.name for path in backups.iterdir())
    assert len(kept) == 4
    assert report.snapshot.path.name == kept[-2]
    assert "shift_pre_migration_20261001T000000000000Z.db" in kept
    assert len(report.pruned) == 6
    assert {path.parent for path in report.pruned} == {backups, mirror}
    assert len(list(mirror.iterdir())) == 3


def test_restore_check_accepts_a_real_snapshot(
    database: Database, store: EventStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store.append_many([build_event("handover.started"), build_event("acc.cash_receipt")])
    snapshot = _service(database, tmp_path).create().snapshot

    assert restore_check_main(["--backup", str(snapshot.path)]) == 0
    printed = capsys.readouterr().out
    assert "2 events linked" in printed
    assert f"schema v{migrations.LATEST}" in printed


def test_restore_check_rejects_a_tampered_chain(
    database: Database, store: EventStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store.append(build_event("handover.started"))
    snapshot = _service(database, tmp_path).create().snapshot
    raw = sqlite3.connect(snapshot.path)
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("UPDATE events SET actor_id = 'someone-else' WHERE seq = 1")
    raw.commit()
    raw.close()

    assert restore_check_main(["--backup", str(snapshot.path)]) == 1
    assert "chain broken at seq 1" in capsys.readouterr().out


def test_restore_check_rejects_a_snapshot_from_a_newer_schema(
    database: Database, store: EventStore, tmp_path: Path
) -> None:
    store.append(build_event("handover.started"))
    snapshot = _service(database, tmp_path).create().snapshot
    raw = sqlite3.connect(snapshot.path)
    raw.execute(f"PRAGMA user_version = {migrations.LATEST + 1}")
    raw.commit()
    raw.close()

    result = check_backup(snapshot.path)

    assert not result.ok
    assert result.problem is not None and "newer than this code" in result.problem


def test_backup_now_cli_takes_a_snapshot(
    database: Database, store: EventStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store.append(build_event("handover.started"))

    code = backup_now_main(["--db", str(database.path), "--to", str(tmp_path / "backups"), "--keep", "2"])

    assert code == 0
    assert "1 events" in capsys.readouterr().out


def test_backup_now_cli_exits_one_when_the_database_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = backup_now_main(["--db", str(tmp_path / "missing.db"), "--to", str(tmp_path / "backups")])

    assert code == 1
    assert "backup rejected" in capsys.readouterr().out


def test_backup_now_cli_reports_an_asked_for_second_location_it_could_not_reach(
    database: Database, store: EventStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI's contract is an intact copy in both locations; a degraded one exits 1 (review F1)."""
    store.append(build_event("handover.started"))
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"a file where the mount point used to be")

    code = backup_now_main(
        [
            "--db",
            str(database.path),
            "--to",
            str(tmp_path / "backups"),
            "--keep",
            "2",
            "--mirror",
            str(blocked / "mirror"),
        ]
    )

    assert code == 1
    assert "mirror unavailable" in capsys.readouterr().out
