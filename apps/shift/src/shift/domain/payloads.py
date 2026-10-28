"""Validated payload models for the projection event vocabulary (schema_v = 1)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from shift.domain.money import Toman

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Quantity = Annotated[int, Field(strict=True, ge=0)]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShiftOpened(_Payload):
    opening_cash: Toman


class CashCounted(_Payload):
    counted: Toman


class CashAmount(_Payload):
    amount: Toman


class CashWithdrawn(CashAmount):
    pass


class CashDeposited(CashAmount):
    pass


class CashReceipt(CashAmount):
    pass


class ItemCounted(_Payload):
    item_id: Identifier
    location_id: Identifier
    counted: Quantity


class WeeklyCount(ItemCounted):
    pass


class StockDelta(_Payload):
    item_id: Identifier
    location_id: Identifier
    quantity: Quantity


class ShelfRestocked(StockDelta):
    pass


class EmergencyWithdrawal(StockDelta):
    pass


class WasteReported(StockDelta):
    pass


class ConsumptionRecorded(StockDelta):
    pass
