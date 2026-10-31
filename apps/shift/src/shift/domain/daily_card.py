"""The manager's daily card window: which day a card is for and which days it covers (S01 §6).

S01 §6 and ADR-018: the manager gets a daily card at 07:30, Friday has no card at all, and Saturday's
card covers the two days the Friday gap left behind — Thursday and Friday. The gap is clinic policy,
not a setting a manager could change, so it is a constant here (ADR-018). `datetime.weekday()` counts
Monday as 0, so Friday is 4 and Saturday is 5.

Pure by design: no clock, no database and no Persian text. A caller supplies the Tehran date the run
is for and gets the instants the covered days span, both at Tehran midnight (UTC+03:30) with the
start inclusive and the end exclusive.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from shift.common.clock import TEHRAN

# Bumped only with the stored payload's shape; a stored card stays readable while the shape grows.
# 3 added the shift-event sections, 5 the reward and campaign ones and 6 the purchasing and warehouse ones
# (SHF-4); a stored card of an older schema still reads, with the sections it predates simply empty.
CARD_SCHEMA_VERSION = 6
FRIDAY = 4
SATURDAY = 5
DEFAULT_HORIZON_DAYS = 14


@dataclass(frozen=True, slots=True)
class CardWindow:
    """The days one card is for: its own `card_date` and the days it covers, oldest first."""

    card_date: date
    covered_from: date
    covered_to: date

    @property
    def start(self) -> datetime:
        """Tehran midnight that opens the first covered day (inclusive)."""
        return _midnight(self.covered_from)

    @property
    def end(self) -> datetime:
        """Tehran midnight after the last covered day (exclusive)."""
        return _midnight(self.covered_to + timedelta(days=1))

    @property
    def day_count(self) -> int:
        """How many days the card covers: two on Saturday, which also carries Friday."""
        return (self.covered_to - self.covered_from).days + 1


def window_for(run_day: date) -> CardWindow | None:
    """The card a run on `run_day` produces, or None on Friday (S01 §6, ADR-018).

    Saturday has no Friday card to follow, so its card covers Thursday and Friday — the two days the
    gap left behind. Every other day covers the day before, because that is the day whose work the
    07:30 card reports.
    """
    weekday = run_day.weekday()
    if weekday == FRIDAY:
        return None
    if weekday == SATURDAY:
        return CardWindow(run_day, run_day - timedelta(days=2), run_day - timedelta(days=1))
    yesterday = run_day - timedelta(days=1)
    return CardWindow(run_day, yesterday, yesterday)


def due_card_days(today: date, *, newest: date | None, horizon: int = DEFAULT_HORIZON_DAYS) -> tuple[date, ...]:
    """Every card date a run on `today` still owes, oldest first (S01 §6, ADR-018).

    Today's card is due when today is not Friday and no card for today is stored yet. A run also
    backfills the card dates missed since `newest`, so a restart — or a server that was off at 07:30 —
    leaves no silent gap; Fridays are skipped because they never have a card. The backfill never
    reaches further back than `horizon` days, so a long outage does not replay a month of cards, and
    every date appears once. With no stored card at all (`newest is None`) the history begins today:
    a fresh install gets today's card only, not a fortnight of empty ones.
    """
    if horizon < 1:
        raise ValueError("the backfill horizon is at least one day")
    if newest is None:
        return () if today.weekday() == FRIDAY else (today,)
    first = today - timedelta(days=horizon)
    if newest is not None and newest >= first:
        first = newest + timedelta(days=1)
    missed: list[date] = []
    day = first
    while day < today:
        if day.weekday() != FRIDAY:
            missed.append(day)
        day += timedelta(days=1)
    if today.weekday() != FRIDAY and (newest is None or today > newest):
        missed.append(today)
    return tuple(missed)


def _midnight(day: date) -> datetime:
    """Tehran midnight that starts `day`; the clinic's fixed UTC+03:30 offset has no DST."""
    return datetime.combine(day, time.min, tzinfo=TEHRAN)
