"""پ، پت، پب — the reward records (S01 §5, SHF-5): earned, confirmed, revoked.

One money story in three kinds. `reward.earned` is written by the service and never typed on a
phone: it names the work accounting already proved (a sugar test, a registration with a valid
national ID and mobile), the rate the manager set for the month the work happened in, and the person
who did it. It waits for the manager — `approvable_by=(Role.MANAGER,)`, closed by SHF-3's generic
`event.approved` / `event.rejected` — and that decision is what makes it payable. `reward.confirmed`
is the manager's credit for an **assigned task**: there the credit *is* the closing confirmation the
owner's document asks for, so it carries no approval (a manager may not approve his own record), and
only `manager_task` may be credited here — a vitals or registration amount must come from the work
itself, never from a form. `reward.revoked` takes a **confirmed** reward back with a required reason:
a pending record is rejected rather than revoked, a rejected one is dead already, and a revoked one
is never revoked twice (`unique_reference`). A revocation may name a confirmed record of an earlier
shift (`cross_shift`), because a phone call about a fake record arrives days later.

None of the three is a phone button (`phone=False`): a reward the earner could record for themselves
would be a self-dealing hole (shf5-DESIGN.md §3). All three carry no projection effect, so a rebuild
stays exactly the two projections the clinic keeps.
"""

from typing import Any, Literal

from shift.domain.effects import no_effects
from shift.domain.money import Toman
from shift.domain.payloads import Identifier, Note, Payload
from shift.domain.permissions import Permission, Role
from shift.domain.rewards import CREDIT_WORK_TYPES, WORK_TYPE_LABELS, WorkType
from shift.domain.shift_events.base import (
    APPROVED,
    FieldKind,
    FieldSpec,
    KindGroup,
    KindSpec,
    ShiftEventContext,
    ShiftEventError,
    register,
)

REWARD_EARNED = "reward.earned"
REWARD_CONFIRMED = "reward.confirmed"
REWARD_REVOKED = "reward.revoked"

# The two accounting feeds a reward may be earned from; a record never mixes them (one source each).
RewardSource = Literal["vitals", "registration"]
SOURCE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("vitals", WORK_TYPE_LABELS[WorkType.VITALS]),
    ("registration", WORK_TYPE_LABELS[WorkType.REGISTRATION]),
)
WORK_TYPE_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (work_type.value, label) for work_type, label in WORK_TYPE_LABELS.items()
)

# What the phone or the manager's screen shows when a reward record is refused. No figure appears.
NOT_POSITIVE = "مبلغ پاداش باید بیشتر از صفر باشد."
EARNED_IS_NO_TASK = "کار ارجاعی مدیر مبلغ ثابت ندارد؛ آن را از «کار ارجاعی» ثبت کنید."
CONFIRMED_IS_A_TASK = "این ثبت فقط برای کار ارجاعی مدیر است."
SOURCE_MISMATCH = "منبع این پاداش با نوع کارش نمی‌خواند."
UNKNOWN_PERSON = "این کارمند در فهرست پرسنل فعال درمانگاه نیست."
SELF_EARNED = "پاداش کار خودتان را نمی‌توانید ثبت کنید؛ این کار مدیر است."
SELF_CONFIRMED = "پاداش کار ارجاعی را نمی‌توانید برای خودتان ثبت کنید؛ کارمند دیگری را انتخاب کنید."
REASON_REQUIRED = "دلیل ابطال پاداش را بنویسید."
NOT_CONFIRMED = "فقط پاداش تأییدشده ابطال می‌شود؛ این ثبت هنوز تأیید نشده است."
NOT_A_REWARD = "این رویداد پاداش نیست و ابطال نمی‌شود."


class RewardEarned(Payload):
    """A work the clinic can verify: who did it, which work, and the rate of its own month."""

    staff_id: Identifier
    work_type: WorkType
    amount_toman: Toman
    source: RewardSource
    source_ref: Identifier
    note: Note | None = None


class RewardConfirmed(Payload):
    """The manager's credit for an assigned task he closed; the credit confirms it by itself."""

    staff_id: Identifier
    work_type: WorkType
    amount_toman: Toman
    note: Note | None = None


class RewardRevoked(Payload):
    """A confirmed reward taken back; the amount to deduct is the referenced record's own amount."""

    reason: Note | None = None


def _validate_earned(payload: RewardEarned, context: ShiftEventContext) -> None:
    """A reward is a real work of a real person, priced by the feed it came from and never a task."""
    if payload.amount_toman <= 0:
        raise ShiftEventError(NOT_POSITIVE)
    if payload.work_type is WorkType.MANAGER_TASK:
        raise ShiftEventError(EARNED_IS_NO_TASK)
    if payload.source != payload.work_type.value:
        raise ShiftEventError(SOURCE_MISMATCH)
    if context.staff_roles(payload.staff_id) is None:
        raise ShiftEventError(UNKNOWN_PERSON)
    if payload.staff_id == context.actor_id:
        raise ShiftEventError(SELF_EARNED)


def _validate_confirmed(payload: RewardConfirmed, context: ShiftEventContext) -> None:
    """Only an assigned task or the follow-up path is credited by the manager, never himself."""
    if payload.amount_toman <= 0:
        raise ShiftEventError(NOT_POSITIVE)
    if payload.work_type not in CREDIT_WORK_TYPES:
        raise ShiftEventError(CONFIRMED_IS_A_TASK)
    if context.staff_roles(payload.staff_id) is None:
        raise ShiftEventError(UNKNOWN_PERSON)
    if payload.staff_id == context.actor_id:
        raise ShiftEventError(SELF_CONFIRMED)


def _validate_revoked(payload: RewardRevoked, context: ShiftEventContext) -> None:
    """A revocation needs a reason and a target that is already confirmed and still in force."""
    if not str(payload.reason or "").strip():
        raise ShiftEventError(REASON_REQUIRED)
    target = context.target_event
    if target is None or target.type not in (REWARD_EARNED, REWARD_CONFIRMED):
        raise ShiftEventError(NOT_A_REWARD)
    if target.type == REWARD_EARNED and context.decision_for(target.event_id) != APPROVED:
        raise ShiftEventError(NOT_CONFIRMED)


def _summary(payload: RewardEarned | RewardConfirmed | RewardRevoked) -> dict[str, Any]:
    """The card row of one reward record: the work, its amount and the person, never a work id."""
    if isinstance(payload, RewardRevoked):
        return {"reason": payload.reason}
    return {
        "staff_id": payload.staff_id,
        "work_type": WORK_TYPE_LABELS[payload.work_type],
        "amount_toman": payload.amount_toman,
        "note": payload.note,
    }


_EARNED_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("staff_id", "کارمند", FieldKind.STAFF),
    FieldSpec("work_type", "نوع کار", FieldKind.CHOICE, options=WORK_TYPE_OPTIONS),
    FieldSpec("amount_toman", "مبلغ (تومان)", FieldKind.AMOUNT, minimum=1),
    FieldSpec("source", "منبع", FieldKind.CHOICE, options=SOURCE_OPTIONS),
    FieldSpec("source_ref", "شناسهٔ کار در حسابداری", FieldKind.TEXT),
    FieldSpec("note", "یادداشت (اختیاری)", FieldKind.TEXT, required=False, max_length=500),
)
_CONFIRMED_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("staff_id", "کارمند", FieldKind.STAFF),
    FieldSpec("work_type", "نوع کار", FieldKind.CHOICE, options=WORK_TYPE_OPTIONS),
    FieldSpec("amount_toman", "مبلغ (تومان)", FieldKind.AMOUNT, minimum=1),
    FieldSpec("note", "یادداشت (اختیاری)", FieldKind.TEXT, required=False, max_length=500),
)

KINDS: tuple[KindSpec, ...] = (
    KindSpec(
        event_type=REWARD_EARNED,
        code="پ",
        label_fa="پاداش کار",
        group=KindGroup.MONEY,
        fields=_EARNED_FIELDS,
        permission=Permission.REWARD_GRANT,
        payload_model=RewardEarned,
        effects=no_effects,
        sample=lambda: {
            "staff_id": "staff-1",
            "work_type": "vitals",
            "amount_toman": 50_000,
            "source": "vitals",
            "source_ref": "9001",
        },
        summarize=_summary,
        validate=_validate_earned,
        approvable_by=(Role.MANAGER,),
        beneficiary_field="staff_id",
        card_fields=("staff_id", "work_type", "amount_toman", "note"),
        phone=False,
    ),
    KindSpec(
        event_type=REWARD_CONFIRMED,
        code="پت",
        label_fa="پاداش کار ارجاعی",
        group=KindGroup.MONEY,
        fields=_CONFIRMED_FIELDS,
        permission=Permission.REWARD_GRANT,
        payload_model=RewardConfirmed,
        effects=no_effects,
        sample=lambda: {
            "staff_id": "staff-1",
            "work_type": "manager_task",
            "amount_toman": 80_000,
            "note": "تعمیر پیگیری شد",
        },
        summarize=_summary,
        validate=_validate_confirmed,
        beneficiary_field="staff_id",
        card_fields=("staff_id", "work_type", "amount_toman", "note"),
        phone=False,
    ),
    KindSpec(
        event_type=REWARD_REVOKED,
        code="پب",
        label_fa="ابطال پاداش",
        group=KindGroup.MONEY,
        fields=(FieldSpec("reason", "دلیل ابطال", FieldKind.TEXT, max_length=500),),
        permission=Permission.REWARD_GRANT,
        payload_model=RewardRevoked,
        effects=no_effects,
        sample=lambda: {"reason": "ثبت جعلی بود"},
        summarize=_summary,
        validate=_validate_revoked,
        needs_reference=True,
        reference_types=(REWARD_EARNED, REWARD_CONFIRMED),
        unique_reference=True,
        cross_shift=True,
        card_fields=("reason",),
        phone=False,
    ),
)

register(*KINDS)
