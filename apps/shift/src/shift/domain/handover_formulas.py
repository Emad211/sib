"""Discrepancy arithmetic and the handover error type (H-06, glossary).

The references themselves are not computed here: the projections hold them, because every event that
moves a shelf or the box already carries its own sign (H-01 `E = C0 + R - D - U - W` and H-02 are the
specification, pinned in `tests/data/handover_oracle.py` against the event log). A second formula in
the source would be a second truth. Every value is a whole number; nothing here rounds. The error type
lives here because both the flow and the count chains reject illegal steps.
"""

from dataclasses import dataclass


class HandoverError(ValueError):
    """A caller asked for a transition the clinic's handover rules do not allow."""


def difference(reference: int, counted: int) -> int:
    """How far a final count reads from the reference: positive is a shortage, negative a surplus."""
    return reference - counted


@dataclass(frozen=True, slots=True)
class Discrepancy:
    """A settled count's gap from its reference: the record a manager sees later (H-06).

    A named discrepancy is a warning only and never a wage deduction. An unnamed one
    (`culprit_id` is None) comes from the emergency path and goes to the manager (H-08).
    """

    difference: int
    culprit_id: str | None
