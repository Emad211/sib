"""Payloads of the main store's own records: its opening count, a receiver's count, a shelf offer (B1, G3).

The main store is the one place vendor goods enter (owner decision 1): a parcel joins its stock on the
storekeeper's own count at delivery, not on reception's count at the door, and the storekeeper counts the
whole store once before the first blind weekly count has anything to compare with. Every record here
names the person who counted, because the log's actor is that person.

Pure like the rest of the domain: frozen, closed to unknown fields, whole-number quantities.
"""

from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from shift.domain.payloads import Identifier, Note, Payload, Quantity
from shift.domain.purchase_payloads import MAX_LINES, RestockConfirmed

# The main store's location id: no catalog field names it yet, so the domain pins it for every reader.
MAIN_STORE: Final = "main_store"
MAX_STORE_LINES: Final = 500
ReviewDecision = Literal["approved", "rejected", "changed"]
Counted = Quantity


class ConfirmedLine(Payload):
    """One line of the receiver's own count; zero is a real answer ("nothing of this came")."""

    item_id: Identifier
    quantity: Counted


class DeliveryConfirmed(Payload):
    """The storekeeper took the parcel and counted it: these units join the main store (G3, decision 1)."""

    delivery_id: Identifier
    lines: tuple[ConfirmedLine, ...] = Field(min_length=1, max_length=MAX_LINES)


class RestockOffered(RestockConfirmed):
    """The giver's half of a shelf charge: nothing moves until the shelf's own person confirms it (G3)."""


class ShelfReturnConfirmed(RestockConfirmed):
    """Goods back from a shelf to the main store: stock down on the shelf and up in the store at once (A2).

    The fields are the shelf charge's, read the other way round: `from_location_id` is the shelf and
    `to_location_id` the main store. It is the `U` of the shelf reference, so the shelf's next blind
    count is compared with a figure that already excludes what went back.
    """


class ShelfReturnOffered(ShelfReturnConfirmed):
    """The giver's half of a return: nothing moves until the named confirmer counts what arrived (A2)."""


class StoreBaselineLine(Payload):
    item_id: Identifier
    quantity: Counted


class StoreBaselined(Payload):
    """The storekeeper's opening count of the whole main store, recorded once under their name (B1)."""

    lines: tuple[StoreBaselineLine, ...] = Field(min_length=1, max_length=MAX_STORE_LINES)

    @model_validator(mode="after")
    def _each_item_once(self) -> "StoreBaselined":
        if len({line.item_id for line in self.lines}) != len(self.lines):
            raise ValueError("a store baseline names each item once")
        return self


class SampleCounted(Payload):
    """The accountant's blind check of one sampled item; it compares and never sets the stock (B1)."""

    item_id: Identifier
    location_id: Identifier
    counted: Counted
    month: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}$")]


class ManagerReviewed(Payload):
    """The manager's answer on a card row that is not itself an approvable record (C1)."""

    decision: ReviewDecision
    note: Note | None = None
