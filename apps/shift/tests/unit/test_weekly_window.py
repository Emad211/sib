"""P-11's weekly count: the Saturday..Monday window and the Tehran deadline that makes it overdue.

The clinic counts the main store once a week on one of three days and wants the manager's card to warn
when the week went by without it (S01 §4). The window is a pure function of a date and the manager's
`weekly_count_weekday`, and every boundary is Tehran time (UTC+03:30), so a window is never decided by
the server's own timezone.
"""

from datetime import UTC, date, datetime, time

import pytest

from shift.common.clock import TEHRAN
from shift.domain.stock_checks import weekly_status, weekly_window

MONDAY = 0
WEDNESDAY = 2
SATURDAY = date(2026, 10, 31)
SUNDAY = date(2026, 11, 1)
MONDAY_DAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
FRIDAY = date(2026, 11, 6)
NEXT_SATURDAY = date(2026, 11, 7)
DUE = time(18, 0)
THIS_WEEK = (SATURDAY, MONDAY_DAY)


def _tehran(day: date, hour: int, minute: int = 0) -> datetime:
    """A Tehran wall-clock instant, the way the clinic reads its own clock."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TEHRAN)


def test_the_clinics_window_is_saturday_to_monday() -> None:
    for day in (SATURDAY, SUNDAY, MONDAY_DAY):
        assert weekly_window(day, due_weekday=MONDAY) == THIS_WEEK


def test_a_day_after_the_window_belongs_to_the_one_that_just_closed() -> None:
    """The manager is still waiting for that count, so Tuesday is not a fresh, quiet week."""
    for day in (TUESDAY, FRIDAY):
        assert weekly_window(day, due_weekday=MONDAY) == THIS_WEEK
    assert weekly_window(NEXT_SATURDAY, due_weekday=MONDAY) == (NEXT_SATURDAY, NEXT_SATURDAY.replace(day=9))


def test_the_window_follows_the_due_weekday_the_manager_set() -> None:
    wednesday = date(2026, 11, 4)

    assert weekly_window(wednesday, due_weekday=WEDNESDAY) == (MONDAY_DAY, wednesday)
    assert weekly_window(date(2026, 11, 5), due_weekday=WEDNESDAY) == (MONDAY_DAY, wednesday)


@pytest.mark.parametrize("due_weekday", [-1, 7, 12])
def test_a_weekday_outside_the_calendar_is_refused(due_weekday: int) -> None:
    with pytest.raises(ValueError, match="due_weekday"):
        weekly_window(MONDAY_DAY, due_weekday=due_weekday)


def test_a_count_inside_the_window_closes_it() -> None:
    counted = _tehran(SUNDAY, 9, 0)

    status = weekly_status(counted, _tehran(MONDAY_DAY, 23, 59), due_weekday=MONDAY, due_time=DUE)

    assert status.state == "done"
    assert (status.window_start, status.window_end) == THIS_WEEK
    assert status.last_count_at == counted


def test_an_uncounted_window_is_due_until_its_deadline() -> None:
    status = weekly_status(None, _tehran(MONDAY_DAY, 17, 59), due_weekday=MONDAY, due_time=DUE)

    assert status.state == "due"
    assert (status.window_start, status.window_end) == THIS_WEEK
    assert status.last_count_at is None


def test_the_deadline_minute_is_still_on_time() -> None:
    """`weekly_count_time` is the last minute the count is on time, not the first minute it is late."""
    deadline = weekly_status(None, _tehran(MONDAY_DAY, 18, 0), due_weekday=MONDAY, due_time=DUE)
    late = weekly_status(None, _tehran(MONDAY_DAY, 18, 1), due_weekday=MONDAY, due_time=DUE)

    assert (deadline.state, late.state) == ("due", "overdue")


def test_a_missed_window_stays_overdue_until_the_next_count() -> None:
    """The whole point of P-11: the warning does not disappear at midnight on Monday."""
    status = weekly_status(None, _tehran(TUESDAY, 8, 0), due_weekday=MONDAY, due_time=DUE)

    assert status.state == "overdue"
    assert (status.window_start, status.window_end) == THIS_WEEK


def test_a_count_of_the_week_before_does_not_close_this_window() -> None:
    last_week = _tehran(date(2026, 10, 26), 10, 0)

    status = weekly_status(last_week, _tehran(TUESDAY, 8, 0), due_weekday=MONDAY, due_time=DUE)

    assert status.state == "overdue"
    assert status.last_count_at == last_week


def test_the_window_is_read_in_tehran_time_not_the_servers() -> None:
    """20:30 UTC on Monday is already Tuesday in the clinic; the window has not rolled over."""
    moment = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)

    status = weekly_status(None, moment, due_weekday=MONDAY, due_time=DUE)

    assert status.state == "overdue"
    assert (status.window_start, status.window_end) == THIS_WEEK
