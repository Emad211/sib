"""ض — ضایعات (`stock.waste_reported`, SHF-3): stock leaves the shelf and two people are named.

The owner's rule: the item, the quantity, the kind of damage (broken / expired / damaged), the culprit,
and a second person to confirm it who is **not** the culprit. The recorder already counts as one
signature, so the recorder cannot be that second person either, and an item the catalog does not hold
on that shelf is a typo, not a loss. The projection subtracts the quantity from the shelf at once — the
goods have already left — and the named person confirms the record afterwards: `confirmer_field` makes
them the one decider of this kind (H-01, `stock_delta(SUBTRACT)`).

A row stored before SHF-3 carries only the item, the shelf and the quantity, so the payload model keeps
`waste_kind`, `culprit_id` and `confirmer_id` optional and every old row still replays. The three
required answers are refused here instead, which runs for a new record only: the service answers a
replay before it asks this rule.
"""

from typing import Any

from shift.domain.effects import StockOperation, stock_delta
from shift.domain.payloads import WasteKind, WasteReported
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

STOCK_WASTE_REPORTED = "stock.waste_reported"

# The three kinds of damage the owner document names, in its order; `WasteKind` in the payload is the
# guard that no other value ever reaches the projection.
WASTE_KINDS: tuple[tuple[str, str], ...] = (
    ("broken", "شکسته"),
    ("expired", "تاریخ‌گذشته"),
    ("damaged", "خراب"),
)
WASTE_KIND_LABELS: dict[str, str] = dict(WASTE_KINDS)

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("item_id", "قلم", FieldKind.ITEM),
    FieldSpec("location_id", "محل", FieldKind.LOCATION),
    FieldSpec("quantity", "تعداد", FieldKind.QUANTITY, minimum=1),
    FieldSpec("waste_kind", "نوع", FieldKind.CHOICE, options=WASTE_KINDS),
    FieldSpec("culprit_id", "مقصر", FieldKind.STAFF),
    FieldSpec("confirmer_id", "تأییدکننده (≠ مقصر)", FieldKind.STAFF),
    FieldSpec("photo_sha256", "عکس (اختیاری)", FieldKind.PHOTO, required=False),
)

# What the phone shows the person whose record was refused. No figure appears in any of them.
NOT_POSITIVE = "تعداد ضایعات باید بیشتر از صفر باشد."
NOT_ON_THAT_SHELF = "این قلم در این محل در کاتالوگ نیست؛ محل درست را انتخاب کنید."
MISSING_CONFIRMATION = "نوع، مقصر و تأییدکنندهٔ ضایعات را وارد کنید."
CONFIRMER_IS_CULPRIT = "مقصر نمی‌تواند تأییدکنندهٔ همان ضایعات باشد."
CONFIRMER_IS_RECORDER = "ثبت‌کنندهٔ ضایعات نمی‌تواند تأییدکنندهٔ آن باشد."
UNKNOWN_CULPRIT = "مقصر باید از کارکنان فعال درمانگاه باشد."
UNKNOWN_CONFIRMER = "تأییدکننده باید از کارکنان فعال درمانگاه باشد."


def validate(payload: WasteReported, context: ShiftEventContext) -> None:
    """Refuse waste nobody can account for, before anything is appended or subtracted."""
    if payload.quantity <= 0:
        raise ShiftEventError(NOT_POSITIVE)
    if not context.item_at(payload.item_id, payload.location_id):
        raise ShiftEventError(NOT_ON_THAT_SHELF)
    if payload.waste_kind is None or payload.culprit_id is None or payload.confirmer_id is None:
        raise ShiftEventError(MISSING_CONFIRMATION)
    if payload.confirmer_id == payload.culprit_id:
        raise ShiftEventError(CONFIRMER_IS_CULPRIT)
    if payload.confirmer_id == context.actor_id:
        raise ShiftEventError(CONFIRMER_IS_RECORDER)
    if context.staff_roles(payload.culprit_id) is None:
        raise ShiftEventError(UNKNOWN_CULPRIT)
    if context.staff_roles(payload.confirmer_id) is None:
        raise ShiftEventError(UNKNOWN_CONFIRMER)


def sample() -> dict[str, Any]:
    """A complete example of the kind, for the contract fixtures and the wizard's preview."""
    return {
        "item_id": "item-1",
        "location_id": "location-1",
        "quantity": 2,
        "waste_kind": "broken",
        "culprit_id": "staff-2",
        "confirmer_id": "staff-3",
        "photo_sha256": None,
    }


def summarize(payload: WasteReported) -> dict[str, Any]:
    """The card row of one waste: what left the shelf, how much and why, and who confirmed it."""
    waste_kind: WasteKind | None = payload.waste_kind
    summary: dict[str, Any] = {
        "quantity": payload.quantity,
        "item_id": payload.item_id,
        "waste_kind": WASTE_KIND_LABELS.get(waste_kind) if waste_kind is not None else None,
        "confirmer_id": payload.confirmer_id,
    }
    if payload.photo_sha256 is not None:
        summary["photo_sha256"] = payload.photo_sha256
    return summary


KIND = KindSpec(
    event_type=STOCK_WASTE_REPORTED,
    code="ض",
    label_fa="ضایعات",
    group=KindGroup.STOCK,
    fields=FIELDS,
    permission=Permission.STOCK_WASTE_REPORT,
    payload_model=WasteReported,
    effects=stock_delta(StockOperation.SUBTRACT),
    sample=sample,
    summarize=summarize,
    validate=validate,
    confirmer_field="confirmer_id",
    card_fields=("quantity", "item_id", "waste_kind", "confirmer_id", "photo_sha256"),
)

register(KIND)
