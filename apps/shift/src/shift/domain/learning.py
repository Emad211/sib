"""Learning mode: the first seven days after the initial count (owner decision 7, G1).

A gap found while the clinic is still learning its own shelves is shown to the manager as learning, not as
a failure of the person who counted. The window opens on the first initial count the app recorded (a flow's
baseline or the storekeeper's store count) and covers that Tehran day and the six after it. It changes a
label on the card and nothing else: no reward, no stock and no rule reads it.
"""

from datetime import date, timedelta

LEARNING_DAYS = 7


def learning_until(first_initial_count: date | None) -> date | None:
    """The last Tehran day that still counts as learning, or None while no initial count exists."""
    if first_initial_count is None:
        return None
    return first_initial_count + timedelta(days=LEARNING_DAYS - 1)


def is_learning(day: date, first_initial_count: date | None) -> bool:
    """Whether a gap that happened on `day` falls inside the learning window."""
    last = learning_until(first_initial_count)
    return first_initial_count is not None and last is not None and first_initial_count <= day <= last
