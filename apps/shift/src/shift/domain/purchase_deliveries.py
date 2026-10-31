"""The delivery's state machine, folded from the log (S03 §4 «سفارش خرید», P-04…P-08).

A delivery is the parcel: reception takes it in against an invoice number and counts units, a problem
flag locks its payment until the manager decides, the parcel changes hands between shifts until the
storekeeper holds it, and the accountant's invoice and payments close it. The state is folded from the
records, never stored, so the receptionist's page, the accountant's queue and a rebuild agree.

`payment_locked` is the one thing this fold reads across records: a `delivery_flagged` stays locked
while no `event.approved` references it. A rejection leaves it locked, because the owner's rule keeps a
refused parcel on the return path instead of paying for it (P-06). `paid` is a sum and not a step: an
invoice is paid when its payments cover it (P-08). A later vendor return wins over a custody before it,
so a parcel that went back never reads as still on its way.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from shift.domain.event_types import EventType
from shift.domain.purchase import payload_of
from shift.domain.purchase_payloads import (
    DeliveryFlagged,
    DeliveryLine,
    DeliveryReceived,
    DeliveryTransferred,
    InvoiceDigitized,
    InvoiceLine,
    PaymentRecorded,
    VendorCredit,
    VendorReturn,
)

# The statuses of S03 §4, in the order a parcel can reach them.
RECEIVED = "received"
FLAGGED = "flagged"
IN_TRANSIT = "in_transit"
WITH_STOREKEEPER = "with_storekeeper"
RETURNED = "returned"
DIGITIZED = "digitized"
PAID = "paid"
DELIVERY_STATUSES = (RECEIVED, FLAGGED, IN_TRANSIT, WITH_STOREKEEPER, RETURNED, DIGITIZED, PAID)
# Taken in and not closed by a return: the invoice may be digitized and a return recorded.
RECEIVED_STATUSES = frozenset({RECEIVED, FLAGGED, IN_TRANSIT, WITH_STOREKEEPER})
_CUSTODY_STATUS = {"shift": IN_TRANSIT, "storekeeper": WITH_STOREKEEPER}


@dataclass(frozen=True, slots=True)
class DeliveryState:
    """One parcel as the log describes it; `actor_id` is the person who took it in at the door."""

    delivery_id: str
    order_id: str | None
    invoice_reference: str
    lines: tuple[DeliveryLine, ...]
    packages: int | None
    status: str
    flagged_reason: str | None
    payment_locked: bool
    invoice_total: int
    invoice_lines: tuple[InvoiceLine, ...]
    paid_total: int
    credit_total: int
    custody: str | None
    returned: bool
    actor_id: str
    at_tehran: str


def delivery_states(events: Iterable[Any]) -> dict[str, DeliveryState]:
    """Fold every delivery of the log, oldest first; a record of another type changes nothing."""
    ordered = sorted(events, key=lambda record: record.seq)
    approved = {
        record.ref_event_id
        for record in ordered
        if record.type == EventType.EVENT_APPROVED and getattr(record, "ref_event_id", None)
    }
    states: dict[str, DeliveryState] = {}
    for event in ordered:
        changed = _fold(states, event, approved)
        if changed is not None:
            states[changed.delivery_id] = changed
    return {key: _settled(state) for key, state in states.items()}


def _fold(states: dict[str, DeliveryState], event: Any, approved: set[str]) -> DeliveryState | None:
    """One record against the deliveries folded so far; None when it moves no delivery."""
    if event.type == EventType.DELIVERY_RECEIVED:
        payload = payload_of(DeliveryReceived, event)
        if payload is None:
            return None
        return DeliveryState(
            delivery_id=payload.delivery_id,
            order_id=payload.order_id,
            invoice_reference=payload.invoice_reference,
            lines=payload.lines,
            packages=payload.packages,
            status=RECEIVED,
            flagged_reason=None,
            payment_locked=False,
            invoice_total=0,
            invoice_lines=(),
            paid_total=0,
            credit_total=0,
            custody=None,
            returned=False,
            actor_id=event.actor_id,
            at_tehran=event.at_tehran,
        )
    current = states.get(_delivery_id(event) or "")
    if current is None:
        return None
    if event.type == EventType.DELIVERY_FLAGGED:
        payload = payload_of(DeliveryFlagged, event)
        if payload is None:
            return None
        return replace(
            current,
            status=FLAGGED,
            flagged_reason=payload.reason,
            payment_locked=event.event_id not in approved,
        )
    if event.type == EventType.DELIVERY_TRANSFERRED:
        payload = payload_of(DeliveryTransferred, event)
        if payload is None:
            return None
        status = current.status if current.returned else _CUSTODY_STATUS[payload.to_kind]
        return replace(current, custody=payload.to_kind, status=status)
    if event.type == EventType.VENDOR_RETURN:
        if payload_of(VendorReturn, event) is None:
            return None
        return replace(current, returned=True, status=RETURNED)
    if event.type == EventType.INVOICE_DIGITIZED:
        payload = payload_of(InvoiceDigitized, event)
        if payload is None:
            return None
        total = sum(line.quantity * line.unit_price_toman for line in payload.lines)
        return replace(
            current,
            invoice_lines=payload.lines,
            invoice_total=total,
            status=current.status if current.returned else DIGITIZED,
        )
    if event.type == EventType.PAYMENT_RECORDED:
        payload = payload_of(PaymentRecorded, event)
        if payload is None:
            return None
        return replace(current, paid_total=current.paid_total + payload.amount_toman)
    if event.type == EventType.VENDOR_CREDIT:
        payload = payload_of(VendorCredit, event)
        if payload is None:
            return None
        return replace(current, credit_total=current.credit_total + payload.amount_toman)
    return None


def _delivery_id(event: Any) -> str | None:
    """The delivery a record is about, if its payload names one at all."""
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    value = payload.get("delivery_id")
    return None if value is None else str(value)


def _settled(state: DeliveryState) -> DeliveryState:
    """P-08: a delivery is paid once its payments cover a real invoice, whatever closed it last.

    A parcel that went back to the vendor keeps `returned`: the money for it is the credit note, so a
    refunded return must never read as a paid invoice.
    """
    if state.returned or state.invoice_total <= 0 or state.paid_total < state.invoice_total:
        return state
    return replace(state, status=PAID)
