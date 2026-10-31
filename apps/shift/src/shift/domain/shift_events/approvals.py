"""Approval decisions (S01 §3 «تأیید»): `event.approved` and `event.rejected`.

A decision is a shift event like any other: it needs a target, it never moves a projection, and it is
appended idempotently through the same phone endpoint. Who may decide *which* record is not a property
of the decision, it is a property of the target: the person the target names (`confirmer_field`) or a
holder of one of its roles (`approvable_by`), never its author. So the decision declares the permission
every role holds and the service decides the rest; a role gate here would refuse the named nurse and
receptionist the clinic asks to confirm.
"""

from typing import Annotated

from pydantic import Field

from shift.domain.effects import no_effects
from shift.domain.payloads import Payload
from shift.domain.permissions import Permission
from shift.domain.shift_events.base import (
    APPROVED,
    REJECTED,
    FieldKind,
    FieldSpec,
    KindGroup,
    KindSpec,
    register,
)


class DecisionNote(Payload):
    """A decision with an optional short note; the reference lives on the event, not in the payload."""

    note: Annotated[str, Field(max_length=500)] | None = None


def _summary(payload: DecisionNote) -> dict[str, object]:
    return {"note": payload.note}


def _kind(event_type: str, code: str, label: str, sample_note: str) -> KindSpec:
    return KindSpec(
        event_type=event_type,
        code=code,
        label_fa=label,
        group=KindGroup.MONEY,
        fields=(FieldSpec("note", "یادداشت (اختیاری)", FieldKind.TEXT, required=False, max_length=500),),
        permission=Permission.EVENTS_APPEND,
        payload_model=DecisionNote,
        effects=no_effects,
        sample=lambda: {"note": sample_note},
        summarize=_summary,
        needs_reference=True,
        card_fields=("note",),
    )


register(
    _kind(APPROVED, "✓", "تأیید", "تأیید شد"),
    _kind(REJECTED, "✗", "رد", "رد شد"),
)
