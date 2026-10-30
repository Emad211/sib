"""Event vocabulary, payload registry and projection-effect lookup (pure, zero IO)."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from shift.domain.effects import (
    CashOperation,
    EffectBuilder,
    Effects,
    StockOperation,
    cash_count,
    cash_delta,
    cash_open,
    joint_count,
    no_effects,
    stock_delta,
    stock_set,
)
from shift.domain.handover_payloads import (
    ChecklistAnswered,
    DiscrepancyRecorded,
    EarlyLeaveReported,
    EmergencyStartConfirmed,
    HandoverSigned,
    JointCounted,
    OutgoingCounted,
    ParcelHanded,
    RecountRequested,
)
from shift.domain.payloads import (
    CashCounted,
    CashDeposited,
    CashReceipt,
    CashReversal,
    CashWithdrawn,
    ConsumptionRecorded,
    EmergencyWithdrawal,
    ItemCounted,
    ShelfRestocked,
    ShiftOpened,
    WasteReported,
    WeeklyCount,
)


class EventType(StrEnum):
    HANDOVER_STARTED = "handover.started"
    HANDOVER_CASH_COUNTED = "handover.cash_counted"
    HANDOVER_ITEM_COUNTED = "handover.item_counted"
    HANDOVER_RECOUNT_REQUESTED = "handover.recount_requested"
    HANDOVER_OUTGOING_COUNTED = "handover.outgoing_counted"
    HANDOVER_JOINT_COUNTED = "handover.joint_counted"
    HANDOVER_CHECKLIST_ANSWERED = "handover.checklist_answered"
    HANDOVER_PARCEL_HANDED = "handover.parcel_handed"
    HANDOVER_SIGNED = "handover.signed"
    HANDOVER_EARLY_LEAVE_REPORTED = "handover.early_leave_reported"
    HANDOVER_EMERGENCY_START_CONFIRMED = "handover.emergency_start_confirmed"
    DISCREPANCY_RECORDED = "discrepancy.recorded"
    CASH_WITHDRAWN = "cash.withdrawn"
    CASH_DEPOSITED = "cash.deposited"
    STOCK_WEEKLY_COUNT = "stock.weekly_count"
    STOCK_SHELF_RESTOCKED = "stock.shelf_restocked"
    STOCK_EMERGENCY_WITHDRAWAL = "stock.emergency_withdrawal"
    STOCK_WASTE_REPORTED = "stock.waste_reported"
    ACC_CASH_RECEIPT = "acc.cash_receipt"
    ACC_CASH_REVERSAL = "acc.cash_reversal"
    ACC_CONSUMPTION = "acc.consumption"


class UnknownEventType(ValueError):
    """The event type is not in the registry."""


@dataclass(frozen=True, slots=True)
class EventSpec:
    payload_model: type[BaseModel]
    effects: EffectBuilder
    schema_v: int = 1
    requires_shift: bool = False


EVENT_SPECS: Mapping[EventType, EventSpec] = {
    EventType.HANDOVER_STARTED: EventSpec(ShiftOpened, cash_open, requires_shift=True),
    EventType.HANDOVER_CASH_COUNTED: EventSpec(CashCounted, cash_count, requires_shift=True),
    EventType.HANDOVER_ITEM_COUNTED: EventSpec(ItemCounted, stock_set, requires_shift=True),
    EventType.HANDOVER_RECOUNT_REQUESTED: EventSpec(RecountRequested, no_effects, requires_shift=True),
    EventType.HANDOVER_OUTGOING_COUNTED: EventSpec(OutgoingCounted, stock_set, requires_shift=True),
    EventType.HANDOVER_JOINT_COUNTED: EventSpec(JointCounted, joint_count, requires_shift=True),
    EventType.HANDOVER_CHECKLIST_ANSWERED: EventSpec(ChecklistAnswered, no_effects, requires_shift=True),
    EventType.HANDOVER_PARCEL_HANDED: EventSpec(ParcelHanded, no_effects, requires_shift=True),
    EventType.HANDOVER_SIGNED: EventSpec(HandoverSigned, no_effects, requires_shift=True),
    EventType.HANDOVER_EARLY_LEAVE_REPORTED: EventSpec(EarlyLeaveReported, no_effects, requires_shift=True),
    EventType.HANDOVER_EMERGENCY_START_CONFIRMED: EventSpec(EmergencyStartConfirmed, no_effects, requires_shift=True),
    EventType.DISCREPANCY_RECORDED: EventSpec(DiscrepancyRecorded, no_effects, requires_shift=True),
    EventType.CASH_WITHDRAWN: EventSpec(CashWithdrawn, cash_delta(CashOperation.SUBTRACT), requires_shift=True),
    EventType.CASH_DEPOSITED: EventSpec(CashDeposited, cash_delta(CashOperation.SUBTRACT), requires_shift=True),
    EventType.STOCK_WEEKLY_COUNT: EventSpec(WeeklyCount, stock_set),
    EventType.STOCK_SHELF_RESTOCKED: EventSpec(ShelfRestocked, stock_delta(StockOperation.ADD)),
    EventType.STOCK_EMERGENCY_WITHDRAWAL: EventSpec(EmergencyWithdrawal, stock_delta(StockOperation.SUBTRACT)),
    EventType.STOCK_WASTE_REPORTED: EventSpec(WasteReported, stock_delta(StockOperation.SUBTRACT)),
    EventType.ACC_CASH_RECEIPT: EventSpec(CashReceipt, cash_delta(CashOperation.ADD), requires_shift=True),
    EventType.ACC_CASH_REVERSAL: EventSpec(CashReversal, cash_delta(CashOperation.SUBTRACT), requires_shift=True),
    EventType.ACC_CONSUMPTION: EventSpec(ConsumptionRecorded, stock_delta(StockOperation.SUBTRACT)),
}


def spec_for(event_type: str) -> EventSpec:
    """Look up an event type, raising `UnknownEventType` for unregistered keys."""
    try:
        key = EventType(event_type)
    except ValueError as exc:
        raise UnknownEventType(f"unknown event type: {event_type!r}") from exc
    return EVENT_SPECS[key]


def validate_payload(event_type: str, payload: Mapping[str, Any]) -> BaseModel:
    """Validate raw payload JSON against the registered schema."""
    return spec_for(event_type).payload_model.model_validate(payload)


def effects_for(event_type: str, payload: BaseModel, shift_id: str | None) -> Effects:
    """Return the projection effects, enforcing shift scope for cash events."""
    spec = spec_for(event_type)
    if spec.requires_shift and not shift_id:
        raise ValueError(f"event {event_type!r} requires shift_id")
    return spec.effects(payload)
