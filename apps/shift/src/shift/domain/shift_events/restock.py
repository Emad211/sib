"""و — ورودی وسط شیفت (`stock.shelf_restocked`, SHF-3), S01 §3 E-06.

A box arrives from the store or from a vendor while the shift is running and the reception shelf
grows without anybody counting it: this kind is the app's side of that entry (H-01 `R`). The phone
asks what came, how many and from where; the purchase chain itself — order, delivery, price,
payment — is SHF-4 and is not built here.

A vendor entry without its reference cannot be matched against the invoice later, so it is refused;
the store's own charge-up carries no invoice and may leave the reference empty.

A row stored before SHF-3 carries only item, location and quantity, so the payload model keeps
`source` and `reference` optional and every old row still replays. The missing source is refused
here instead, and this rule runs for a new record only: the service resolves a replay first.
"""

from typing import Any

from shift.domain.effects import StockOperation, stock_delta
from shift.domain.payloads import ShelfRestocked
from shift.domain.permissions import Permission
from shift.domain.shift_events.base import (
    FieldKind,
    FieldSpec,
    KindGroup,
    KindSpec,
    ShiftEventContext,
    ShiftEventError,
    register,
)

SHELF_RESTOCKED = "stock.shelf_restocked"

# What the wizard offers for «از کجا»: the store charges the shelf, a vendor brings an invoice.
SOURCES: tuple[tuple[str, str], ...] = (("store", "انبار"), ("vendor", "فروشنده"))

# The questions the phone asks, in the order the owner document lists them.
FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("item_id", "قلم", FieldKind.ITEM),
    FieldSpec("location_id", "محل", FieldKind.LOCATION),
    FieldSpec("quantity", "تعداد", FieldKind.QUANTITY, minimum=1),
    FieldSpec("source", "منبع", FieldKind.CHOICE, options=SOURCES),
    FieldSpec("reference", "مرجع (فاکتور یا حواله)", FieldKind.TEXT, required=False, max_length=200),
)

# What the phone shows the person whose entry was refused.
NOT_POSITIVE = "تعداد ورود باید بیشتر از صفر باشد."
UNKNOWN_SHELF = "این قلم در این محل در کاتالوگ نیست."
MISSING_SOURCE = "منبع ورود را انتخاب کنید: انبار یا فروشنده."
VENDOR_WITHOUT_REFERENCE = "ورود از فروشنده بدون شمارهٔ فاکتور یا حواله ثبت نمی‌شود."


def validate(payload: ShelfRestocked, context: ShiftEventContext) -> None:
    """Refuse an entry no shelf and no invoice can account for (E-06)."""
    if payload.quantity <= 0:
        raise ShiftEventError(NOT_POSITIVE)
    if not context.item_at(payload.item_id, payload.location_id):
        raise ShiftEventError(UNKNOWN_SHELF)
    if payload.source is None:
        raise ShiftEventError(MISSING_SOURCE)
    if payload.source == "vendor" and not (payload.reference or "").strip():
        raise ShiftEventError(VENDOR_WITHOUT_REFERENCE)


def sample() -> dict[str, Any]:
    """A complete example of the kind, for the contract fixtures and the wizard's preview."""
    return {"item_id": "item-1", "location_id": "location-1", "quantity": 5, "source": "store", "reference": None}


def summarize(payload: ShelfRestocked) -> dict[str, Any]:
    """The card row of one entry: how many, of what, from where and on which document."""
    return {
        "quantity": payload.quantity,
        "item_id": payload.item_id,
        "source": dict(SOURCES).get(payload.source or "", payload.source),
        "reference": payload.reference,
    }


KIND = KindSpec(
    event_type=SHELF_RESTOCKED,
    code="و",
    label_fa="ورودی وسط شیفت",
    group=KindGroup.STOCK,
    fields=FIELDS,
    permission=Permission.STOCK_RESTOCK_CONFIRM,
    payload_model=ShelfRestocked,
    effects=stock_delta(StockOperation.ADD),
    sample=sample,
    summarize=summarize,
    validate=validate,
    card_fields=("quantity", "item_id", "source", "reference"),
)

register(KIND)
