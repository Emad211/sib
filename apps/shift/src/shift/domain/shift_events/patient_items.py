"""Patient belongings (SHF-3 «م»): a belonging kept at the desk and its one delivery (S01 §3, E-07).

A patient's phone, glasses or documents wait behind the reception desk; nothing about them sits on a
shelf or in the box, so neither kind has a projection effect (S03 §3). The delivery is an event of its
own that points at the held event with `ref_event_id` and closes it once: one belonging goes back to
the patient once, and a second delivery of the same held event would claim two returns of one item.

The payload models live here, in the module that owns the two kinds, because the kind is the single
owner of its own questions; `domain/payloads.py` keeps the older vocabulary they are built on.
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field

from shift.domain.effects import no_effects
from shift.domain.payloads import Identifier, Note, Payload
from shift.domain.permissions import Permission
from shift.domain.shift_events.base import FieldKind, FieldSpec, KindGroup, KindSpec, register

# The words the person at the desk writes on the slip; the field bounds refuse a blank name.
Name = Annotated[str, Field(min_length=1, max_length=200)]
HELD_CARD_FIELDS = ("item_name", "patient_name", "admission_no")
DELIVERED_CARD_FIELDS = ("note",)


class PatientItemHeld(Payload):
    """The belonging a patient left with us: what it is, whose it is and which admission brought it."""

    item_name: Name
    patient_name: Name
    admission_no: Identifier


class PatientItemDelivered(Payload):
    """The belonging went back to its patient; the note stays optional because the held event says the rest."""

    note: Note | None = None


def _held_summary(payload: BaseModel) -> dict[str, Any]:
    """The card line of a held belonging: what, whose, and the admission that brought it."""
    return {key: getattr(payload, key) for key in HELD_CARD_FIELDS}


def _delivered_summary(payload: BaseModel) -> dict[str, Any]:
    """The card line of a delivery: the note, when the person wrote one."""
    return {key: getattr(payload, key) for key in DELIVERED_CARD_FIELDS}


register(
    KindSpec(
        event_type="patient.item_held",
        code="م",
        label_fa="امانت بیمار",
        group=KindGroup.CARE,
        fields=(
            FieldSpec("item_name", "قلم", FieldKind.TEXT),
            FieldSpec("patient_name", "نام بیمار", FieldKind.TEXT),
            FieldSpec("admission_no", "شماره پذیرش", FieldKind.TEXT, max_length=64),
        ),
        permission=Permission.PATIENT_ITEM_HANDLE,
        payload_model=PatientItemHeld,
        effects=no_effects,
        sample=lambda: {"item_name": "عینک بیمار", "patient_name": "بیمار آزمایشی", "admission_no": "A-2001"},
        summarize=_held_summary,
        card_fields=HELD_CARD_FIELDS,
    ),
    KindSpec(
        event_type="patient.item_delivered",
        code="م",
        label_fa="تحویل امانت بیمار",
        group=KindGroup.CARE,
        fields=(FieldSpec("note", "یادداشت (اختیاری)", FieldKind.TEXT, required=False, max_length=500),),
        permission=Permission.PATIENT_ITEM_HANDLE,
        payload_model=PatientItemDelivered,
        effects=no_effects,
        sample=lambda: {"note": None},
        summarize=_delivered_summary,
        needs_reference=True,
        reference_types=("patient.item_held",),
        unique_reference=True,
        card_fields=DELIVERED_CARD_FIELDS,
    ),
)
