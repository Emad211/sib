"""Shift names and their order (ACC-2.2). Shifts are switched by hand, never derived from the clock;
a night shift that crosses midnight keeps the work date it started on."""

from __future__ import annotations

SHIFT_ORDER = {"morning": 0, "evening": 1, "night": 2}
SHIFT_FA = {"morning": "صبح", "evening": "عصر", "night": "شب"}
SHIFTS = tuple(SHIFT_ORDER)


def shift_rank(shift: str | None) -> int:
    """Position inside a work date (unknown/None sorts last)."""
    return SHIFT_ORDER.get(shift or "", len(SHIFT_ORDER))


def shift_label(shift: str | None) -> str:
    return SHIFT_FA.get(shift or "", shift or "")


def sort_key(work_date: str | None, shift: str | None) -> tuple[str, int]:
    """Chronological key for (work_date, shift) pairs."""
    return (work_date or "", shift_rank(shift))
