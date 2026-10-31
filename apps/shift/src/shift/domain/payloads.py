"""Validated payload models for the projection event vocabulary (schema_v = 1)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from shift.domain.money import Toman

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Quantity = Annotated[int, Field(strict=True, ge=0)]
AmountRaw = Annotated[float, Field(ge=0, allow_inf_nan=False)]
ReversalReason = Literal["reclosed", "voided", "refunded"]
WasteKind = Literal["broken", "expired", "damaged"]
RestockSource = Literal["store", "vendor"]
Note = Annotated[str, Field(max_length=500)]
# The content hash of a receipt photo the server stores (SHF-7): one file per hash and never a path
# in the payload, so an event can only point at a photo the clinic's own store holds.
PhotoHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Payload(BaseModel):
    """Base of every event payload: frozen and closed to unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ShiftOpened(Payload):
    """A flow started; only reception opens the cash box, so nursing carries no amount (H-12)."""

    opening_cash: Toman | None


class CashCounted(Payload):
    counted: Toman


class CashAmount(Payload):
    amount: Toman


class CashWithdrawn(CashAmount):
    """A cash withdrawal: the recipient and reason are required on new records (SHF-3 «د»)."""

    recipient_id: Identifier | None = None
    reason: Note | None = None
    manager_id: Identifier | None = None
    photo_sha256: PhotoHash | None = None


class CashDeposited(CashAmount):
    """Cash taken out of the box to the bank; the accountant records it, the bank slip is optional (B2)."""

    photo_sha256: PhotoHash | None = None


class CashReceipt(CashAmount):
    """A cash receipt copied from accounting; the raw REAL is kept for audit (design §4)."""

    amount_raw: AmountRaw | None = None


class CashReversal(CashAmount):
    """Compensation for a receipt accounting took back: a re-close or a void (S04 §2.1)."""

    reason: ReversalReason
    amount_raw: AmountRaw | None = None


class ItemCounted(Payload):
    item_id: Identifier
    location_id: Identifier
    counted: Quantity


class WeeklyCount(ItemCounted):
    pass


class StockDelta(Payload):
    item_id: Identifier
    location_id: Identifier
    quantity: Quantity


class ShelfRestocked(StockDelta):
    """A mid-shift entry: from the store or from a vendor with an invoice reference (SHF-3 «و»)."""

    source: RestockSource | None = None
    reference: Annotated[str, Field(max_length=200)] | None = None
    photo_sha256: PhotoHash | None = None


class EmergencyWithdrawal(StockDelta):
    """Two people open the sealed envelope; the storekeeper confirms it afterwards (SHF-3)."""

    reason: Note | None = None
    second_person_id: Identifier | None = None


class WasteReported(StockDelta):
    """Confirmed waste: a second person who is not the culprit confirms it (SHF-3 «ض»)."""

    waste_kind: WasteKind | None = None
    culprit_id: Identifier | None = None
    confirmer_id: Identifier | None = None
    photo_sha256: PhotoHash | None = None


class ConsumptionRecorded(StockDelta):
    pass


class PresenceDeclared(Payload):
    """ADR-021: a second receptionist or the shift doctor is on shift from now until this shift ends.

    The role is the bound role being claimed; who and when come from the event's own `actor_id` and
    `client_at`, so the record cannot say one thing and mean another.
    """

    role: Literal["reception", "shift_doctor"]
