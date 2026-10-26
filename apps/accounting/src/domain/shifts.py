"""Shift names, order and the clinic's schedule (ACC-2.2, FB-01/FB-02).

A shift is never switched by the clock. The schedule only says which shift a person *starts*
when they take over: morning 07:30, evening 13:30, night 19:30 until 07:30 the next day. A night
shift keeps the work date it started on, even after midnight. Taking over up to
`EARLY_TAKEOVER_MINUTES` before a boundary counts as the next shift; taking over late still
starts the shift whose window we are in. Whoever has not handed over keeps working in their
shift until they do (late handovers happen).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

SHIFT_ORDER = {"morning": 0, "evening": 1, "night": 2}
SHIFT_FA = {"morning": "صبح", "evening": "عصر", "night": "شب"}
SHIFTS = tuple(SHIFT_ORDER)

DEFAULT_STARTS = {"morning": time(7, 30), "evening": time(13, 30), "night": time(19, 30)}
EARLY_TAKEOVER_MINUTES = 30

CURRENT, FINISHING, STALE = "current", "finishing", "stale"


def shift_rank(shift: str | None) -> int:
    """Position inside a work date (unknown/None sorts last)."""
    return SHIFT_ORDER.get(shift or "", len(SHIFT_ORDER))


def shift_label(shift: str | None) -> str:
    return SHIFT_FA.get(shift or "", shift or "")


def sort_key(work_date: str | None, shift: str | None) -> tuple[str, int]:
    """Chronological key for (work_date, shift) pairs."""
    return (work_date or "", shift_rank(shift))


def parse_hhmm(text: str | None, fallback: time) -> time:
    """'07:30' -> time(7, 30); anything unreadable -> fallback."""
    try:
        hours, minutes = (int(part) for part in str(text).strip().split(":"))
        return time(hours, minutes)
    except (TypeError, ValueError):
        return fallback


def scheduled_shift(
    now: datetime, starts: dict[str, time] | None = None, early_minutes: int = EARLY_TAKEOVER_MINUTES
) -> tuple[str, str]:
    """(shift, work_date ISO) that a person taking over at `now` starts."""
    starts = starts or DEFAULT_STARTS
    moment = now + timedelta(minutes=early_minutes)
    t = moment.time()
    if starts["morning"] <= t < starts["evening"]:
        return "morning", moment.date().isoformat()
    if starts["evening"] <= t < starts["night"]:
        return "evening", moment.date().isoformat()
    work_day = moment.date() if t >= starts["night"] else moment.date() - timedelta(days=1)
    return "night", work_day.isoformat()


def previous_shift(shift: str, work_date: str) -> tuple[str, str]:
    """The shift right before (shift, work_date)."""
    day = date.fromisoformat(work_date)
    if shift == "morning":
        return "night", (day - timedelta(days=1)).isoformat()
    if shift == "evening":
        return "morning", work_date
    return "evening", work_date


def standing(stored: tuple[str, str] | None, scheduled: tuple[str, str]) -> str:
    """How a person's stored shift relates to the schedule:

    * CURRENT   - they are in the scheduled shift;
    * FINISHING - they are still in the shift right before (late handover: keep working);
    * STALE     - their stored shift is older (e.g. yesterday's): they must start the scheduled one.
    """
    if not stored or not stored[0] or not stored[1]:
        return STALE
    stored = (stored[0], stored[1][:10])
    if stored == scheduled:
        return CURRENT
    if stored == previous_shift(*scheduled):
        return FINISHING
    return STALE
