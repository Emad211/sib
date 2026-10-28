"""Deterministic clocks and Tehran instants for tests."""

from datetime import date, datetime, timedelta

from shift.common.clock import TEHRAN
from shift.domain.shift_clock import SHIFT_START_TIMES


class FixedClock:
    """A clock whose instant a test can move between operations."""

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("a test clock needs an aware datetime")
        self.moment = moment

    def now_utc(self) -> datetime:
        """Return the instant the test currently set."""
        return self.moment


def inside_shift(day: date, rank: int) -> datetime:
    """A Tehran instant 30 minutes after `rank` starts, i.e. safely inside that shift."""
    return datetime.combine(day, SHIFT_START_TIMES[rank], tzinfo=TEHRAN) + timedelta(minutes=30)
