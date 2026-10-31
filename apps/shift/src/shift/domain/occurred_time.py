"""How old, or how new, a record from a phone may be before the manager has to look at it (F5, ADR-016).

A phone works offline for a whole shift and may even cross midnight, so the server never refuses a
record for being "late": it is attributed by the instant it happened and the received time is kept
next to it. Two cases are not trustworthy enough to apply on their own, though — an instant in the
future (a handset whose clock ran ahead) and an instant older than three days (a queue that sat in a
drawer) — and those records are held for the manager instead of being dropped or silently applied.

This module is pure: it answers one verdict and the Persian line the manager reads with it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from shift.common.clock import require_aware

# A handset may run a little ahead of the server; a few minutes are not worth a manager's attention.
FUTURE_TOLERANCE = timedelta(minutes=10)
# Three days: long enough for a weekend queue, short enough that an old record is worth a look.
MAX_AGE = timedelta(hours=72)


class OccurredVerdict(StrEnum):
    """What the server may do with a record stamped at this instant."""

    OK = "ok"
    FUTURE = "future"
    STALE = "stale"


# R-15 (ADR-021): a record whose own instant falls outside the recorder's shift is kept for the manager.
OFF_SHIFT_REASON = "off_shift"

# The manager's line for a held record; it names no figure, like every message the phone shows.
HELD_MESSAGES: dict[str, str] = {
    OccurredVerdict.FUTURE.value: "زمان ثبت این رویداد در آینده است؛ تا بررسی مدیر نگه داشته شد و هنوز روی صندوق یا قفسه اثر نکرده است.",
    OccurredVerdict.STALE.value: "این رویداد بیش از ۷۲ ساعت پیش ثبت شده است؛ تا بررسی مدیر نگه داشته شد و هنوز روی صندوق یا قفسه اثر نکرده است.",
    OFF_SHIFT_REASON: "این رویداد در زمانی ثبت شده که ثبت‌کننده در شیفت نبوده است؛ تا بررسی مدیر نگه داشته شد و هنوز روی صندوق یا قفسه اثر نکرده است.",
}


def verdict(occurred_at: datetime, now: datetime) -> OccurredVerdict:
    """Classify one record by its own instant: applicable now, or held for the manager."""
    occurred = require_aware(occurred_at)
    moment = require_aware(now)
    if occurred > moment + FUTURE_TOLERANCE:
        return OccurredVerdict.FUTURE
    if occurred < moment - MAX_AGE:
        return OccurredVerdict.STALE
    return OccurredVerdict.OK


@dataclass(frozen=True, slots=True)
class HeldRecord:
    """One held record as the manager's card and the release route need it."""

    held_event_id: str
    event_id: str
    type: str
    reason: str
    message_fa: str
    occurred_at: str
    received_at: str
    actor_id: str
    decided: bool
