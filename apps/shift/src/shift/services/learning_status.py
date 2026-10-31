"""G1: is the manager's card still inside the clinic's learning window (owner decision 7)?

The window opens on the first initial count the app recorded — a flow's baseline or the storekeeper's
store count — and covers that Tehran day and the six after it. This module only reads the log: a gap
inside the window changes a label on the card, never a reward, a stock figure or a rule. The pure date
math stays in `domain/learning.py`; here is the one DB read and the Persian label the card prints.
"""

from dataclasses import dataclass
from datetime import date

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.common.clock import parse_iso, to_tehran
from shift.common.jalali import format_jalali
from shift.domain.event_types import EventType
from shift.domain.learning import is_learning, learning_until

# The two records that open the window: a flow's baseline and the storekeeper's opening store count.
INITIAL_COUNTS = (EventType.HANDOVER_BASELINE_RECORDED, EventType.STOCK_STORE_BASELINED)


@dataclass(frozen=True, slots=True)
class LearningStatus:
    """What the card prints: whether today is a learning day and the last learning day, if any."""

    active: bool
    until_fa: str | None


def first_initial_count(database: Database) -> date | None:
    """The Tehran day of the app's first initial count, or None while the clinic has not counted."""
    earliest: date | None = None
    with database.read() as connection:
        for event in events_repo.iter_events(connection):
            if event.type not in INITIAL_COUNTS:
                continue
            day = to_tehran(parse_iso(event.at_tehran)).date()
            earliest = day if earliest is None else min(earliest, day)
    return earliest


def learning_status(database: Database, today: date) -> LearningStatus:
    """The card's one learning line: active inside the seven days, with the last day in Jalali."""
    since = first_initial_count(database)
    last = learning_until(since)
    return LearningStatus(
        active=is_learning(today, since),
        until_fa=None if last is None else format_jalali(last),
    )
