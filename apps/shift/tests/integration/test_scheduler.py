"""Scheduler skeleton: the settings own every time, each job runs once per period (step 14, S01 §6)."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN
from shift.services import scheduler as scheduler_module
from shift.services.scheduler import DAILY_CARD, WEEKLY_COUNT_WARNING, PolicySchedule, Scheduler
from shift.services.settings import SettingsService

TUESDAY_BEFORE_OPENING = datetime(2026, 10, 27, 3, 59, tzinfo=UTC)  # 07:29 Tehran
TUESDAY_OPENING = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)  # 07:30 Tehran
TUESDAY_JUST_AFTER_OPENING = datetime(2026, 10, 27, 4, 1, tzinfo=UTC)  # 07:31 Tehran
TUESDAY_LATE = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)  # 12:30 Tehran
WEDNESDAY_OPENING = datetime(2026, 10, 28, 4, 0, tzinfo=UTC)  # 07:30 Tehran, next day
MONDAY_WARNING = datetime(2026, 10, 26, 4, 30, tzinfo=UTC)  # 08:00 Tehran
FRIDAY_LATE = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)  # 12:30 Tehran


@dataclass(frozen=True)
class Schedule:
    """A test double for the versioned settings; production values arrive with SHF-1.4."""

    daily_card_time: time = time(7, 30)
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


def test_no_daily_card_on_friday() -> None:
    """S01 §6: Friday has no card, and Saturday's card covers Friday and Saturday (ADR-018)."""
    assert DAILY_CARD not in [task.name for task in Scheduler(Schedule()).due(FRIDAY_LATE)]


def test_an_injected_action_replaces_the_stub_and_runs_once_per_period() -> None:
    """The app injects the real card builder (SHF-6.1); the period key still runs it only once."""
    calls: list[datetime] = []

    def build_card(moment: datetime) -> str:
        calls.append(moment)
        return "daily card 2026-10-27 stored (1 day)"

    scheduler = Scheduler(Schedule(), actions={DAILY_CARD: build_card})

    first = scheduler.run_due(TUESDAY_OPENING)
    second = scheduler.run_due(TUESDAY_LATE)

    assert calls == [datetime(2026, 10, 27, 7, 30, tzinfo=TEHRAN)]  # the Tehran moment of the run
    assert [run.message for run in first if run.name == DAILY_CARD] == ["daily card 2026-10-27 stored (1 day)"]
    assert DAILY_CARD not in [run.name for run in second]


def test_the_friday_gap_keeps_the_injected_card_action_from_running() -> None:
    calls: list[datetime] = []

    def build_card(moment: datetime) -> str:
        calls.append(moment)
        return "daily card stored"

    runs = Scheduler(Schedule(), actions={DAILY_CARD: build_card}).run_due(FRIDAY_LATE)

    assert DAILY_CARD not in [run.name for run in runs]
    assert calls == []


def test_an_injected_action_leaves_the_other_jobs_on_their_default() -> None:
    scheduler = Scheduler(Schedule(), actions={DAILY_CARD: lambda _moment: "daily card stored"})

    runs = scheduler.run_due(MONDAY_WARNING)

    assert [(run.name, run.message.startswith("weekly count warning raised")) for run in runs] == [
        (DAILY_CARD, False),
        (WEEKLY_COUNT_WARNING, True),
    ]


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


def test_the_scheduler_reads_its_schedule_from_the_versioned_settings(database: Database) -> None:
    """The times and days are rows now: a schedule drawn from the database still raises the card."""
    scheduler = Scheduler(PolicySchedule(SettingsService(database)), clock=FrozenClock(TUESDAY_JUST_AFTER_OPENING))

    due = next(task for task in scheduler.due() if task.name == DAILY_CARD)

    assert due.due_at == datetime(2026, 10, 27, 7, 30, tzinfo=TEHRAN)
    assert due.period == "2026-10-27"


def test_the_scheduler_source_holds_no_literal_times() -> None:
    source = Path(scheduler_module.__file__).read_text(encoding="utf-8")

    assert re.search(r"time\(\s*\d", source) is None
    assert re.search(r"\d{1,2}:\d{2}", source) is None
