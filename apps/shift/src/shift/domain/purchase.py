"""The purchase order's state machine, folded from the log (S03 §4 «سفارش خرید», P-02).

An order is one `order_id` and a handful of records: the storekeeper's draft, the manager's single
decision, and the purchaser's hand-off to a vendor. Nothing here writes: a fold reads the records the
log already holds, so the page, the manager's card and a rebuild all agree on one status.

`submitted` is the only status that waits for the manager. «تغییر» is a decision like the other two and
returns the order to `draft`; the fresh draft comes back with the same `order_id` and lands on
`submitted` again, which is why a later `order_drafted` wins over everything stored before it.

Pure by design: no IO, no clock, no Persian. An event is any record with `seq`, `event_id`, `type`,
`payload`, `actor_id` and `at_tehran` — the stored row, or a test's own stand-in.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, ValidationError

from shift.domain.event_types import EventType
from shift.domain.purchase_payloads import OrderDecided, OrderDrafted, OrderLine, OrderPlaced

# The statuses of S03 §4, in the order an order can reach them.
SUBMITTED = "submitted"
DRAFT = "draft"
APPROVED = "approved"
REJECTED = "rejected"
PLACED = "placed"
ORDER_STATUSES = (SUBMITTED, DRAFT, APPROVED, REJECTED, PLACED)
# The orders the manager still has to decide; a `draft` is waiting for the storekeeper, not the manager.
UNDECIDED = frozenset({SUBMITTED})
_DECISION_STATUS = {"approved": APPROVED, "rejected": REJECTED, "changed": DRAFT}


@dataclass(frozen=True, slots=True)
class OrderState:
    """One order as the log describes it, plus who spoke last and when."""

    order_id: str
    status: str
    lines: tuple[OrderLine, ...]
    note: str | None
    actor_id: str
    at_tehran: str
    decided_by: str | None
    placed_vendor: str | None
    placed_reference: str | None


def payload_of[Model: BaseModel](model: type[Model], event: Any) -> Model | None:
    """Validate one record's payload against its model, or None when it is not that shape.

    The log is append-only and older rows survive every change of a payload model, so a fold skips a
    row it cannot read instead of failing: one unreadable document must not hide the rest of the
    chain. The projection engine stays the place that refuses a payload it cannot replay.
    """
    try:
        return model.model_validate(event.payload)
    except ValidationError:
        return None


def order_states(events: Iterable[Any]) -> dict[str, OrderState]:
    """Fold every order of the log, oldest first; a record of another type changes nothing."""
    states: dict[str, OrderState] = {}
    for event in sorted(events, key=lambda record: record.seq):
        changed = _fold(states, event)
        if changed is not None:
            states[changed.order_id] = changed
    return states


def _fold(states: dict[str, OrderState], event: Any) -> OrderState | None:
    """One record against the orders folded so far; None when it moves no order."""
    if event.type == EventType.ORDER_DRAFTED:
        payload = payload_of(OrderDrafted, event)
        if payload is None:
            return None
        # A draft is a fresh start: its lines, its note, and nobody's decision on it yet.
        return OrderState(
            order_id=payload.order_id,
            status=SUBMITTED,
            lines=payload.lines,
            note=payload.note,
            actor_id=event.actor_id,
            at_tehran=event.at_tehran,
            decided_by=None,
            placed_vendor=None,
            placed_reference=None,
        )
    if event.type == EventType.ORDER_DECIDED:
        payload = payload_of(OrderDecided, event)
        current = None if payload is None else states.get(payload.order_id)
        if payload is None or current is None:
            return None
        return replace(current, status=_DECISION_STATUS[payload.decision], decided_by=event.actor_id)
    if event.type == EventType.ORDER_PLACED:
        payload = payload_of(OrderPlaced, event)
        current = None if payload is None else states.get(payload.order_id)
        if payload is None or current is None:
            return None
        return replace(current, status=PLACED, placed_vendor=payload.vendor, placed_reference=payload.reference)
    return None
