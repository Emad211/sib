"""Which shift an accounting fact belongs to, decided by the handover instant (H-11).

An accounting invoice closed after the shift's cutoff was paid after the handover started, so it
belongs to the next shift — the "paper patient" that reception registers late (S02 §3.1). The
cutoff itself is inclusive: a row closed exactly at the cutoff instant is still this shift's.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from shift.common.clock import require_aware


class ShiftSide(StrEnum):
    """Where an accounting fact belongs relative to one shift's cutoff."""

    CURRENT = "current"
    NEXT = "next"


@dataclass(frozen=True, slots=True)
class ShiftCutoff:
    """The instant one handover flow started, frozen there and reused only for that flow (H-11)."""

    shift_id: str
    work_date: date
    cutoff_at: datetime

    def __post_init__(self) -> None:
        if not self.shift_id.strip():
            raise ValueError("shift_id must be non-empty")
        if isinstance(self.work_date, datetime) or not isinstance(self.work_date, date):
            raise ValueError("work_date must be a date")
        require_aware(self.cutoff_at)

    def side_of(self, at: datetime) -> ShiftSide:
        """Classify one accounting timestamp against the cutoff; aware instants compare absolutely."""
        return ShiftSide.CURRENT if require_aware(at) <= self.cutoff_at else ShiftSide.NEXT

    def covers(self, at: datetime) -> bool:
        """True when a fact stamped `at` is counted for this shift."""
        return self.side_of(at) is ShiftSide.CURRENT
