"""The two internal cash records of a handover flow (H-11, SHF-3 finding 1).

Both exist because the clinic's money moves at the counter while the network may not: one explains
why a flow's frozen reference moved while it was being counted, the other cancels a cash record that
only reached the server after its flow had already settled. Neither is a staff button — the flywheel
of the phone never shows them — and neither changes what a person sees on a shelf or in the box
without the other event that caused it.
"""

from datetime import datetime
from typing import Annotated

from pydantic import Field, model_validator

from shift.domain.payloads import Identifier, Payload


class CashReferenceAdjusted(Payload):
    """A pre-cutoff cash event arrived during the flow, so its frozen reference moved (H-11).

    The delta is signed: a withdrawal that occurred before the cutoff takes money out of the figure
    the count compares against, a receipt puts it in. The event carries no projection effect; it is
    the flow's own record of why the number it compared against is not the number it froze.
    """

    delta: Annotated[int, Field(strict=True)]

    @model_validator(mode="after")
    def _real_adjustment(self) -> "CashReferenceAdjusted":
        if self.delta == 0:
            raise ValueError("an adjustment records a non-zero delta")
        return self


class LateCashRecorded(Payload):
    """A cash event occurred before a settled flow's cutoff but reached the server after it (H-11).

    `amount` is the late event's own signed change to the box: negative when money left, positive
    when it came in. This record's effect is the opposite of it, so the box keeps the figure the
    flow settled on and the manager reads the late arrival off the card instead of a false gap.
    """

    handover_id: Identifier
    amount: Annotated[int, Field(strict=True)]
    occurred_at: datetime

    @model_validator(mode="after")
    def _real_movement(self) -> "LateCashRecorded":
        if self.amount == 0:
            raise ValueError("a late cash record names a non-zero movement")
        return self
