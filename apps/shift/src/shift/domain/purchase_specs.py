"""The SHF-4 purchasing chain's event table: payload model and projection effect per type (pure, zero IO).

Kept beside the payload file so the vocabulary of S03 §3 stays one document, and composed into the
registry by `events.py`, the same way the shift-event kinds are.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from shift.domain import purchase_payloads as purchase
from shift.domain.effects import EffectBuilder, Effects, StockEffect, StockOperation, no_effects
from shift.domain.event_types import EventType


def stock_transfer(payload: Any) -> Effects:
    """A shelf charged from another place moves both ends in one event, in this order (P-10).

    The storekeeper takes the goods out of the main store and puts them on the shelf in the same
    moment, so the two effects travel together: a rebuild that replays the event applies the
    subtraction first and the addition second, and the pair can never be separated by a crash between
    two events. S01 §4 asks for exactly this — the store down and the shelf up at once, never one
    without the other.
    """
    return Effects(
        stock=(
            StockEffect(payload.item_id, payload.from_location_id, StockOperation.SUBTRACT, payload.quantity),
            StockEffect(payload.item_id, payload.to_location_id, StockOperation.ADD, payload.quantity),
        )
    )


PURCHASE_EVENT_SPECS: Mapping[EventType, tuple[type[BaseModel], EffectBuilder]] = {
    # The SHF-4 purchasing chain (S01 §4, S03 §3) is a set of documents, and a document changes no
    # shelf: `delivery_received` is the buying record while the goods enter a shelf through the shift
    # event «و» (`stock.shelf_restocked`), so one arrival is never counted into stock twice. An invoice,
    # a payment and a credit are the accountant's figures — the shift app records them and never moves
    # the clinic box with them — and a vendor return is a purchasing credit, not a shelf movement.
    EventType.ORDER_DRAFTED: (purchase.OrderDrafted, no_effects),
    EventType.ORDER_DECIDED: (purchase.OrderDecided, no_effects),
    EventType.ORDER_PLACED: (purchase.OrderPlaced, no_effects),
    EventType.DELIVERY_RECEIVED: (purchase.DeliveryReceived, no_effects),
    # Its shift-event kind owns this type too; the composed spec below keeps the same payload and the
    # same (empty) effect, and `approvable_by=(MANAGER,)` is what lets the manager unlock a payment.
    EventType.DELIVERY_FLAGGED: (purchase.DeliveryFlagged, no_effects),
    EventType.DELIVERY_TRANSFERRED: (purchase.DeliveryTransferred, no_effects),
    EventType.INVOICE_DIGITIZED: (purchase.InvoiceDigitized, no_effects),
    EventType.PAYMENT_RECORDED: (purchase.PaymentRecorded, no_effects),
    EventType.VENDOR_RETURN: (purchase.VendorReturn, no_effects),
    EventType.VENDOR_CREDIT: (purchase.VendorCredit, no_effects),
    # The one moving record of the group: a shelf charged from another place, both ends at once (P-10).
    EventType.RESTOCK_CONFIRMED: (purchase.RestockConfirmed, stock_transfer),
    EventType.EXPIRY_FLAGGED: (purchase.ExpiryFlagged, no_effects),
}
