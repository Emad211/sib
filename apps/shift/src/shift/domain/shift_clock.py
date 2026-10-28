"""Shift boundaries and the rule that a change starts at the next shift (R-09).

`covering` turns the Tehran wall clock into the shift that is running (or the one that
started last). `effective_from` uses it when no running shift is supplied, so a runtime
change can never fall back to `baseline()` and rewrite shifts that already happened.
"""

from collections.abc import Mapping
from datetime import datetime, time, timedelta

from shift.domain.policy import PolicyError, ShiftKey

SHIFT_START_TIMES: Mapping[int, time] = {1: time(7, 30), 2: time(13, 30), 3: time(19, 30)}


def covering(now: datetime) -> ShiftKey:
    """The shift that started most recently at Tehran wall-clock `now`."""
    local = now.replace(tzinfo=None)
    for rank in reversed(SHIFT_START_TIMES):
        if local.time() >= SHIFT_START_TIMES[rank]:
            return ShiftKey(local.date(), rank)
    return ShiftKey(local.date() - timedelta(days=1), max(SHIFT_START_TIMES))


def effective_from(running_shift: ShiftKey | None, *, now: datetime) -> ShiftKey:
    """First shift a change recorded now applies to: the next one (R-09)."""
    if running_shift == ShiftKey.baseline():
        raise PolicyError("baseline is install-time only; a runtime change needs a real running shift")
    return (running_shift or covering(now)).following()


def require_monotonic(target: ShiftKey, newest: ShiftKey | None) -> ShiftKey:
    """Refuse a version that would start before the newest one and rewrite history."""
    if newest is not None and target < newest:
        raise PolicyError(f"a change cannot start at {target.text} before the newest version at {newest.text}")
    return target
