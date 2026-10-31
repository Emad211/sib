"""«ب» refund/cancellation request (S01 §3): the app records the request, accounting voids it."""

from typing import Annotated

from pydantic import Field

from shift.domain.effects import no_effects
from shift.domain.payloads import Identifier, Payload
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


class RefundRequested(Payload):
    """An admission whose money should be returned; the doctor or manager approves the request.

    The real void and refund happen in the accounting app (Q-08) and reach the shift's cash
    projection through the read-only bridge, not through this payload.
    """

    admission_no: Identifier
    reason: Annotated[str, Field(min_length=1, max_length=500)]


def _validate(payload: RefundRequested, _context: ShiftEventContext) -> None:
    if not payload.reason.strip():
        raise ShiftEventError("دلیل برگشت پول را بنویسید.")


def _summary(payload: RefundRequested) -> dict[str, object]:
    return {"admission_no": payload.admission_no, "reason": payload.reason}


KIND = KindSpec(
    event_type="refund.requested",
    code="ب",
    label_fa="برگشت پول / ابطال",
    group=KindGroup.MONEY,
    fields=(
        FieldSpec("admission_no", "شماره پذیرش اصلی", FieldKind.TEXT, max_length=64),
        FieldSpec("reason", "دلیل", FieldKind.TEXT, max_length=500),
    ),
    permission=Permission.REFUND_REQUEST,
    payload_model=RefundRequested,
    effects=no_effects,
    sample=lambda: {"admission_no": "A-1002", "reason": "بیمار منصرف شد"},
    summarize=_summary,
    validate=_validate,
    approvable_by=(Role.SHIFT_DOCTOR, Role.MANAGER),
    requires_approval=True,
    card_fields=("admission_no", "reason"),
)

register(KIND)
