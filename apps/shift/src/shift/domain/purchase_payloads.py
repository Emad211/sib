"""Validated payloads of the purchasing and warehouse chain (SHF-4, S03 §3).

Every row here is a document, not a stock movement: the goods reach a shelf through the shift event
«و» (`stock.shelf_restocked`), so a delivery, an invoice and a payment never touch `proj_stock`, and no
purchasing payload carries a cash-box figure. Money belongs to accounting; this app records the
accountant's own figures and leaves the clinic box alone (S01 §4).

The shape follows `domain/payloads.py`: frozen, closed to unknown fields, whole-number quantities and
integer Toman — a float or a string price is refused instead of being coerced. `Quantity` here is at
least one, because a line of nothing is a record of no document at all; `PositiveToman` is the strict
floor for a payment or a credit (`Toman` allows the zero unit price of a free item on an invoice).
"""

from datetime import date
from typing import Annotated, Final, Literal

from pydantic import Field

from shift.domain.money import Toman
from shift.domain.payloads import Identifier, Note, Payload, PhotoHash

Quantity = Annotated[int, Field(strict=True, ge=1)]
PositiveToman = Annotated[int, Field(strict=True, gt=0)]
DocumentText = Annotated[str, Field(min_length=1, max_length=200)]
OptionalText = Annotated[str, Field(max_length=200)] | None
MAX_LINES: Final = 50
MAX_NAME: Final = 200

Decision = Literal["approved", "rejected", "changed"]
ProblemKind = Literal["short", "extra", "wrong", "broken"]
Custody = Literal["shift", "storekeeper"]
PaymentMethod = Literal["cash", "card", "transfer"]


class OrderLine(Payload):
    """One requested item of an order: what to buy and where it belongs."""

    item_id: Identifier
    location_id: Identifier
    quantity: Quantity


class OrderDrafted(Payload):
    """The storekeeper's draft; a re-draft after «تغییر» carries the same `order_id` again (P-02)."""

    order_id: Identifier
    lines: tuple[OrderLine, ...] = Field(min_length=1, max_length=MAX_LINES)
    note: Note | None = None


class OrderDecided(Payload):
    """The manager's one decision: approve, reject, or send it back as a fresh draft (P-02)."""

    order_id: Identifier
    decision: Decision
    note: Note | None = None


class OrderPlaced(Payload):
    """The purchaser gave the approved order to a vendor (P-03); the vendor is a role's record."""

    order_id: Identifier
    vendor: Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
    reference: OptionalText = None


class DeliveryLine(Payload):
    """One counted line of a delivery: units only, because reception never types a price (P-04)."""

    item_id: Identifier
    location_id: Identifier
    quantity: Quantity


class DeliveryReceived(Payload):
    """What reception counted at the door against an invoice number; the order is optional (P-04)."""

    delivery_id: Identifier
    order_id: Identifier | None = None
    invoice_reference: DocumentText
    lines: tuple[DeliveryLine, ...] = Field(min_length=1, max_length=MAX_LINES)
    packages: Quantity | None = None
    # The invoice photo is mandatory for a new receipt (the service refuses without it); the field stays
    # optional in the model so a receipt written before the owner's rule still reads back (decision 6).
    invoice_photo_sha256: PhotoHash | None = None


class DeliveryFlagged(Payload):
    """A problem with the parcel; it locks the invoice's payment until the manager decides (P-06)."""

    delivery_id: Identifier
    reason: ProblemKind
    note: Note | None = None


class DeliveryTransferred(Payload):
    """The parcel changes hands between shifts, or reaches the storekeeper who takes custody (P-12)."""

    delivery_id: Identifier
    to_staff_id: Identifier
    to_kind: Custody
    packages: Quantity | None = None


class InvoiceLine(Payload):
    """One digitized invoice line; the server multiplies quantity by unit price (P-07)."""

    item_id: Identifier
    quantity: Quantity
    unit_price_toman: Toman


class InvoiceDigitized(Payload):
    """The accountant's invoice: lines and an optional reference, never a client-typed total (P-07)."""

    delivery_id: Identifier
    lines: tuple[InvoiceLine, ...] = Field(min_length=1, max_length=MAX_LINES)
    reference: OptionalText = None


class PaymentRecorded(Payload):
    """One payment against a digitized invoice; several partial payments are allowed (P-08)."""

    delivery_id: Identifier
    amount_toman: PositiveToman
    method: PaymentMethod
    paid_on: date | None = None
    receipt_reference: OptionalText = None
    receipt_photo_sha256: PhotoHash | None = None


class VendorReturnLine(Payload):
    """One line going back to the vendor; the return hangs off the original delivery (P-09)."""

    item_id: Identifier
    quantity: Quantity


class VendorReturn(Payload):
    """The storekeeper's return record; it is a purchasing document and moves no shelf (P-09)."""

    delivery_id: Identifier
    lines: tuple[VendorReturnLine, ...] = Field(min_length=1, max_length=MAX_LINES)
    reason: Note


class VendorCredit(Payload):
    """The accountant's credit note for a return, capped by the invoice it belongs to (P-09)."""

    delivery_id: Identifier
    amount_toman: PositiveToman
    note: Note | None = None


class RestockConfirmed(Payload):
    """A shelf charged from the main store: one record, stock down there and up here at once (P-10)."""

    item_id: Identifier
    from_location_id: Identifier
    to_location_id: Identifier
    quantity: Quantity
    confirmer_id: Identifier
    # The offer this confirmation closes (G3); a charge recorded in one step before the two-sided rule has none.
    offer_id: Identifier | None = None


class ExpiryFlagged(Payload):
    """An item near expiry seen during the weekly count; the date itself is never stored (P-11)."""

    item_id: Identifier
    location_id: Identifier
    note: Note | None = None
