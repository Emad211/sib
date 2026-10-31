"""Vital signs (SHF-3 «ق»): one reading recorded on the phone and the follow-up a nurse opens (E-10).

The app only records what was measured. The numeric threshold that would open a follow-up by itself
is not in the clinic's documents, so nothing here decides that a pressure or a sugar is too high: a
follow-up is always an explicit second event, and it points at the reading it follows.

No projection effect either: a vital sign is neither money nor stock (S03 §3).
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from shift.domain.effects import no_effects
from shift.domain.payloads import Identifier, Payload
from shift.domain.permissions import Permission
from shift.domain.shift_events.base import FieldKind, FieldSpec, KindGroup, KindSpec, register

Systolic = Annotated[int, Field(ge=0, le=400)]
Diastolic = Annotated[int, Field(ge=0, le=300)]
Sugar = Annotated[int, Field(ge=0, le=2000)]
FollowupNote = Annotated[str, Field(min_length=1, max_length=500)]
READING_CARD_FIELDS = ("systolic", "diastolic", "sugar")
FOLLOWUP_CARD_FIELDS = ("note",)


class VitalsRecorded(Payload):
    """One measurement: at least one of the three numbers, and the admission number when it is known."""

    systolic: Systolic | None = None
    diastolic: Diastolic | None = None
    sugar: Sugar | None = None
    admission_no: Identifier | None = None

    @model_validator(mode="after")
    def _at_least_one_reading(self) -> "VitalsRecorded":
        if self.systolic is None and self.diastolic is None and self.sugar is None:
            raise ValueError("a vitals record carries at least one of systolic, diastolic and sugar")
        return self


class FollowupOpened(Payload):
    """The nurse decided the patient needs another look; the reading it follows is the reference."""

    note: FollowupNote


def _readings_summary(payload: BaseModel) -> dict[str, Any]:
    """The card line of a measurement: the readings that were taken."""
    return {key: getattr(payload, key) for key in READING_CARD_FIELDS}


def _followup_summary(payload: BaseModel) -> dict[str, Any]:
    """The card line of a follow-up: what the nurse wrote down."""
    return {key: getattr(payload, key) for key in FOLLOWUP_CARD_FIELDS}


register(
    KindSpec(
        event_type="vitals.recorded",
        code="ق",
        label_fa="قند و فشار",
        group=KindGroup.CARE,
        fields=(
            FieldSpec("systolic", "سیستول", FieldKind.QUANTITY, required=False),
            FieldSpec("diastolic", "دیاستول", FieldKind.QUANTITY, required=False),
            FieldSpec("sugar", "قند", FieldKind.QUANTITY, required=False),
            FieldSpec("admission_no", "شماره پذیرش", FieldKind.TEXT, required=False, max_length=64),
        ),
        permission=Permission.VITALS_RECORD,
        payload_model=VitalsRecorded,
        effects=no_effects,
        sample=lambda: {"systolic": 120, "diastolic": 80, "sugar": 110, "admission_no": "A-2001"},
        summarize=_readings_summary,
        card_fields=READING_CARD_FIELDS,
    ),
    KindSpec(
        event_type="vitals.followup_opened",
        code="ق",
        label_fa="مسیر پیگیری",
        group=KindGroup.CARE,
        fields=(FieldSpec("note", "یادداشت پیگیری", FieldKind.TEXT, max_length=500),),
        permission=Permission.VITALS_RECORD,
        payload_model=FollowupOpened,
        effects=no_effects,
        sample=lambda: {"note": "کنترل دوبارهٔ فشار در پایان شیفت"},
        summarize=_followup_summary,
        needs_reference=True,
        reference_types=("vitals.recorded",),
        card_fields=FOLLOWUP_CARD_FIELDS,
    ),
)
