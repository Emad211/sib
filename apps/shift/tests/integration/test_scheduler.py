"""Scheduler skeleton: the settings own every time, each job runs once per period (step 14, S01 §6)."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from shift.common.clock import TEHRAN
from shift.services import scheduler as scheduler_module
from shift.services.scheduler import DAILY_CARD, WEEKLY_COUNT_WARNING, Scheduler

TUESDAY_BEFORE_OPENING = datetime(2026, 10, 27, 3, 59, tzinfo=UTC)  # 07:29 Tehran
TUESDAY_OPENING = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)  # 07:30 Tehran
TUESDAY_LATE = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)  # 12:30 Tehran
WEDNESDAY_OPENING = datetime(2026, 10, 28, 4, 0, tzinfo=UTC)  # 07:30 Tehran, next day
MONDAY_WARNING = datetime(2026, 10, 26, 4, 30, tzinfo=UTC)  # 08:00 Tehran
FRIDAY_LATE = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)  # 12:30 Tehran


@dataclass(frozen=True)
class Schedule:
    """A test double for the versioned settings; production values arrive with SHF-1.4."""

    daily_card_time: time = time(7, 30)
    daily_card_skip_weekday: int | None = 4
    weekly_count_time: time = time(8, 0)
    weekly_count_weekday: int = 0


class FrozenClock:
    """A clock pinned to one instant, so `due()` without an argument is deterministic."""

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now_utc(self) -> datetime:
        return self._moment


def test_the_daily_card_falls_due_at_the_configured_time() -> None:
    scheduler = Scheduler(Schedule())

    before = [task.name for task in scheduler.due(TUESDAY_BEFORE_OPENING)]
    due = next(task for task in scheduler.due(TUESDAY_OPENING) if task.name == DAILY_CARD)

    assert DAILY_CARD not in before
    assert due.due_at == datetime(2026, 10, 27, 7, 30, tzinfo=TEHRAN)
    assert due.period == "2026-10-27"


def test_a_job_runs_once_per_period() -> None:
    scheduler = Scheduler(Schedule())

    first = scheduler.run_due(TUESDAY_OPENING)
    second = scheduler.run_due(TUESDAY_LATE)

    assert [run.name for run in first] == [DAILY_CARD, WEEKLY_COUNT_WARNING]
    assert first[0].message.startswith("daily card requested")
    assert second == ()
    assert scheduler.due(TUESDAY_LATE) == ()
    assert [run.name for run in scheduler.run_due(WEDNESDAY_OPENING)] == [DAILY_CARD]


def test_a_missed_daily_card_is_caught_up_on_the_next_poll() -> None:
    scheduler = Scheduler(Schedule(weekly_count_weekday=2), clock=FrozenClock(TUESDAY_LATE))

    runs = scheduler.run_due()

    assert [run.name for run in runs] == [DAILY_CARD]
    assert runs[0].ran_at == TUESDAY_LATE


def test_no_daily_card_on_the_configured_skip_weekday() -> None:
    assert DAILY_CARD not in [task.name for task in Scheduler(Schedule()).due(FRIDAY_LATE)]

    skipping_friday = Schedule(daily_card_skip_weekday=None)

    assert DAILY_CARD in [task.name for task in Scheduler(skipping_friday).due(FRIDAY_LATE)]


def test_the_weekly_count_warning_runs_once_in_its_week() -> None:
    scheduler = Scheduler(Schedule())

    monday = scheduler.run_due(MONDAY_WARNING)
    tuesday = scheduler.run_due(TUESDAY_LATE)

    assert [run.name for run in monday] == [DAILY_CARD, WEEKLY_COUNT_WARNING]
    assert monday[1].period == "2026-W44"
    assert monday[1].due_at == datetime(2026, 10, 26, 8, 0, tzinfo=TEHRAN)
    assert [run.name for run in tuesday] == [DAILY_CARD]


def test_a_weekly_count_warning_missed_on_its_day_is_caught_up() -> None:
    scheduler = Scheduler(Schedule())

    due = scheduler.due(TUESDAY_LATE)

    assert [task.name for task in due] == [DAILY_CARD, WEEKLY_COUNT_WARNING]
    assert due[1].period == "2026-W44"
    assert due[1].due_at == datetime(2026, 10, 26, 8, 0, tzinfo=TEHRAN)


def test_a_weekly_count_warning_is_not_raised_before_its_weekday() -> None:
    scheduler = Scheduler(Schedule(weekly_count_weekday=2))

    assert WEEKLY_COUNT_WARNING not in [task.name for task in scheduler.due(TUESDAY_LATE)]


def test_every_time_comes_from_the_settings() -> None:
    early = Schedule(daily_card_time=time(6, 15), weekly_count_time=time(6, 45), weekly_count_weekday=2)
    scheduler = Scheduler(early)

    first = scheduler.run_due(datetime(2026, 10, 28, 2, 45, tzinfo=UTC))  # 06:15 Tehran, Wednesday
    second = scheduler.run_due(datetime(2026, 10, 28, 3, 15, tzinfo=UTC))  # 06:45 Tehran

    assert [run.name for run in first] == [DAILY_CARD]
    assert [run.name for run in second] == [WEEKLY_COUNT_WARNING]
    assert second[0].due_at == datetime(2026, 10, 28, 6, 45, tzinfo=TEHRAN)


def test_the_tehran_date_not_the_utc_date_names_the_period() -> None:
    after_midnight = datetime(2026, 10, 27, 21, 0, tzinfo=UTC)  # 00:30 Tehran on the 28th

    runs = Scheduler(Schedule(daily_card_time=time(0, 0))).run_due(after_midnight)

    daily = next(run for run in runs if run.name == DAILY_CARD)
    assert daily.period == "2026-10-28"


def test_the_scheduler_source_holds_no_literal_times() -> None:
    source = Path(scheduler_module.__file__).read_text(encoding="utf-8")

    assert re.search(r"time\(\s*\d", source) is None
    assert re.search(r"\d{1,2}:\d{2}", source) is None
