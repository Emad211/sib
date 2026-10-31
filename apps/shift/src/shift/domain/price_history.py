"""The price history of an item per vendor, folded from the accountant's digitized invoices (F2).

Every digitized invoice line is a price the clinic paid for an item to the vendor the order named, so the
history is a plain read of the log: no table, no stored figure, and a rebuild cannot disagree with it.
A delivery that came without an order has no vendor; its prices are kept under an empty vendor name and
the page words it as such. Prices stay out of every role's view but the accountant's and the manager's.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from shift.domain.event_types import EventType
from shift.domain.purchase import payload_of
from shift.domain.purchase_payloads import DeliveryReceived, InvoiceDigitized, OrderPlaced


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One bought line: the unit price on one invoice, when it was digitized and how many were bought."""

    delivery_id: str
    invoice_reference: str
    at_tehran: str
    unit_price_toman: int
    quantity: int


@dataclass(frozen=True, slots=True)
class PriceSeries:
    """All prices of one item from one vendor, oldest first."""

    item_id: str
    vendor: str
    points: tuple[PricePoint, ...]

    @property
    def latest(self) -> int:
        return self.points[-1].unit_price_toman

    @property
    def change_toman(self) -> int:
        """Latest minus the one before it; zero when there is only one price."""
        return 0 if len(self.points) < 2 else self.points[-1].unit_price_toman - self.points[-2].unit_price_toman


def price_history(events: Iterable[Any]) -> tuple[PriceSeries, ...]:
    """Every (item, vendor) series of the log, sorted by item and vendor."""
    ordered = sorted(events, key=lambda record: record.seq)
    vendors: dict[str, str] = {}
    orders: dict[str, str] = {}
    references: dict[str, str] = {}
    series: dict[tuple[str, str], list[PricePoint]] = {}
    for event in ordered:
        if event.type == EventType.ORDER_PLACED:
            placed = payload_of(OrderPlaced, event)
            if placed is not None:
                vendors[placed.order_id] = placed.vendor
        elif event.type == EventType.DELIVERY_RECEIVED:
            received = payload_of(DeliveryReceived, event)
            if received is not None:
                references[received.delivery_id] = received.invoice_reference
                if received.order_id is not None:
                    orders[received.delivery_id] = received.order_id
        elif event.type == EventType.INVOICE_DIGITIZED:
            digitized = payload_of(InvoiceDigitized, event)
            if digitized is None:
                continue
            vendor = vendors.get(orders.get(digitized.delivery_id, ""), "")
            for line in digitized.lines:
                point = PricePoint(
                    delivery_id=digitized.delivery_id,
                    invoice_reference=references.get(digitized.delivery_id, ""),
                    at_tehran=event.at_tehran,
                    unit_price_toman=line.unit_price_toman,
                    quantity=line.quantity,
                )
                series.setdefault((line.item_id, vendor), []).append(point)
    return tuple(PriceSeries(item, vendor, tuple(points)) for (item, vendor), points in sorted(series.items()))
