"""ا — برداشت اضطراری انبار (`stock.emergency_withdrawal`, SHF-3), S01 §3 E-08.

When the storekeeper is not there, two people open the sealed second key: one records the item, the
quantity and the reason, and names the other. The shelf drops at once — the goods have already left
— and the storekeeper confirms the record afterwards in the manager's card (`approvable_by`). That
confirmation changes a status and never the projection: the withdrawal is on the shelf in H-01 `U`
from the moment it was recorded, which is exactly why the confirmation is asked for at all.

A row stored before SHF-3 carries only item, location and quantity, so the payload model keeps
`reason` and `second_person_id` optional and every old row still replays. The missing pair is refused
here instead, and this rule runs for a new record only: the service resolves a replay first.
"""

from typing import Any

from shift.domain.effects import StockOperation, stock_delta
from shift.domain.payloads import EmergencyWithdrawal
from shift.domain.permissions import Permission, Role
from shift.domain.shift_events.base import (
    FieldKind,
    FieldSpec,
    KindGroup,
    KindSpec,
    ShiftEventContext,
    ShiftEventError,
    register,
)

EMERGENCY_WITHDRAWAL = "stock.emergency_withdrawal"

# The questions the phone asks: the reason and the second person open the sealed envelope.
FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("item_id", "قلم", FieldKind.ITEM),
    FieldSpec("location_id", "محل", FieldKind.LOCATION),
    FieldSpec("quantity", "تعداد", FieldKind.QUANTITY, minimum=1),
    FieldSpec("reason", "دلیل", FieldKind.TEXT, max_length=500),
    FieldSpec("second_person_id", "نفر دوم (≠ ثبت‌کننده)", FieldKind.STAFF),
)

# What the phone shows the person whose withdrawal was refused.
NOT_POSITIVE = "تعداد برداشت باید بیشتر از صفر باشد."
UNKNOWN_SHELF = "این قلم در این محل در کاتالوگ نیست."
MISSING_REASON = "دلیل برداشت اضطراری را ثبت کنید."
MISSING_SECOND_PERSON = "نفر دوم را انتخاب کنید؛ برداشت اضطراری دونفره انجام می‌شود."
SAME_AS_ACTOR = "نفر دوم نمی‌تواند همان ثبت‌کننده باشد."
UNKNOWN_SECOND_PERSON = "نفر دوم باید از پرسنل فعال درمانگاه باشد."


def validate(payload: EmergencyWithdrawal, context: ShiftEventContext) -> None:
    """Two different people and a reason, or the envelope was never really opened (E-08)."""
    if payload.quantity <= 0:
        raise ShiftEventError(NOT_POSITIVE)
    if not context.item_at(payload.item_id, payload.location_id):
        raise ShiftEventError(UNKNOWN_SHELF)
    if not (payload.reason or "").strip():
        raise ShiftEventError(MISSING_REASON)
    second_person = (payload.second_person_id or "").strip()
    if not second_person:
        raise ShiftEventError(MISSING_SECOND_PERSON)
    if second_person == context.actor_id:
        raise ShiftEventError(SAME_AS_ACTOR)
    if context.staff_roles(second_person) is None:
        raise ShiftEventError(UNKNOWN_SECOND_PERSON)


def sample() -> dict[str, Any]:
    """A complete example of the kind, for the contract fixtures and the wizard's preview."""
    return {
        "item_id": "item-1",
        "location_id": "location-1",
        "quantity": 2,
        "reason": "قفسه خالی",
        "second_person_id": "staff-2",
    }


def summarize(payload: EmergencyWithdrawal) -> dict[str, Any]:
    """The card row of one withdrawal, the row the storekeeper has to confirm."""
    return {
        "quantity": payload.quantity,
        "item_id": payload.item_id,
        "second_person_id": payload.second_person_id,
        "reason": payload.reason,
    }


KIND = KindSpec(
    event_type=EMERGENCY_WITHDRAWAL,
    code="اض",
    label_fa="برداشت اضطراری انبار",
    group=KindGroup.STOCK,
    fields=FIELDS,
    permission=Permission.STOCK_WITHDRAW_EMERGENCY,
    payload_model=EmergencyWithdrawal,
    effects=stock_delta(StockOperation.SUBTRACT),
    sample=sample,
    summarize=summarize,
    validate=validate,
    approvable_by=(Role.STOREKEEPER,),
    card_fields=("quantity", "item_id", "second_person_id", "reason"),
)

register(KIND)
