"""«ک» card-to-card transfer (S01 §3): what reception sent, waiting for the accountant."""

from shift.domain.effects import no_effects
from shift.domain.payloads import Identifier, Payload, Toman
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


class CardTransferReported(Payload):
    """A card-to-card receipt the accountant later matches against the bank statement."""

    amount: Toman
    admission_no: Identifier


def _validate(payload: CardTransferReported, _context: ShiftEventContext) -> None:
    if payload.amount <= 0:
        raise ShiftEventError("مبلغ کارت‌به‌کارت باید بزرگ‌تر از صفر باشد.")


def _summary(payload: CardTransferReported) -> dict[str, object]:
    return {"amount": payload.amount, "admission_no": payload.admission_no}


KIND = KindSpec(
    event_type="card.transfer_reported",
    code="ک",
    label_fa="کارت‌به‌کارت",
    group=KindGroup.MONEY,
    fields=(
        FieldSpec("amount", "مبلغ (تومان)", FieldKind.AMOUNT),
        FieldSpec("admission_no", "شماره پذیرش", FieldKind.TEXT, max_length=64),
    ),
    permission=Permission.CARD_TRANSFER_REPORT,
    payload_model=CardTransferReported,
    effects=no_effects,
    sample=lambda: {"amount": 150_000, "admission_no": "A-1001"},
    summarize=_summary,
    validate=_validate,
    approvable_by=(Role.ACCOUNTANT,),
    card_fields=("amount", "admission_no"),
)

register(KIND)
