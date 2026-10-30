"""Reference and discrepancy arithmetic for the blind handover (H-01, H-02, glossary).

The references are the numbers the app compares counts against and never shows the operator
(R-02, ADR-011). Every value is a whole number of Toman or whole units; nothing here rounds.
The error type lives here because both the flow and the count chains reject illegal steps.
"""

from dataclasses import dataclass


class HandoverError(ValueError):
    """A caller asked for a transition the clinic's handover rules do not allow."""


def shelf_reference(last_count: int, restocked: int, consumed: int, emergency: int, waste: int) -> int:
    """E = C0 + R - D - U - W (H-01): what the shelf should hold when the handover starts."""
    reference = last_count + restocked - consumed - emergency - waste
    if reference < 0:
        raise ValueError("shelf reference would be negative; check the shift's counts")
    return reference


def cash_reference(opening: int, receipts: int, withdrawals: int, deposits: int) -> int:
    """N = N0 + P_cash - B - W (H-02): what the cash box should hold when the handover starts."""
    reference = opening + receipts - withdrawals - deposits
    if reference < 0:
        raise ValueError("cash reference would be negative; check the shift's cash events")
    return reference


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
