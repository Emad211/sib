"""The crew's cleaning checklist (SHF-3 «خ»): one place confirmed with the items cleaned there (E-11).

The person who cleaned a room ticks the catalog items of that room; the event carries the location
and the list in one record, so the manager reads who confirmed what and where. An item only counts at
its own location: confirming the reception shelf must not accept an item that lives in the CPR room,
because the checklist would then claim work nobody did there.

No projection effect: cleaning changes neither money nor a shelf (S03 §3).
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field

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

# One checklist line: the catalog's own item id, short because it names a button on the phone.
ChecklistItem = Annotated[str, Field(min_length=1, max_length=50)]
CARD_FIELDS = ("location_id", "items")


class CleaningConfirmed(Payload):
    """A cleaned place and the items ticked off there; an empty list would confirm nothing."""

    location_id: Identifier
    items: Annotated[tuple[ChecklistItem, ...], Field(min_length=1)]


def _items_belong_to_the_location(payload: CleaningConfirmed, ctx: ShiftEventContext) -> None:
    """Every confirmed item has to sit at the confirmed location; that lookup also checks the location."""
    for item in payload.items:
        if not ctx.item_at(item, payload.location_id):
            raise ShiftEventError(f"قلم «{item}» در این محل در کاتالوگ نیست.")


def _cleaning_summary(payload: BaseModel) -> dict[str, Any]:
    """The card line of a confirmed room: which place, and the items ticked there."""
    return {key: getattr(payload, key) for key in CARD_FIELDS}


register(
    KindSpec(
        event_type="cleaning.confirmed",
        code="خ",
        label_fa="چک‌لیست خدمه",
        group=KindGroup.CARE,
        fields=(
            FieldSpec("location_id", "محل", FieldKind.LOCATION),
            FieldSpec("items", "اقلام تأییدشده", FieldKind.MULTI),
        ),
        permission=Permission.CLEANING_CONFIRM,
        payload_model=CleaningConfirmed,
        effects=no_effects,
        sample=lambda: {"location_id": "location-1", "items": ["item-1"]},
        summarize=_cleaning_summary,
        validate=_items_belong_to_the_location,
        card_fields=CARD_FIELDS,
    ),
)
