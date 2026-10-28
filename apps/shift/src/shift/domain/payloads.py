"""Validated payload models for the projection event vocabulary (schema_v = 1)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from shift.domain.money import Toman

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Quantity = Annotated[int, Field(strict=True, ge=0)]
AmountRaw = Annotated[float, Field(ge=0, allow_inf_nan=False)]
ReversalReason = Literal["reclosed", "voided", "refunded"]


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
    """A cash receipt copied from accounting; the raw REAL is kept for audit (design §4)."""

    amount_raw: AmountRaw | None = None


class CashReversal(CashAmount):
    """Compensation for a receipt accounting took back: a re-close or a void (S04 §2.1)."""

    reason: ReversalReason
    amount_raw: AmountRaw | None = None


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
