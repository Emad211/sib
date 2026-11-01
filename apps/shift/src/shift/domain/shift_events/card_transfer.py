"""«ک» card-to-card transfer (S01 §3): what reception sent, waiting for the accountant."""

from shift.domain.effects import no_effects
from shift.domain.payloads import Identifier, Payload, PhotoHash, Toman
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
    # The bank receipt the accountant matches against; optional, and only ever a content hash.
    photo_sha256: PhotoHash | None = None


def _validate(payload: CardTransferReported, _context: ShiftEventContext) -> None:
    if payload.amount <= 0:
        raise ShiftEventError("مبلغ کارت‌به‌کارت باید بزرگ‌تر از صفر باشد.")


def _summary(payload: CardTransferReported) -> dict[str, object]:
    """The card row, with the receipt's hash only when the person actually attached one."""
    summary: dict[str, object] = {"amount": payload.amount, "admission_no": payload.admission_no}
    if payload.photo_sha256 is not None:
        summary["photo_sha256"] = payload.photo_sha256
    return summary


KIND = KindSpec(
    event_type="card.transfer_reported",
    code="ک",
    label_fa="کارت‌به‌کارت",
    group=KindGroup.MONEY,
    fields=(
        FieldSpec("amount", "مبلغ (تومان)", FieldKind.AMOUNT),
        FieldSpec("admission_no", "شماره پذیرش", FieldKind.TEXT, max_length=64),
        FieldSpec("photo_sha256", "عکس رسید", FieldKind.PHOTO),
    ),
    permission=Permission.CARD_TRANSFER_REPORT,
    payload_model=CardTransferReported,
    effects=no_effects,
    sample=lambda: {"amount": 150_000, "admission_no": "A-1001", "photo_sha256": None},
    summarize=_summary,
    validate=_validate,
    photo_required=True,
    approvable_by=(Role.ACCOUNTANT,),
    card_fields=("amount", "admission_no", "photo_sha256"),
)

register(KIND)
