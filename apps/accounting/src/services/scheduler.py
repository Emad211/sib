"""Automatic backups (ADR-008, ACC-1.2).

* On start: a backup if the newest one is older than STARTUP_MAX_AGE (or there is none).
* Daily: one backup after DAILY_HOUR local time, if none was taken that calendar day.
* After each backup: rotation (services.backup.prune).
* The manager's "auto_backup" setting (default on) is honoured.
All decisions live in `on_startup()` / `tick()` so they are testable without threads.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from src.common.utils import iran_now
from src.services import backup as bk

log = logging.getLogger(__name__)

DAILY_HOUR = 4
STARTUP_MAX_AGE = timedelta(hours=20)
POLL_SECONDS = 60


class BackupScheduler:
    def __init__(self, app=None):
        self.db_path: Path | None = None
        self.backup_dir: Path | None = None
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        if app is not None:
            self.init_app(app)

    @classmethod
    def for_paths(cls, db_path: str, backup_dir: str) -> BackupScheduler:
        s = cls()
        s.db_path, s.backup_dir = Path(db_path), Path(backup_dir)
        return s

    def init_app(self, app) -> None:
        self.db_path = Path(app.config["DATABASE_PATH"])
        self.backup_dir = Path(app.config["BACKUP_FOLDER"])
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- decisions
    def _enabled(self) -> bool:
        try:
            conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT value FROM settings WHERE key = 'auto_backup'").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return True  # no settings table yet: default on
        return row is None or str(row[0]) != "0"

    def _taken_on(self, day) -> bool:
        return any(
            b.created_at.date() == day and (b.name.startswith("backup_auto_") or b.name.startswith("backup_startup_"))
            for b in bk.list_backups(self.backup_dir)
        )

    def _make(self, kind: str, now: datetime):
        made = bk.create_backup(self.db_path, self.backup_dir, kind, now=now)
        bk.prune(self.backup_dir)
        return made

    def on_startup(self, now: datetime | None = None):
        now = now or iran_now()
        if not self._enabled() or not self.db_path.exists():
            return None
        age = bk.latest_backup_age(self.backup_dir, now)
        if age is not None and age < STARTUP_MAX_AGE:
            return None
        return self._make(bk.KIND_STARTUP, now)

    def tick(self, now: datetime | None = None):
        now = now or iran_now()
        if now.hour < DAILY_HOUR or not self._enabled() or self._taken_on(now.date()):
            return None
        return self._make(bk.KIND_AUTO, now)

    # ---------------------------------------------------------------- thread
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="backup-scheduler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _run(self) -> None:
        try:
            self.on_startup()
        except Exception:  # noqa: BLE001 — a failed backup must never stop the program
            log.exception("startup backup failed")
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("daily backup failed")


scheduler = BackupScheduler()


def init_scheduler(app):
    """Initialize and start the backup scheduler"""
    scheduler.init_app(app)
    scheduler.start()
    return scheduler
