"""Projection effects: the only way an event may change stock or cash state."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from shift.domain.cash_box import CashBox


class StockOperation(StrEnum):
    SET = "set"
    ADD = "add"
    SUBTRACT = "subtract"


class CashOperation(StrEnum):
    OPEN = "open"
    ADD = "add"
    SUBTRACT = "subtract"
    COUNT = "count"
    SETTLE = "settle"


@dataclass(frozen=True, slots=True)
class StockEffect:
    item_id: str
    location_id: str
    operation: StockOperation
    quantity: int


@dataclass(frozen=True, slots=True)
class CashEffect:
    """One change to one cash box; the box is reception's until the clinic opens a second (D2).

    `carry` belongs to `SETTLE` alone: the cash the box recorded during the flow, which stays on top
    of the agreed figure so the next shift's `N0` includes it (H-11). Every other operation leaves it
    at zero.
    """

    operation: CashOperation
    amount: int
    cash_box_id: str | None
    carry: int = 0


@dataclass(frozen=True, slots=True)
class Effects:
    stock: StockEffect | None = None
    cash: CashEffect | None = None


EffectBuilder = Callable[[Any], Effects]


def stock_set(payload: Any) -> Effects:
    """A blind count replaces the shelf quantity for that item and location."""
    return Effects(stock=StockEffect(payload.item_id, payload.location_id, StockOperation.SET, payload.counted))


def stock_delta(operation: StockOperation) -> EffectBuilder:
    """A restock or consumption changes the shelf quantity by `quantity`."""

    def build(payload: Any) -> Effects:
        return Effects(stock=StockEffect(payload.item_id, payload.location_id, operation, payload.quantity))

    return build


def cash_open(payload: Any) -> Effects:
    """Opening cash seeds the reception box; a nursing flow opens no cash at all (H-12, ADR-017 8)."""
    if payload.opening_cash is None:
        return Effects()
    return Effects(cash=CashEffect(CashOperation.OPEN, payload.opening_cash, CashBox.RECEPTION.value))


def cash_count(payload: Any) -> Effects:
    """A closing blind count records only the counted amount."""
    return Effects(cash=CashEffect(CashOperation.COUNT, payload.counted, CashBox.RECEPTION.value))


def cash_settle(payload: Any) -> Effects:
    """A settled cash chain rebases the box to `counted + carry` (H-13, H-11).

    Without the rebase the gap of one shift would sit inside every later reference, because the next
    shift's `N0` is the figure the handover settled on (H-02, ADR-017 item 8). The carry is the cash
    the box took in during the flow — the next shift's money, because the incoming person takes none
    — so it stays on top of the agreed figure instead of being absorbed by it.
    """
    return Effects(cash=CashEffect(CashOperation.SETTLE, payload.counted, CashBox.RECEPTION.value, payload.carry))


def cash_delta(operation: CashOperation) -> EffectBuilder:
    """A receipt or withdrawal changes the expected cash amount."""

    def build(payload: Any) -> Effects:
        return Effects(cash=CashEffect(operation, payload.amount, CashBox.RECEPTION.value))

    return build


def no_effects(_payload: Any) -> Effects:
    """A note, a checklist answer or a signature changes no stock and no cash by itself."""
    return Effects()


def joint_count(payload: Any) -> Effects:
    """An item joint count sets the shelf to the agreed figure (H-05).

    A cash joint count moves nothing by itself: on the cash side that figure is only a count, and
    the settlement event rebases the box (H-13).
    """
    if payload.scope == "cash":
        return Effects()
    return Effects(stock=StockEffect(payload.item_id, payload.location_id, StockOperation.SET, payload.counted))
