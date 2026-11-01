"""The equipment fault chain (S01 §3 «خرابی»): one incident, three notes — report, assign, fix.

A member of staff reports what is broken, the manager assigns it to a person with a deadline, and that
person — or the manager — closes it with «تعمیر شد». None of the three moves a shelf or the cash box,
and none is an approval: a fault is closed by the fix, so a report never waits in the approval queue and
the manager's card can show it as open until somebody records the repair.

The chain is one incident, so the assignment and the fix both reference the report rather than each
other; `unique_reference` makes the second fix of the same fault a refusal, not a second closing.

A fault outlives the shift that found it: the manager assigns it and the assignee closes it whenever
they are on duty, so both notes declare `cross_shift` and may point at a report of an earlier shift.
"""

from datetime import date
from typing import Annotated, Any

from pydantic import Field

from shift.domain.effects import no_effects
from shift.domain.payloads import Identifier, Payload, PhotoHash
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

REPORTED = "equipment.fault_reported"
ASSIGNED = "equipment.fault_assigned"
FIXED = "equipment.fault_fixed"
CODE = "خ"
MANAGER = Role.MANAGER.value

Device = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(min_length=1, max_length=1000)]
FixNote = Annotated[str, Field(max_length=500)]


class EquipmentFaultReported(Payload):
    """What is broken, where it stands and what a person sees: the note the whole chain hangs on."""

    device: Device
    description: Description
    location_id: Identifier | None = None
    # A photo of the broken device; optional, and only ever a content hash (SHF-7).
    photo_sha256: PhotoHash | None = None


class FaultAssigned(Payload):
    """The manager hands one fault to one person, with the day it should be working again."""

    assignee_id: Identifier
    due_on: date


class FaultFixed(Payload):
    """The assignee's word that it works again; the note is optional because the fix is the record."""

    note: FixNote | None = None


def _validate_assignment(payload: FaultAssigned, ctx: ShiftEventContext) -> None:
    """A job is given to somebody on the roster, by a day still ahead, for a fault still open."""
    if ctx.staff_roles(payload.assignee_id) is None:
        raise ShiftEventError("ارجاع فقط به یک کارمند فعال فهرست پرسنل ممکن است.")
    if payload.due_on < ctx.shift_work_date:
        raise ShiftEventError("مهلت نمی‌تواند پیش از روز شیفت باشد.")
    if ctx.ref_event_id is not None and ctx.fixed_for(ctx.ref_event_id):
        raise ShiftEventError("این خرابی قبلاً تعمیر شده است و ارجاع دوباره ندارد.")


def _validate_fix(_payload: FaultFixed, ctx: ShiftEventContext) -> None:
    """The job belongs to the person it was given to; the manager may close it either way."""
    if MANAGER in ctx.actor_roles:
        return
    assignee = None if ctx.ref_event_id is None else ctx.assignee_for(ctx.ref_event_id)
    if assignee is None:
        raise ShiftEventError("این خرابی هنوز به کسی ارجاع نشده؛ فقط مدیر می‌تواند آن را ببندد.")
    if assignee != ctx.actor_id:
        raise ShiftEventError("فقط فرد ارجاع‌شده یا مدیر می‌تواند «تعمیر شد» را ثبت کند.")


def _sample_report() -> dict[str, Any]:
    return {
        "device": "دستگاه فشار",
        "description": "صفحه نمایش روشن نمی‌شود",
        "location_id": "location-1",
        "photo_sha256": None,
    }


def _sample_assignment() -> dict[str, Any]:
    return {"assignee_id": "staff-2", "due_on": "2026-11-02"}


def _sample_fix() -> dict[str, Any]:
    return {"note": "دستگاه تعمیر و آزمایش شد"}


def _summarise_report(payload: EquipmentFaultReported) -> dict[str, Any]:
    summary: dict[str, Any] = {"device": payload.device, "description": payload.description}
    if payload.photo_sha256 is not None:
        summary["photo_sha256"] = payload.photo_sha256
    return summary


def _summarise_assignment(payload: FaultAssigned) -> dict[str, Any]:
    return {"assignee_id": payload.assignee_id, "due_on": payload.due_on.isoformat()}


def _summarise_fix(payload: FaultFixed) -> dict[str, Any]:
    return {"note": payload.note}


FAULT_REPORTED = KindSpec(
    event_type=REPORTED,
    code=CODE,
    label_fa="خرابی تجهیزات",
    group=KindGroup.EQUIPMENT,
    fields=(
        FieldSpec("device", "تجهیز", FieldKind.TEXT, max_length=200),
        FieldSpec("description", "شرح خرابی", FieldKind.TEXT, max_length=1000),
        FieldSpec("location_id", "محل", FieldKind.LOCATION, required=False),
        FieldSpec("photo_sha256", "عکس خرابی", FieldKind.PHOTO),
    ),
    permission=Permission.EQUIPMENT_FAULT_REPORT,
    payload_model=EquipmentFaultReported,
    effects=no_effects,
    sample=_sample_report,
    summarize=_summarise_report,
    card_fields=("device", "description", "photo_sha256"),
    photo_required=True,
)

FAULT_ASSIGNED = KindSpec(
    event_type=ASSIGNED,
    code=CODE,
    label_fa="ارجاع خرابی",
    group=KindGroup.EQUIPMENT,
    fields=(
        FieldSpec("assignee_id", "ارجاع به", FieldKind.STAFF),
        FieldSpec("due_on", "مهلت", FieldKind.DATE),
    ),
    permission=Permission.FAULT_ASSIGN,
    payload_model=FaultAssigned,
    effects=no_effects,
    sample=_sample_assignment,
    summarize=_summarise_assignment,
    validate=_validate_assignment,
    needs_reference=True,
    reference_types=(REPORTED,),
    cross_shift=True,
    card_fields=("assignee_id", "due_on"),
)

FAULT_FIXED = KindSpec(
    event_type=FIXED,
    code=CODE,
    label_fa="تعمیر شد",
    group=KindGroup.EQUIPMENT,
    fields=(FieldSpec("note", "یادداشت (اختیاری)", FieldKind.TEXT, required=False, max_length=500),),
    permission=Permission.FAULT_FIX,
    payload_model=FaultFixed,
    effects=no_effects,
    sample=_sample_fix,
    summarize=_summarise_fix,
    validate=_validate_fix,
    needs_reference=True,
    reference_types=(REPORTED,),
    unique_reference=True,
    cross_shift=True,
    card_fields=("note",),
)

register(FAULT_REPORTED, FAULT_ASSIGNED, FAULT_FIXED)
