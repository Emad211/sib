"""د — برداشت از صندوق (`cash.withdrawn`, SHF-3): the box loses money and one person answers for it.

The owner's rule: the phone asks the recipient, the reason and the amount, and up to
`withdrawal_cap_toman` the recipient's own agreement carries the record; above the cap only a named
manager does, and that name must hold the manager role — a name is not an approval. The rule reads the
catalog and the shift's settings through `ShiftEventContext`; the projection takes the amount out of
the reception box at once (H-02, `cash_delta(SUBTRACT)`).

The money has moved, so the recipient's confirmation is recorded after the fact and never delays the
projection: `confirmer_field="recipient_id"` makes the «گیرنده» the named decider of this kind. No
role is named in `approvable_by`, and the recorder never confirms their own record: when the recipient
recorded the withdrawal themselves, any other staff member answers for it, while the manager may
always decide an open record.

A row stored before SHF-3 carries only the amount, so the payload model keeps `recipient_id`, `reason`
and `manager_id` optional and every old row still replays. The missing pair is refused here instead,
which runs for a new record only: the service resolves a replay before it asks this rule.
"""

from collections.abc import Mapping
from typing import Any

from shift.domain.effects import CashOperation, cash_delta
from shift.domain.payloads import CashWithdrawn
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

CASH_WITHDRAWN = "cash.withdrawn"

# The questions the phone asks, in the order the owner document lists them; the manager is last
# because only an over-cap withdrawal has one.
FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("amount", "مبلغ (تومان)", FieldKind.AMOUNT, minimum=1),
    FieldSpec("recipient_id", "گیرنده", FieldKind.STAFF),
    FieldSpec("reason", "دلیل", FieldKind.TEXT, max_length=500),
    FieldSpec("manager_id", "مدیر تأییدکننده (بالای سقف)", FieldKind.STAFF, required=False),
)

# What the phone shows the person whose record was refused. No figure appears in any of them.
MISSING_RECIPIENT_AND_REASON = "گیرنده و دلیل برداشت را وارد کنید."
UNKNOWN_RECIPIENT = "گیرندهٔ برداشت باید از کارکنان فعال درمانگاه باشد."
NOT_POSITIVE = "مبلغ برداشت باید بیشتر از صفر باشد."
ABOVE_CAP_WITHOUT_MANAGER = "برداشت بالای سقف بدون تأیید مدیر ثبت نمی‌شود."
MANAGER_AT_OR_BELOW_CAP = "برداشت تا سقف نیازی به تأیید مدیر ندارد."


def is_above_cap(amount: int, already_withdrawn: int, cap: int) -> bool:
    """Whether one withdrawal takes the shift's running total over the box's cap (ADR-019).

    The owner decided the cap counts the shift's withdrawals together, not each record alone, so four
    small withdrawals cannot empty the box without a manager. A withdrawal counted here is one this
    shift already recorded, by **its own occurred shift**: an offline queue crossing midnight still
    counts against the shift it happened in. The projection effect stays immediate either way, because
    the cash really left the box and waiting for an approval would create a false gap.
    """
    return already_withdrawn + amount > cap


def is_above_cap_payload(payload: Mapping[str, Any]) -> bool:
    """Whether a stored withdrawal was recorded above the cap, which is why it names a manager."""
    return payload.get("manager_id") is not None


def validate(payload: CashWithdrawn, context: ShiftEventContext) -> None:
    """Refuse a withdrawal the clinic cannot stand behind, before anything is appended."""
    if payload.recipient_id is None or payload.reason is None:
        raise ShiftEventError(MISSING_RECIPIENT_AND_REASON)
    if context.staff_roles(payload.recipient_id) is None:
        raise ShiftEventError(UNKNOWN_RECIPIENT)
    if payload.amount <= 0:
        raise ShiftEventError(NOT_POSITIVE)
    if is_above_cap(payload.amount, context.withdrawn_in_shift(), context.withdrawal_cap):
        approver = None if payload.manager_id is None else context.staff_roles(payload.manager_id)
        if approver is None or Role.MANAGER.value not in approver:
            raise ShiftEventError(ABOVE_CAP_WITHOUT_MANAGER)
    elif payload.manager_id is not None:
        raise ShiftEventError(MANAGER_AT_OR_BELOW_CAP)


def sample() -> dict[str, Any]:
    """A complete example of the kind, for the contract fixtures and the wizard's preview."""
    return {"amount": 100000, "recipient_id": "staff-2", "reason": "خرید فوری", "manager_id": None}


def summarize(payload: CashWithdrawn) -> dict[str, Any]:
    """The card row of one withdrawal: how much left the box, to whom and why."""
    return {"amount": payload.amount, "recipient_id": payload.recipient_id, "reason": payload.reason}


KIND = KindSpec(
    event_type=CASH_WITHDRAWN,
    code="د",
    label_fa="برداشت از صندوق",
    group=KindGroup.MONEY,
    fields=FIELDS,
    permission=Permission.CASH_WITHDRAW_REQUEST,
    payload_model=CashWithdrawn,
    effects=cash_delta(CashOperation.SUBTRACT),
    sample=sample,
    summarize=summarize,
    validate=validate,
    confirmer_field="recipient_id",
    # An above-cap withdrawal is decided by the manager afterwards, from their own session; the
    # record already moved the box (ADR-019). A below-cap one names no manager and waits for nobody.
    approvable_by=(Role.MANAGER,),
    manager_only_when=is_above_cap_payload,
    card_fields=("amount", "recipient_id", "reason"),
)

register(KIND)
