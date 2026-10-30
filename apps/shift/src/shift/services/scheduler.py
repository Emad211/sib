"""When the periodic jobs of the shift service run (skeleton for design step 14, S01 §6).

The module decides *when*: the daily manager card and the Monday weekly-count warning. What a job
actually does is still a stub — the card's seven sections arrive with SHF-6.1 and the warning text
with SHF-4.x — so each run only reports itself. Every time and weekday comes from the versioned
settings through `ScheduleSettings`; this file holds no literal hour, and a missed run is caught up
on the next poll because `due` compares the clock with the schedule rather than waiting for a tick.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol

from shift.common.clock import TEHRAN, Clock, SystemClock, to_tehran
from shift.domain.policy import SettingKey
from shift.services.settings import SettingsService

log = logging.getLogger(__name__)

DAILY_CARD = "daily_card"
WEEKLY_COUNT_WARNING = "weekly_count_warning"


class ScheduleSettings(Protocol):
    """Schedule inputs that live in the versioned settings, so a change needs no deploy (R-09)."""

    daily_card_time: time
    daily_card_skip_weekday: int | None
    weekly_count_time: time
    weekly_count_weekday: int


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """A job that is due now: its name, the instant it was scheduled for and its period key."""

    name: str
    due_at: datetime
    period: str


@dataclass(frozen=True, slots=True)
class TaskRun:
    """The record of one executed job: which period, when it was due, when it actually ran."""

    name: str
    period: str
    due_at: datetime
    ran_at: datetime
    message: str


def _daily_card_stub(_moment: datetime) -> str:
    """Stand-in for the SHF-6.1 card: Saturday's card covers Friday and Saturday."""
    return "daily card requested (stub)"


def _weekly_count_stub(_moment: datetime) -> str:
    """Stand-in for the SHF-4.x reminder: the weekly shelf count runs Saturday to Monday."""
    return "weekly count warning raised (stub)"


_ACTIONS: dict[str, Callable[[datetime], str]] = {
    DAILY_CARD: _daily_card_stub,
    WEEKLY_COUNT_WARNING: _weekly_count_stub,
}


@dataclass(frozen=True, slots=True)
class PolicySchedule:
    """`ScheduleSettings` backed by the versioned settings (R-09): a change needs no deploy."""

    settings: SettingsService

    @property
    def daily_card_time(self) -> time:
        """When the daily manager card falls due."""
        return self.settings.clock_time(SettingKey.CARD_TIME)

    @property
    def daily_card_skip_weekday(self) -> int:
        """The weekday without a card; a Python weekday number, so Monday is 0."""
        return self.settings.number(SettingKey.DAILY_CARD_SKIP_WEEKDAY)

    @property
    def weekly_count_time(self) -> time:
        """When the weekly shelf-count warning falls due."""
        return self.settings.clock_time(SettingKey.WEEKLY_COUNT_TIME)

    @property
    def weekly_count_weekday(self) -> int:
        """The weekday the count is expected by; a Python weekday number, so Monday is 0."""
        return self.settings.number(SettingKey.WEEKLY_COUNT_WEEKDAY)


class Scheduler:
    """Decides which periodic jobs are due and runs each one at most once per period."""

    def __init__(self, settings: ScheduleSettings, *, clock: Clock | None = None) -> None:
        self._settings = settings
        self._clock = clock or SystemClock()
        self._last_period: dict[str, str] = {}

    def due(self, now: datetime | None = None) -> tuple[ScheduledTask, ...]:
        """Jobs that are due at `now` (default: the clock) and have not run in their period yet."""
        moment = self._moment(now)
        tasks = (self._daily_card(moment), self._weekly_count(moment))
        return tuple(task for task in tasks if task is not None)

    def run_due(self, now: datetime | None = None) -> tuple[TaskRun, ...]:
        """Run every due job's stub and remember its period; a restart forgets, a later step persists."""
        moment = self._moment(now)
        runs: list[TaskRun] = []
        for task in self.due(moment):
            message = _ACTIONS[task.name](moment)
            self._last_period[task.name] = task.period
            runs.append(TaskRun(task.name, task.period, task.due_at, moment, message))
            log.info("scheduled job %s ran for %s: %s", task.name, task.period, message)
        return tuple(runs)

    def _moment(self, now: datetime | None) -> datetime:
        return to_tehran(self._clock.now_utc() if now is None else now)

    def _daily_card(self, moment: datetime) -> ScheduledTask | None:
        if moment.weekday() == self._settings.daily_card_skip_weekday:
            return None
        return self._schedule(DAILY_CARD, moment, self._settings.daily_card_time)

    def _weekly_count(self, moment: datetime) -> ScheduledTask | None:
        """Due from its weekday to the end of that ISO week, so a day switched off loses no warning."""
        settings = self._settings
        elapsed = moment.weekday() - settings.weekly_count_weekday
        if elapsed < 0:
            return None
        due_at = datetime.combine(moment.date() - timedelta(days=elapsed), settings.weekly_count_time, tzinfo=TEHRAN)
        period = _period_key(WEEKLY_COUNT_WARNING, moment)
        if moment < due_at or self._last_period.get(WEEKLY_COUNT_WARNING) == period:
            return None
        return ScheduledTask(WEEKLY_COUNT_WARNING, due_at, period)

    def _schedule(self, name: str, moment: datetime, at: time) -> ScheduledTask | None:
        due_at = datetime.combine(moment.date(), at, tzinfo=TEHRAN)
        period = _period_key(name, moment)
        if moment < due_at or self._last_period.get(name) == period:
            return None
        return ScheduledTask(name, due_at, period)


def _period_key(name: str, moment: datetime) -> str:
    """Daily jobs key on the Tehran date, weekly jobs on the ISO week that contains it."""
    if name == DAILY_CARD:
        return moment.date().isoformat()
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
