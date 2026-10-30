"""Payload models for the handover events and their automatic discrepancies (S03 §3)."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from shift.domain.payloads import AmountRaw, Identifier, ItemCounted, Payload, Quantity

CountScope = Literal["cash", "item"]
SignerSide = Literal["incoming", "outgoing"]
DifferenceAmount = Annotated[int, Field(strict=True)]


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


class OutgoingCounted(ItemCounted):
    """The outgoing person's blind third count of one item (H-05, Q-09).

    Emit it only when that blind count reads the reference: the item then matches without a
    joint count and the figure may replace the shelf quantity (S03 §5). On any other answer the
    figure is provisional — emit `RecountRequested` for the joint count and let `JointCounted`
    move the stock, so an unconfirmed figure never becomes the reference.
    """


class JointCounted(CountTarget):
    """The figure both people agree on after counting together (H-05, H-13)."""

    counted: Quantity


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
