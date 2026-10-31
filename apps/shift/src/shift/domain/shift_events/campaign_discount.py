"""«ت» campaign discount (S01 §3): only a campaign the manager defined may be applied."""

from shift.domain.effects import no_effects
from shift.domain.payloads import Identifier, Payload
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


class CampaignDiscountApplied(Payload):
    """One discount, named from the active campaign calendar and tied to an admission."""

    campaign_id: Identifier
    admission_no: Identifier


def _validate(payload: CampaignDiscountApplied, context: ShiftEventContext) -> None:
    if not context.campaign_active(payload.campaign_id):
        raise ShiftEventError("کمپین انتخاب‌شده فعال نیست.")


def _summary(payload: CampaignDiscountApplied) -> dict[str, object]:
    return {"campaign_id": payload.campaign_id, "admission_no": payload.admission_no}


KIND = KindSpec(
    event_type="campaign.discount_applied",
    code="ت",
    label_fa="تخفیف کمپین",
    group=KindGroup.MONEY,
    fields=(
        FieldSpec("campaign_id", "کمپین", FieldKind.CHOICE),
        FieldSpec("admission_no", "شماره پذیرش", FieldKind.TEXT, max_length=64),
    ),
    permission=Permission.CAMPAIGN_DISCOUNT_APPLY,
    payload_model=CampaignDiscountApplied,
    effects=no_effects,
    sample=lambda: {"campaign_id": "cmp-1", "admission_no": "A-1002"},
    summarize=_summary,
    validate=_validate,
    card_fields=("campaign_id", "admission_no"),
)

register(KIND)
