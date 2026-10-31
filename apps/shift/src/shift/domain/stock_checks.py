"""The weekly count of the main store: its three-day window and whether it is overdue (P-11).

S01 §4 asks the storekeeper to count the main store blind once a week, on one of the days the clinic
allows, and the manager's card has to warn when a week goes by without it. The manager's
`weekly_count_weekday` setting is the last day the count is still on time and follows `date.weekday()`
(Monday is 0), so the window is that day and the two days before it: Saturday..Monday for the clinic's
Monday. `weekly_count_time` is the clock time that day stops being on time.

A day after the window's last day still belongs to the window that just closed, so the morning after a
missed Monday reads `overdue` until the next count lands, instead of quietly becoming "due" again.

Everything is Tehran time (UTC+03:30, fixed): a count is inside the window when its Tehran date is, and
the deadline is the window's last day at `weekly_count_time`. Pure by design — no clock, no database
and no Persian text; the caller supplies the moment.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from shift.common.clock import TEHRAN, to_tehran

WeeklyState = Literal["done", "due", "overdue"]
WINDOW_DAYS = 3
MAX_WEEKDAY = 6


@dataclass(frozen=True, slots=True)
class WeeklyStatus:
    """One week's answer: whether it is counted, and the window it belongs to."""

    state: WeeklyState
    window_start: date
    window_end: date
    last_count_at: datetime | None


def weekly_window(day: date, *, due_weekday: int) -> tuple[date, date]:
    """The count window `day` belongs to, or the one that closed last: its first and its due day.

    The window ends on `due_weekday` and opens two days before it (Saturday..Monday for a Monday due
    day). A day after that ending falls back to the window that just closed, because the clinic is
    still waiting for that count and the manager's card has to keep saying so.
    """
    if not 0 <= due_weekday <= MAX_WEEKDAY:
        raise ValueError(f"due_weekday must be date.weekday() (0..6), got {due_weekday!r}")
    end = day + timedelta(days=WINDOW_DAYS - 1)
    end -= timedelta(days=(end.weekday() - due_weekday) % 7)
    return end - timedelta(days=WINDOW_DAYS - 1), end


def weekly_status(
    last_count_at: datetime | None,
    moment: datetime,
    *,
    due_weekday: int,
    due_time: time,
) -> WeeklyStatus:
    """Whether the window of `moment` has a count, and whether its deadline has already passed."""
    now = to_tehran(moment)
    start, end = weekly_window(now.date(), due_weekday=due_weekday)
    counted = None if last_count_at is None else to_tehran(last_count_at)
    if counted is not None and start <= counted.date() <= end:
        state: WeeklyState = "done"
    elif now > datetime.combine(end, due_time, tzinfo=TEHRAN):
        state = "overdue"
    else:
        state = "due"
    return WeeklyStatus(state, start, end, counted)
