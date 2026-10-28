"""Projection effects: the only way an event may change stock or cash state."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class StockOperation(StrEnum):
    SET = "set"
    ADD = "add"
    SUBTRACT = "subtract"


class CashOperation(StrEnum):
    OPEN = "open"
    ADD = "add"
    SUBTRACT = "subtract"
    COUNT = "count"


@dataclass(frozen=True, slots=True)
class StockEffect:
    item_id: str
    location_id: str
    operation: StockOperation
    quantity: int


@dataclass(frozen=True, slots=True)
class CashEffect:
    operation: CashOperation
    amount: int


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
    """Opening cash seeds both the expected and the physically counted amount."""
    return Effects(cash=CashEffect(CashOperation.OPEN, payload.opening_cash))


def cash_count(payload: Any) -> Effects:
    """A closing blind count records only the counted amount."""
    return Effects(cash=CashEffect(CashOperation.COUNT, payload.counted))


def cash_delta(operation: CashOperation) -> EffectBuilder:
    """A receipt or withdrawal changes the expected cash amount."""

    def build(payload: Any) -> Effects:
        return Effects(cash=CashEffect(operation, payload.amount))

    return build
