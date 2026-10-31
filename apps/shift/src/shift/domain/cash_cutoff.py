"""Which side of a handover's cutoff a cash event belongs to (H-11), decided by when it occurred.

The clinic's money moves at the counter, not on the network. A phone records a withdrawal at 12:10
and reconnects at 14:00: the cash left the box at 12:10, so a flow whose cutoff is later than that
instant has to see it inside its own counted reference, while a flow that had already settled must
not move a second time when the record finally arrives. Arrival order decides nothing here — only
`occurred_at` against the flow's frozen cutoff.

This module is pure. The service hands it the cash movements of one flow's window — a signed amount
and the instant each occurred — and the flow's cutoff, and takes back the two sums a handover needs:
what belongs inside the counted figure and what stays on top of it as the next shift's money.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shift.common.clock import require_aware
from shift.domain.cutoff import ShiftSide
from shift.domain.effects import CashOperation
from shift.domain.events import effects_for, spec_for


@dataclass(frozen=True, slots=True)
class CashMovement:
    """One cash event as the cutoff sees it: when it happened and how much it moved the box."""

    seq: int
    occurred_at: datetime
    amount: int


@dataclass(frozen=True, slots=True)
class CashWindows:
    """The two sides of one flow's cutoff, each the signed net of the money on that side."""

    before_cutoff: int
    after_cutoff: int
    # The money that left the box after the cutoff, as a positive figure: the manager is told about
    # a withdrawal inside a handover window exactly as about a receipt (H-11).
    outflow: int = 0

    @property
    def carry(self) -> int:
        """The cash that stays on top of the agreed figure: the next shift's money (H-11)."""
        return self.after_cutoff


def side_of(occurred_at: datetime, cutoff: datetime) -> ShiftSide:
    """Where one movement belongs; the cutoff instant itself is still the flow's own (H-11)."""
    return ShiftSide.CURRENT if require_aware(occurred_at) <= require_aware(cutoff) else ShiftSide.NEXT


def signed_cash(event_type: str, payload: Mapping[str, Any], shift_id: str | None) -> int:
    """How much one event moves the reception box: positive in, negative out, zero for everything else.

    The event registry stays the authority for what an event does, so the cutoff keeps no second
    hand-written list of money kinds: a counting event, an opening or a settlement moves no expected
    figure and answers zero.
    """
    spec = spec_for(event_type)
    model = spec.payload_model.model_validate(payload)
    cash = effects_for(event_type, model, shift_id).cash
    if cash is None or cash.operation not in {CashOperation.ADD, CashOperation.SUBTRACT}:
        return 0
    return cash.amount if cash.operation is CashOperation.ADD else -cash.amount


def windows(movements: Iterable[CashMovement], cutoff: datetime) -> CashWindows:
    """Split one flow's cash movements by the side of its cutoff they occurred on."""
    before = 0
    after = 0
    outflow = 0
    for movement in movements:
        if side_of(movement.occurred_at, cutoff) is ShiftSide.CURRENT:
            before += movement.amount
        else:
            after += movement.amount
            if movement.amount < 0:
                outflow -= movement.amount
    return CashWindows(before_cutoff=before, after_cutoff=after, outflow=outflow)
