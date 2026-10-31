"""خ — بار مشکل‌دار (`delivery_flagged`, SHF-4), S01 §4 P-06.

Reception counts the units at the door, and sometimes the parcel is short, extra, wrong or broken. The
label is recorded against the delivery and **locks the invoice's payment** until the manager decides:
approval unlocks the payment, a rejection keeps the parcel on its way back to the vendor.

That decision is the clinic's existing approval flow — `event.approved` / `event.rejected` with a
reference (E-12) — so this kind declares the manager as its decider and nothing else: the service
already refuses the recorder, a second decision, and a refusal that closes itself. The purchase chain's
fold reads the same approval to unlock the invoice, so one decision serves the card and the accountant.

A flag moves no stock and no cash: the goods are already inside the door, and the price is the
accountant's business. The questions are therefore the delivery, the kind of problem and an optional
note, and nothing else.
"""

from typing import Any

from shift.domain.effects import no_effects
from shift.domain.permissions import Permission, Role
from shift.domain.purchase_payloads import DeliveryFlagged
from shift.domain.shift_events.base import (
    FieldKind,
    FieldSpec,
    KindGroup,
    KindSpec,
    register,
)

DELIVERY_FLAGGED = "delivery_flagged"

# The four problems the owner document names (کم، زیاد، اشتباه، شکسته); `ProblemKind` in the payload is
# the guard that no other value ever reaches the log.
PROBLEMS: tuple[tuple[str, str], ...] = (
    ("short", "کم"),
    ("extra", "زیاد"),
    ("wrong", "اشتباه"),
    ("broken", "شکسته"),
)
PROBLEM_LABELS: dict[str, str] = dict(PROBLEMS)

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("delivery_id", "شناسهٔ بار", FieldKind.TEXT),
    FieldSpec("reason", "نوع مشکل", FieldKind.CHOICE, options=PROBLEMS),
    FieldSpec("note", "یادداشت (اختیاری)", FieldKind.TEXT, required=False, max_length=500),
)


def sample() -> dict[str, Any]:
    """A complete example of the kind, for the contract fixtures and the wizard's preview."""
    return {"delivery_id": "delivery-1", "reason": "short", "note": None}


def summarize(payload: DeliveryFlagged) -> dict[str, Any]:
    """The card row of one flag: which parcel, what was wrong with it, and the reviewer's note."""
    return {
        "delivery_id": payload.delivery_id,
        "reason": PROBLEM_LABELS.get(payload.reason),
        "note": payload.note,
    }


KIND = KindSpec(
    event_type=DELIVERY_FLAGGED,
    code="خ",
    label_fa="بار مشکل‌دار",
    group=KindGroup.STOCK,
    fields=FIELDS,
    permission=Permission.PURCHASE_DELIVERY_RECEIVE,
    payload_model=DeliveryFlagged,
    effects=no_effects,
    sample=sample,
    summarize=summarize,
    approvable_by=(Role.MANAGER,),
    card_fields=("delivery_id", "reason", "note"),
)

register(KIND)
