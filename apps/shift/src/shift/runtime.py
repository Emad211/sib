"""The work a running service does on its own: snapshots, scheduled jobs, the accounting poll.

The lifespan starts these loops and stops them on shutdown (S02 §2, O01 §2). Each loop runs its
job in a worker thread, because SQLite and the accounting file are blocking, and one job's
failure is logged and retried on the next tick: a broken accounting copy must never stop the
backups or the app (R-06). A second backup location that cannot be reached is the same kind of
failure: logged, retried on a later pass, and never a reason to take another primary snapshot
(review F1). A test drives one pass with `tick()` instead of waiting for a timer, and the app only
starts the loops when `Settings.background_jobs` asks for them.

The accounting mirror stays live: the poll keeps reading every fact up to *now*, so a card payment or
a consumption lands within a tick (S02 §2). It attributes each fact to its shift through the handover
flows already recorded (H-11) — never through the moment the poll happened to run — and a handover
start additionally takes its own immediate read of that flow's cutoff in `services.handover`.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from shift.common.clock import Clock, to_tehran
from shift.services.accounting_bridge import AccountingBridge, PollReport
from shift.services.audit import AuditEntry, AuditOutcome, AuditService
from shift.services.backup import BackupRejected, BackupReport, BackupService
from shift.services.scheduler import Scheduler, TaskRun

log = logging.getLogger(__name__)

TICK = timedelta(seconds=30)  # S02 §2: the accounting bridge polls every 30 seconds
DAILY_BACKUP_TIME = time(4, 0)  # O01 §2: one snapshot a day, at 04:00 Tehran
SHUTDOWN_SECONDS = 30.0
RUNTIME_ACTOR = "shift-runtime"


class BackgroundJobs:
    """One event loop's worth of periodic work, run off the loop and stopped on shutdown."""

    def __init__(
        self,
        *,
        clock: Clock,
        scheduler: Scheduler,
        backup: BackupService,
        audit: AuditService | None = None,
        bridge: AccountingBridge | None = None,
        accounting_path: str | Path | None = None,
        tick: timedelta = TICK,
    ) -> None:
        if bridge is not None and accounting_path is None:
            raise ValueError("an accounting bridge needs the database path it polls")
        self._clock = clock
        self._scheduler = scheduler
        self._backup = backup
        self._audit = audit
        self._bridge = bridge
        self._accounting_path = None if accounting_path is None else Path(accounting_path)
        self._tick = tick
        self._last_snapshot_at: datetime | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def running(self) -> bool:
        """True while at least one loop is alive; a stopped runtime reports its work is done."""
        return any(not task.done() for task in self._tasks)

    def tick(self) -> None:
        """One pass of the periodic work; every job fails alone and is retried next tick."""
        for job in (self.run_scheduled_jobs, self.poll_accounting, self.backup_if_due, self.retry_mirror):
            self._guard(job)

    def run_scheduled_jobs(self) -> tuple[TaskRun, ...]:
        """Run the periodic jobs that are due; `Scheduler` reports each one in the log."""
        return self._scheduler.run_due()

    def poll_accounting(self) -> PollReport | None:
        """Ingest accounting up to now, or nothing when this install has no accounting copy."""
        if self._bridge is None or self._accounting_path is None:
            return None
        report = self._bridge.poll(self._accounting_path)
        if report.degraded:
            log.warning("accounting poll left the bridge degraded: %s", report.error)
        return report

    def create_snapshot(self, reason: str) -> BackupReport | None:
        """Take one verified snapshot and audit it; a rejected copy is logged, never raised."""
        try:
            report = self._backup.create()
        except (BackupRejected, OSError) as exc:
            log.error("%s snapshot rejected: %s", reason, exc)
            self._record("backup.failed", AuditOutcome.FAILED, {"reason": reason, "error": str(exc)})
            return None
        self._last_snapshot_at = to_tehran(self._clock.now_utc())
        self._record(
            "backup.created",
            AuditOutcome.OK,
            {"reason": reason, "snapshot": report.snapshot.path.name, "events": report.snapshot.events},
        )
        return report

    def backup_if_due(self, now: datetime | None = None) -> BackupReport | None:
        """Take the daily snapshot once 04:00 Tehran has passed and nothing covers it yet (O01 §2).

        A snapshot at or after today's 04:00 covers the day whatever its reason: a restart at
        03:30 leaves the daily due, a restart at 04:30 is the day's checkpoint and is not doubled.
        """
        moment = to_tehran(self._clock.now_utc() if now is None else now)
        if moment.time() < DAILY_BACKUP_TIME:
            return None
        daily = moment.replace(hour=DAILY_BACKUP_TIME.hour, minute=DAILY_BACKUP_TIME.minute, second=0, microsecond=0)
        if self._last_snapshot_at is not None and self._last_snapshot_at >= daily:
            return None
        return self.create_snapshot("daily")

    def retry_mirror(self) -> Path | None:
        """Deliver the snapshot a second backup location refused earlier, without a new one."""
        return self._backup.retry_mirror()

    async def start(self) -> None:
        """Take the startup snapshot, then run the periodic work until `stop()`."""
        await asyncio.to_thread(self._guard, lambda: self.create_snapshot("startup"))
        self._tasks = [asyncio.create_task(self._loop(), name="shift-runtime")]
        log.info("background jobs started, one pass every %ss", self._tick.total_seconds())

    async def stop(self) -> None:
        """Ask the loop to finish and wait for it, so nothing outlives the lifespan."""
        self._stop.set()
        pending, self._tasks = self._tasks, []
        if not pending:
            return
        _finished, still_running = await asyncio.wait(pending, timeout=SHUTDOWN_SECONDS)
        for task in still_running:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        log.info("background jobs stopped")

    async def _loop(self) -> None:
        """Run one pass off the event loop, then wait a tick or until a stop arrives."""
        while not self._stop.is_set():
            await asyncio.to_thread(self._guard, self.tick)
            try:
                await asyncio.wait_for(self._stop.wait(), self._tick.total_seconds())
            except TimeoutError:
                continue

    @staticmethod
    def _guard(job: Callable[[], object]) -> None:
        """Run one job; a failure is logged and retried, never fatal to the app or its siblings."""
        try:
            job()
        except Exception:  # noqa: BLE001 — a background job must not stop the service (R-06)
            log.exception("background job failed; it is retried on the next pass")

    def _record(self, action: str, outcome: AuditOutcome, detail: dict[str, Any]) -> None:
        """Audit one snapshot attempt; an audit failure never undoes a good backup."""
        if self._audit is None:
            return
        try:
            self._audit.record(AuditEntry(actor_id=RUNTIME_ACTOR, action=action, outcome=outcome, detail=detail))
        except Exception:  # noqa: BLE001 — the snapshot stands even when its audit row cannot be written
            log.exception("cannot audit %s", action)
