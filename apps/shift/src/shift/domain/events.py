"""Event vocabulary, payload registry and projection-effect lookup (pure, zero IO)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from shift.domain import purchase_payloads as purchase
from shift.domain.effects import (
    CashOperation,
    EffectBuilder,
    Effects,
    StockOperation,
    baseline_recorded,
    cash_delta,
    cash_open,
    late_cash_reversal,
    no_effects,
    settled_count,
    stock_delta,
    stock_set,
    stock_transfer,
)
from shift.domain.event_types import EventType, UnknownEventType
from shift.domain.handover_baseline import BaselineRecorded
from shift.domain.handover_cash_payloads import CashReferenceAdjusted, LateCashRecorded
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
from shift.domain.held_payloads import HeldRejected, HeldReleased, SubmissionHeld
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
    # H-11's two internal records: one explains why a cash reference moved during the count, the
    # other cancels a late event's box change so the settled figure is never moved twice.
    EventType.HANDOVER_CASH_REFERENCE_ADJUSTED: EventSpec(CashReferenceAdjusted, no_effects, requires_shift=True),
    EventType.HANDOVER_LATE_CASH_RECORDED: EventSpec(LateCashRecorded, late_cash_reversal, requires_shift=True),
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
    # F5: a record the server kept instead of dropping or applying (ADR-016). None of the three
    # changes a projection; the original event carries the movement when a manager releases it.
    EventType.EVENT_HELD: EventSpec(SubmissionHeld, no_effects, requires_shift=True),
    EventType.EVENT_RELEASED: EventSpec(HeldReleased, no_effects, requires_shift=True),
    EventType.EVENT_HOLD_REJECTED: EventSpec(HeldRejected, no_effects, requires_shift=True),
    # The SHF-4 purchasing chain (S01 §4, S03 §3) is a set of documents, and a document changes no
    # shelf: `delivery_received` is the buying record while the goods enter a shelf through the shift
    # event «و» (`stock.shelf_restocked`), so one arrival is never counted into stock twice. An invoice,
    # a payment and a credit are the accountant's figures — the shift app records them and never moves
    # the clinic box with them — and a vendor return is a purchasing credit, not a shelf movement.
    EventType.ORDER_DRAFTED: EventSpec(purchase.OrderDrafted, no_effects, requires_shift=True),
    EventType.ORDER_DECIDED: EventSpec(purchase.OrderDecided, no_effects, requires_shift=True),
    EventType.ORDER_PLACED: EventSpec(purchase.OrderPlaced, no_effects, requires_shift=True),
    EventType.DELIVERY_RECEIVED: EventSpec(purchase.DeliveryReceived, no_effects, requires_shift=True),
    # Its shift-event kind owns this type too; the composed spec below keeps the same payload and the
    # same (empty) effect, and `approvable_by=(MANAGER,)` is what lets the manager unlock a payment.
    EventType.DELIVERY_FLAGGED: EventSpec(purchase.DeliveryFlagged, no_effects, requires_shift=True),
    EventType.DELIVERY_TRANSFERRED: EventSpec(purchase.DeliveryTransferred, no_effects, requires_shift=True),
    EventType.INVOICE_DIGITIZED: EventSpec(purchase.InvoiceDigitized, no_effects, requires_shift=True),
    EventType.PAYMENT_RECORDED: EventSpec(purchase.PaymentRecorded, no_effects, requires_shift=True),
    EventType.VENDOR_RETURN: EventSpec(purchase.VendorReturn, no_effects, requires_shift=True),
    EventType.VENDOR_CREDIT: EventSpec(purchase.VendorCredit, no_effects, requires_shift=True),
    # The one moving record of the group: a shelf charged from another place, both ends at once (P-10).
    EventType.RESTOCK_CONFIRMED: EventSpec(purchase.RestockConfirmed, stock_transfer, requires_shift=True),
    EventType.EXPIRY_FLAGGED: EventSpec(purchase.ExpiryFlagged, no_effects, requires_shift=True),
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
