"""Payload models for the handover events and their automatic discrepancies (S03 §3)."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from shift.domain.handover_fsm import HandoverStep
from shift.domain.payloads import AmountRaw, Identifier, ItemCounted, Payload, Quantity

CountScope = Literal["cash", "item"]
SignerSide = Literal["incoming", "outgoing"]
# How a chain reached its figure: a joint recount, two agreeing blind counts, or a blind count that
# already read the reference. Cash settles by the first two, an item chain by `matched` or `joint`.
SettlementBasis = Literal["joint", "agreeing_blind", "matched"]
DifferenceAmount = Annotated[int, Field(strict=True)]
# What the box took in during a flow: any whole number of Toman, negative when money went out.
SettlementCarry = Annotated[int, Field(strict=True)]
_ITEM_BASES = frozenset({"joint", "matched"})


class CountTarget(Payload):
    """What a handover count is about: the cash box or one item on a shelf (S03 §4)."""

    scope: CountScope
    item_id: Identifier | None = None
    location_id: Identifier | None = None

    @model_validator(mode="after")
    def _named_target(self) -> "CountTarget":
        if (self.item_id is None) != (self.location_id is None):
            raise ValueError("item_id and location_id are given together")
        if (self.scope == "item") != (self.item_id is not None):
            raise ValueError("an item scope names item_id and location_id; a cash scope names neither")
        return self


class RecountRequested(CountTarget):
    """Another blind count of the cash box or one item is needed (H-05, H-13)."""


class StepAdvanced(Payload):
    """The flow entered the next step of its ordered walk (H-04); the log never skips one."""

    step: HandoverStep


class OutgoingCounted(ItemCounted):
    """The outgoing person's blind third count of one item (H-05, Q-09).

    It is a record like every other blind count: the chain settles through `CountSettled`, which is
    the only event that writes the shelf. A third count that reads the reference matches without a
    joint count, and the settlement carries the matched figure (Q-09, ADR-017 item 1).
    """


class JointCounted(CountTarget):
    """The figure both people agree on after counting together (H-05, H-13)."""

    counted: Quantity


class CountSettled(CountTarget):
    """A count chain settled on one figure — the only count event that moves a projection (H-05, H-13).

    Every count before this one is a record of what somebody read: a first blind count of 3 must not
    turn a shelf of 10 into 3 before anyone agreed to it, and a cash count alone must not rebase the
    box. So a shelf takes `counted` through this event's `SET`, and a cash chain rebases the box here.

    `basis` says what really happened: `joint` or `agreeing_blind` for cash, `matched` or `joint` for
    an item. `carry` is the cash the box recorded during the flow — after the flow's own cutoff, which
    is the next shift's money because the incoming person takes none — and it only exists for cash: it
    stays on top of the agreed figure (`expected = counted + carry`) and may be negative when a
    withdrawal was recorded during the flow (H-11, ADR-017 items 6 and 9).
    """

    counted: Quantity
    basis: SettlementBasis
    carry: SettlementCarry | None = None

    @model_validator(mode="after")
    def _settles_its_own_scope(self) -> "CountSettled":
        if self.scope == "cash":
            if self.basis not in {"joint", "agreeing_blind"}:
                raise ValueError("a cash chain settles by a joint recount or by two agreeing blind counts")
            if self.carry is None:
                raise ValueError("a cash settlement names the cash the box recorded during the flow")
        else:
            if self.basis not in _ITEM_BASES:
                raise ValueError("an item chain settles on a matched blind count or on a joint count")
            if self.carry is not None:
                raise ValueError("only a cash settlement carries cash")
        return self


class ChecklistAnswered(Payload):
    """One checklist item: a yes/no check, a numeric reading or a photo (S03 §1)."""

    item_id: Identifier
    ok: bool
    reading: AmountRaw | None = None
    photo_id: Identifier | None = None


class ParcelHanded(Payload):
    """A parcel still on its way to the store changes hands between shifts (H-04)."""

    parcel_id: Identifier
    packages: Quantity


class HandoverSigned(Payload):
    """One side confirms the handover on their own device (H-04)."""

    signer: SignerSide


class EarlyLeaveReported(Payload):
    """The outgoing person left before handing over (H-08)."""

    staff_id: Identifier


class EmergencyStartConfirmed(Payload):
    """A manager confirmed by phone that the incoming person may finish alone (H-08)."""

    manager_id: Identifier


class DiscrepancyRecorded(CountTarget):
    """A settled count's gap from its reference; `culprit_id` None means it stays unnamed (H-08)."""

    difference: DifferenceAmount
    culprit_id: Identifier | None = None

    @model_validator(mode="after")
    def _real_gap(self) -> "DiscrepancyRecorded":
        if self.difference == 0:
            raise ValueError("a discrepancy records a non-zero gap")
        return self
