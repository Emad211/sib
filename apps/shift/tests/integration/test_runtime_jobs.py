"""A running service wires its backups, audit trail, scheduler and accounting bridge (F1).

The point of this module is the wiring, not the pieces: a job that fails must be logged and
retried without taking the others or the app down (R-06), a snapshot must land in `audit_log`,
and a started app must own its loops and stop them on shutdown.
"""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from make_synthetic_acc_db import build

from shift.adapters.db.core import Database
from shift.app import create_app
from shift.config.settings import Settings
from shift.runtime import BackgroundJobs
from shift.services import mirror as mirror_module
from shift.services.audit import AuditService
from shift.services.backup import BackupService
from shift.services.scheduler import PolicySchedule, Scheduler
from shift.services.settings import SettingsService

BEFORE_FOUR_TEHRAN = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)  # 03:30 Tehran
AFTER_FOUR_TEHRAN = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)  # 04:30 Tehran
CARD_OPENING = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)  # 07:30 Tehran, Tuesday


class MovableClock:
    """A clock the test moves by hand; production uses `SystemClock`."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now_utc(self) -> datetime:
        return self.moment


class BrokenBridge:
    """A bridge whose poll raises, standing in for any job that fails on its way to the writer."""

    def poll(self, _path: object, _shift: object) -> None:
        raise RuntimeError("the accounting copy disappeared")


def _unavailable(_source: Path, _destination: Path) -> None:
    """Stand in for a second disk that is no longer connected."""
    raise OSError("the second disk is not connected")


def _backup(database: Database, clock: MovableClock, *, mirror: Path | None = None) -> BackupService:
    return BackupService(
        database.path,
        backup_dir=database.path.parent / "snapshots",
        keep_snapshots=2,
        mirror_dir=mirror,
        clock=clock,
    )


def _jobs(
    database: Database,
    clock: MovableClock,
    *,
    bridge: object | None = None,
    accounting_path: Path | None = None,
    mirror: Path | None = None,
) -> BackgroundJobs:
    return BackgroundJobs(
        clock=clock,
        scheduler=Scheduler(PolicySchedule(SettingsService(database)), clock=clock),
        backup=_backup(database, clock, mirror=mirror),
        audit=AuditService(database, clock=clock),
        bridge=bridge,
        accounting_path=accounting_path,
    )


def _actions(database: Database) -> list[tuple[str, str]]:
    with database.read() as connection:
        rows = connection.exec_driver_sql("SELECT action, outcome FROM audit_log ORDER BY id")
        return [(str(row[0]), str(row[1])) for row in rows]


def test_a_tick_runs_the_scheduled_job_the_settings_say_is_due(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    jobs = _jobs(database, MovableClock(CARD_OPENING))

    with caplog.at_level("INFO", logger="shift.services.scheduler"):
        jobs.tick()

    assert "scheduled job daily_card ran for 2026-10-27" in caplog.text
    assert _actions(database) == [("backup.created", "ok")]  # the same tick also backed up


def test_nothing_is_snapshotted_before_four_tehran(database: Database) -> None:
    jobs = _jobs(database, MovableClock(BEFORE_FOUR_TEHRAN))

    assert jobs.backup_if_due() is None
    assert list((database.path.parent / "snapshots").glob("*.db")) == []
    assert _actions(database) == []


def test_the_daily_snapshot_is_taken_once_per_tehran_day(database: Database) -> None:
    clock = MovableClock(AFTER_FOUR_TEHRAN)
    jobs = _jobs(database, clock)

    first = jobs.backup_if_due()
    clock.moment += timedelta(hours=3)
    same_day = jobs.backup_if_due()
    clock.moment += timedelta(days=1)
    next_day = jobs.backup_if_due()

    assert first is not None and first.snapshot.path.exists()
    assert same_day is None
    assert next_day is not None
    assert [action for action, _ in _actions(database)] == ["backup.created", "backup.created"]


def test_a_startup_snapshot_before_four_leaves_the_daily_due(database: Database) -> None:
    """A restart at 03:30 must not swallow that day's 04:00 checkpoint (review F2)."""
    clock = MovableClock(BEFORE_FOUR_TEHRAN)
    jobs = _jobs(database, clock)

    startup = jobs.create_snapshot("startup")
    clock.moment = AFTER_FOUR_TEHRAN
    daily = jobs.backup_if_due()

    assert startup is not None and daily is not None
    assert [action for action, _ in _actions(database)] == ["backup.created", "backup.created"]


def test_a_startup_snapshot_after_four_covers_the_daily(database: Database) -> None:
    """A restart at 04:30 already holds the day's checkpoint, so it is not taken twice (review F2)."""
    clock = MovableClock(AFTER_FOUR_TEHRAN)
    jobs = _jobs(database, clock)

    startup = jobs.create_snapshot("startup")
    doubled = jobs.backup_if_due()
    clock.moment += timedelta(days=1)
    tomorrow = jobs.backup_if_due()

    assert startup is not None and tomorrow is not None
    assert doubled is None
    assert [action for action, _ in _actions(database)] == ["backup.created", "backup.created"]


def test_a_rejected_snapshot_is_audited_and_never_raised(database: Database, tmp_path: Path) -> None:
    clock = MovableClock(AFTER_FOUR_TEHRAN)
    jobs = BackgroundJobs(
        clock=clock,
        scheduler=Scheduler(PolicySchedule(SettingsService(database)), clock=clock),
        backup=BackupService(tmp_path / "missing.db", backup_dir=tmp_path / "snapshots", keep_snapshots=2, clock=clock),
        audit=AuditService(database, clock=clock),
    )

    assert jobs.backup_if_due() is None
    assert _actions(database) == [("backup.failed", "failed")]


def test_a_failing_mirror_is_retried_instead_of_snapshotting_every_tick(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken second disk must not turn the 30 s tick into a snapshot loop (review F1)."""
    clock = MovableClock(AFTER_FOUR_TEHRAN)
    mirror = tmp_path / "mirror"
    jobs = _jobs(database, clock, mirror=mirror)
    monkeypatch.setattr(mirror_module.shutil, "copyfile", _unavailable)

    jobs.tick()
    clock.moment += timedelta(seconds=30)
    jobs.tick()

    snapshots = sorted((database.path.parent / "snapshots").glob("shift_*.db"))
    assert len(snapshots) == 1
    assert _actions(database) == [("backup.created", "ok")]
    assert list(mirror.glob("shift_*.db")) == []

    monkeypatch.undo()
    clock.moment += timedelta(seconds=30)
    jobs.tick()

    assert [path.name for path in (database.path.parent / "snapshots").glob("shift_*.db")] == [snapshots[0].name]
    assert [path.name for path in mirror.glob("shift_*.db")] == [snapshots[0].name]


def test_one_failing_job_does_not_stop_the_others(
    database: Database, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The accounting copy is broken: the poll is logged and the backup still happens (R-06)."""
    jobs = _jobs(
        database,
        MovableClock(AFTER_FOUR_TEHRAN),
        bridge=BrokenBridge(),
        accounting_path=tmp_path / "accounting.db",
    )

    with caplog.at_level("INFO", logger="shift.runtime"):
        jobs.tick()

    assert "background job failed" in caplog.text
    assert _actions(database) == [("backup.created", "ok")]


def test_a_started_app_wires_audit_backups_the_scheduler_and_the_bridge(settings: Settings, tmp_path: Path) -> None:
    scenario = build(tmp_path / "accounting.db")
    wired = replace(settings, background_jobs=True, accounting_db_path=scenario.path, backup_keep=2)
    app = create_app(wired)

    with TestClient(app) as client:
        seed_roster(app.state.database, pepper=app.state.secret_key)
        assert app.state.audit is not None
        assert app.state.jobs is not None
        assert app.state.jobs.running

        response = login(client, mobile=ROLES["manager"].mobile, device_id=ROLES["manager"].device_id, pin=PIN)
        assert response.status_code == 200
        report = app.state.jobs.poll_accounting()
        assert report is not None and report.degraded is False

    assert not app.state.jobs.running
    assert list(Path(wired.resolved_backup_dir).glob("shift_*.db"))
    with sqlite3.connect(f"{wired.database_path.as_uri()}?mode=ro", uri=True) as connection:
        recorded = {row[0] for row in connection.execute("SELECT action FROM audit_log")}
    assert {"auth.login", "backup.created"} <= recorded
