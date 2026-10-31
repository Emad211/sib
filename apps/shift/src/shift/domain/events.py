"""Event vocabulary, payload registry and projection-effect lookup (pure, zero IO)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from shift.domain.effects import (
    CashOperation,
    EffectBuilder,
    Effects,
    StockOperation,
    baseline_recorded,
    cash_delta,
    cash_open,
    no_effects,
    settled_count,
    stock_delta,
    stock_set,
)
from shift.domain.event_types import EventType, UnknownEventType
from shift.domain.handover_baseline import BaselineRecorded
from shift.domain.handover_payloads import (
    ChecklistAnswered,
    CountSettled,
    DiscrepancyRecorded,
    EarlyLeaveReported,
    EmergencyStartConfirmed,
    HandoverSigned,
    JointCounted,
    OutgoingCounted,
    ParcelHanded,
    RecountRequested,
    StepAdvanced,
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
from shift.domain.shift_events import shift_event_specs


@dataclass(frozen=True, slots=True)
class EventSpec:
    payload_model: type[BaseModel]
    effects: EffectBuilder
    schema_v: int = 1
    requires_shift: bool = False


BASE_EVENT_SPECS: Mapping[EventType, EventSpec] = {
    # The manager's first count: one event sets every shelf and seeds the reception box, so the
    # first blind handover has a reference without anyone typing a declared figure (SHF-7.1).
    EventType.HANDOVER_BASELINE_RECORDED: EventSpec(BaselineRecorded, baseline_recorded, requires_shift=True),
    EventType.HANDOVER_STARTED: EventSpec(ShiftOpened, cash_open, requires_shift=True),
    # A blind count is a record of what one person read, never a reference: only `count_settled`
    # moves a shelf or the box, so an unconfirmed figure cannot become the next flow's reference.
    EventType.HANDOVER_CASH_COUNTED: EventSpec(CashCounted, no_effects, requires_shift=True),
    EventType.HANDOVER_ITEM_COUNTED: EventSpec(ItemCounted, no_effects, requires_shift=True),
    EventType.HANDOVER_RECOUNT_REQUESTED: EventSpec(RecountRequested, no_effects, requires_shift=True),
    EventType.HANDOVER_STEP_ADVANCED: EventSpec(StepAdvanced, no_effects, requires_shift=True),
    EventType.HANDOVER_OUTGOING_COUNTED: EventSpec(OutgoingCounted, no_effects, requires_shift=True),
    EventType.HANDOVER_JOINT_COUNTED: EventSpec(JointCounted, no_effects, requires_shift=True),
    # The one count event that writes: the shelf's SET, or the box's rebase to `counted + carry`
    # (H-05, H-13, H-11, ADR-017 item 9).
    EventType.HANDOVER_COUNT_SETTLED: EventSpec(CountSettled, settled_count, requires_shift=True),
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


def _shift_event_specs() -> Mapping[EventType, EventSpec]:
    """The composed SHF-3 vocabulary; a kind's own spec wins over the provisional base row.

    The four moving kinds keep a base row for the older modules that imported it, but the kind module
    is the single owner now and its spec (shift-scoped) replaces the provisional one.
    """
    composed = dict(BASE_EVENT_SPECS)
    for event_type, (payload_model, effects) in shift_event_specs().items():
        composed[EventType(event_type)] = EventSpec(payload_model, effects, requires_shift=True)
    return composed


EVENT_SPECS: Mapping[EventType, EventSpec] = _shift_event_specs()


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
