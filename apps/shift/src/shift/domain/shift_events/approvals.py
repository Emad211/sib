"""Approval decisions (S01 §3 «تأیید»): `event.approved` and `event.rejected`.

The service owns the approval rules — which roles may decide which kind, never the author, once per
target. This module only names the two event types and their optional note, so the registry, the
phone and the manager's card share one vocabulary.
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
        permission=Permission.SHIFT_EVENT_APPROVE,
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
