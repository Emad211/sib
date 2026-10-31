"""واریز نقد به بانک (`cash.deposited`, B2): cash leaves the box for the bank and the accountant records it.

The owner's rule (decision 3, 2026-10-31): the accountant records the deposit and the bank slip is
optional. The record takes the amount out of the reception box at once (`cash_delta(SUBTRACT)`, the
«W» term of the cash formula), so a deposit never shows up at the next handover as a missing sum.
Which side of a handover's cutoff it falls on is decided by when it **occurred**, not when it arrived
(`cash_cutoff`, H-11), exactly like a withdrawal.

Nobody confirms a deposit: it is the accountant's own record of money she carried to the bank, so the
kind waits for no decision. The photo, when there is one, is only a content hash the photo store holds.
"""

from typing import Any

from shift.domain.effects import CashOperation, cash_delta
from shift.domain.payloads import CashDeposited
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

CASH_DEPOSITED = "cash.deposited"
NOT_POSITIVE = "مبلغ واریز باید بیشتر از صفر باشد."


def validate(payload: CashDeposited, _context: ShiftEventContext) -> None:
    """Refuse a deposit of nothing; the amount model already refuses a negative or fractional one."""
    if payload.amount <= 0:
        raise ShiftEventError(NOT_POSITIVE)


def sample() -> dict[str, Any]:
    return {"amount": 2_000_000, "photo_sha256": None}


def summarize(payload: CashDeposited) -> dict[str, Any]:
    """The card row of one deposit: how much went to the bank and its slip, when there is one."""
    summary: dict[str, Any] = {"amount": payload.amount}
    if payload.photo_sha256 is not None:
        summary["photo_sha256"] = payload.photo_sha256
    return summary


KIND = KindSpec(
    event_type=CASH_DEPOSITED,
    code="وا",
    label_fa="واریز نقد به بانک",
    group=KindGroup.MONEY,
    fields=(
        FieldSpec("amount", "مبلغ واریز (تومان)", FieldKind.AMOUNT, minimum=1),
        FieldSpec("photo_sha256", "عکس فیش بانکی (اختیاری)", FieldKind.PHOTO, required=False),
    ),
    permission=Permission.CASH_DEPOSIT_RECORD,
    payload_model=CashDeposited,
    effects=cash_delta(CashOperation.SUBTRACT),
    sample=sample,
    summarize=summarize,
    validate=validate,
    card_fields=("amount", "photo_sha256"),
)

register(KIND)
