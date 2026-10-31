"""The purchase order's and the parcel's states, folded from the log (S03 §4, P-02, P-06, P-08).

The two folds decide what a page and the manager's card say about a chain that spans shifts, so they
run here on hand-written records: the order's five statuses, a re-draft after «تغییر», the payment lock
that only a manager's approval lifts, the custody a parcel travels under, and the sum that makes an
invoice paid. A record the fold does not know must never break it — the log is append-only and older
rows outlive every later payload.
"""

from dataclasses import dataclass
from typing import Any

from shift.domain.purchase import (
    APPROVED,
    DRAFT,
    ORDER_STATUSES,
    PLACED,
    REJECTED,
    SUBMITTED,
    order_states,
)
from shift.domain.purchase_deliveries import (
    DELIVERY_STATUSES,
    DIGITIZED,
    FLAGGED,
    IN_TRANSIT,
    PAID,
    RECEIVED,
    RETURNED,
    WITH_STOREKEEPER,
    delivery_states,
)

AT = "2026-10-28T09:00:00.000000+03:30"


@dataclass(frozen=True, slots=True)
class Record:
    """The little a fold reads of one stored event."""

    seq: int
    type: str
    payload: dict[str, Any]
    event_id: str = ""
    actor_id: str = "staff-1"
    at_tehran: str = AT
    ref_event_id: str | None = None


def _event(seq: int, event_type: str, payload: dict[str, Any], **overrides: Any) -> Record:
    values: dict[str, Any] = {"event_id": f"event-{seq}", **overrides}
    return Record(seq=seq, type=event_type, payload=payload, **values)


def _drafted(seq: int, *quantities: int, note: str | None = None) -> Record:
    lines = [
        {"item_id": "para_500_amp", "location_id": "shelf_reception", "quantity": quantity} for quantity in quantities
    ]
    return _event(seq, "order_drafted", {"order_id": "order-1", "lines": lines, "note": note})


def _decided(seq: int, decision: str) -> Record:
    return _event(
        seq, "order_decided", {"order_id": "order-1", "decision": decision, "note": None}, actor_id="manager-1"
    )


def _placed(seq: int) -> Record:
    return _event(seq, "order_placed", {"order_id": "order-1", "vendor": "داروخانهٔ نمونه", "reference": "سفارش ۷"})


def _received(seq: int = 1, **overrides: Any) -> Record:
    payload: dict[str, Any] = {
        "delivery_id": "delivery-1",
        "order_id": "order-1",
        "invoice_reference": "فاکتور ۱",
        "lines": [{"item_id": "para_500_amp", "location_id": "shelf_reception", "quantity": 4}],
        "packages": 2,
    }
    payload.update(overrides)
    return _event(seq, "delivery_received", payload, actor_id="staff-reception")


def _flagged(seq: int) -> Record:
    return _event(seq, "delivery_flagged", {"delivery_id": "delivery-1", "reason": "short", "note": None})


def _approved(seq: int, ref_event_id: str) -> Record:
    return _event(seq, "event.approved", {"note": None}, ref_event_id=ref_event_id, actor_id="manager-1")


def _transferred(seq: int, to_kind: str) -> Record:
    return _event(
        seq,
        "delivery_transferred",
        {"delivery_id": "delivery-1", "to_staff_id": "staff-store", "to_kind": to_kind, "packages": 2},
    )


def _invoice(seq: int = 2, *, unit_price: int = 10_000, quantity: int = 4) -> Record:
    return _event(
        seq,
        "invoice_digitized",
        {
            "delivery_id": "delivery-1",
            "lines": [{"item_id": "para_500_amp", "quantity": quantity, "unit_price_toman": unit_price}],
            "reference": None,
        },
    )


def _payment(seq: int, amount: int) -> Record:
    return _event(
        seq,
        "payment_recorded",
        {"delivery_id": "delivery-1", "amount_toman": amount, "method": "transfer", "paid_on": None},
    )


def _returned(seq: int) -> Record:
    return _event(
        seq,
        "vendor_return",
        {
            "delivery_id": "delivery-1",
            "lines": [{"item_id": "para_500_amp", "quantity": 1}],
            "reason": "بار شکسته بود",
        },
    )


# --- the order ------------------------------------------------------------------------------


def test_a_draft_waits_for_the_manager() -> None:
    order = order_states([_drafted(1, 4, note="کم بود")])["order-1"]

    assert order.status == SUBMITTED
    assert [(line.item_id, line.quantity) for line in order.lines] == [("para_500_amp", 4)]
    assert (order.note, order.actor_id) == ("کم بود", "staff-1")
    assert order.decided_by is None


def test_an_approval_lets_the_purchaser_place_the_order() -> None:
    order = order_states([_drafted(1, 4), _decided(2, "approved"), _placed(3)])["order-1"]

    assert order.status == PLACED
    assert order.decided_by == "manager-1"
    assert (order.placed_vendor, order.placed_reference) == ("داروخانهٔ نمونه", "سفارش ۷")


def test_a_rejected_order_stays_rejected() -> None:
    assert order_states([_drafted(1, 4), _decided(2, "rejected")])["order-1"].status == REJECTED


def test_change_sends_the_order_back_and_a_fresh_draft_waits_again() -> None:
    """P-02: «تغییر» is the third decision, and the new draft keeps the same order id."""
    changed = order_states([_drafted(1, 4), _decided(2, "changed")])
    redrafted = order_states([_drafted(1, 4), _decided(2, "changed"), _drafted(3, 6), _decided(4, "approved")])

    assert changed["order-1"].status == DRAFT
    assert changed["order-1"].decided_by == "manager-1"
    assert [line.quantity for line in redrafted["order-1"].lines] == [6], "the newer draft's lines win"
    assert redrafted["order-1"].status == APPROVED


def test_the_fold_reads_the_log_oldest_first_whatever_the_input_order() -> None:
    order = order_states([_placed(3), _decided(2, "approved"), _drafted(1, 4)])["order-1"]

    assert order.status == PLACED


def test_an_unrelated_or_unreadable_record_never_breaks_the_fold() -> None:
    records = [
        _drafted(1, 4),
        _event(2, "stock.waste_reported", {"item_id": "para_500_amp", "quantity": 1}),
        _event(3, "order_decided", {"order_id": "order-1", "decision": "maybe"}),
        _event(4, "delivery_received", {"delivery_id": "nonsense"}),
    ]

    assert order_states(records)["order-1"].status == SUBMITTED
    assert delivery_states(records) == {}


def test_a_decision_for_an_order_the_log_never_drafted_moves_nothing() -> None:
    assert order_states([_decided(1, "approved")]) == {}


# --- the parcel -----------------------------------------------------------------------------


def test_a_received_delivery_holds_its_lines_and_no_invoice_yet() -> None:
    delivery = delivery_states([_received()])["delivery-1"]

    assert delivery.status == RECEIVED
    assert (delivery.order_id, delivery.invoice_reference, delivery.packages) == ("order-1", "فاکتور ۱", 2)
    assert (delivery.actor_id, delivery.invoice_total, delivery.paid_total) == ("staff-reception", 0, 0)
    assert delivery.payment_locked is False


def test_a_flag_locks_the_payment_until_a_manager_approves_it() -> None:
    """P-06: the label locks the invoice, and a rejection keeps it locked."""
    flagged = delivery_states([_received(), _flagged(2)])["delivery-1"]
    unlocked = delivery_states([_received(), _flagged(2), _approved(3, "event-2")])["delivery-1"]
    rejected = delivery_states([_received(), _flagged(2), _event(3, "event.rejected", {}, ref_event_id="event-2")])

    assert (flagged.status, flagged.flagged_reason, flagged.payment_locked) == (FLAGGED, "short", True)
    assert (unlocked.status, unlocked.payment_locked) == (FLAGGED, False)
    assert rejected["delivery-1"].payment_locked is True


def test_the_parcel_changes_hands_between_shifts_until_the_store_takes_it() -> None:
    between_shifts = delivery_states([_received(), _transferred(2, "shift")])["delivery-1"]
    at_store = delivery_states([_received(), _transferred(2, "shift"), _transferred(3, "storekeeper")])

    assert (between_shifts.status, between_shifts.custody) == (IN_TRANSIT, "shift")
    assert (at_store["delivery-1"].status, at_store["delivery-1"].custody) == (WITH_STOREKEEPER, "storekeeper")


def test_a_vendor_return_is_the_last_word_on_custody() -> None:
    delivery = delivery_states([_received(), _transferred(2, "shift"), _returned(3)])["delivery-1"]

    assert (delivery.status, delivery.returned) == (RETURNED, True)


def test_the_server_sums_the_invoice_and_a_partial_payment_leaves_it_unpaid() -> None:
    deliverance = delivery_states([_received(), _invoice(2, unit_price=10_000, quantity=4), _payment(3, 15_000)])

    delivery = deliverance["delivery-1"]
    assert (delivery.status, delivery.invoice_total, delivery.paid_total) == (DIGITIZED, 40_000, 15_000)


def test_payments_that_cover_the_invoice_make_it_paid() -> None:
    """P-08: `paid` is the sum, so two partial payments close an invoice as one would."""
    delivery = delivery_states([_received(), _invoice(), _payment(3, 15_000), _payment(4, 25_000)])["delivery-1"]

    assert (delivery.status, delivery.paid_total) == (PAID, 40_000)


def test_a_credit_never_changes_what_the_payments_covered() -> None:
    delivery = delivery_states(
        [
            _received(),
            _invoice(),
            _payment(3, 40_000),
            _event(4, "vendor_credit", {"delivery_id": "delivery-1", "amount_toman": 5_000, "note": None}),
        ]
    )["delivery-1"]

    assert (delivery.status, delivery.credit_total) == (PAID, 5_000)


# --- the vocabulary the pages render ---------------------------------------------------------


def test_the_two_folds_only_ever_produce_a_status_a_page_knows() -> None:
    """S03 §4's statuses are a closed list; a template never has to guess at a free-text status."""
    order_chains = (
        [_drafted(1, 4)],
        [_drafted(1, 4), _decided(2, "changed")],
        [_drafted(1, 4), _decided(2, "approved")],
        [_drafted(1, 4), _decided(2, "rejected")],
        [_drafted(1, 4), _decided(2, "approved"), _placed(3)],
    )
    delivery_chains = (
        [_received()],
        [_received(), _flagged(2)],
        [_received(), _transferred(2, "shift")],
        [_received(), _transferred(2, "storekeeper")],
        [_received(), _returned(2)],
        [_received(), _invoice(2)],
        [_received(), _invoice(2), _payment(3, 40_000)],
    )

    assert all(order_states(chain)["order-1"].status in ORDER_STATUSES for chain in order_chains)
    assert all(delivery_states(chain)["delivery-1"].status in DELIVERY_STATUSES for chain in delivery_chains)
