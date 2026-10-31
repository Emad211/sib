"""The closed event vocabulary (S03 §3): every type the append-only log may hold."""

from enum import StrEnum


class EventType(StrEnum):
    HANDOVER_BASELINE_RECORDED = "handover.baseline_recorded"
    HANDOVER_STARTED = "handover.started"
    HANDOVER_CASH_COUNTED = "handover.cash_counted"
    HANDOVER_ITEM_COUNTED = "handover.item_counted"
    HANDOVER_RECOUNT_REQUESTED = "handover.recount_requested"
    HANDOVER_STEP_ADVANCED = "handover.step_advanced"
    HANDOVER_OUTGOING_COUNTED = "handover.outgoing_counted"
    HANDOVER_JOINT_COUNTED = "handover.joint_counted"
    HANDOVER_COUNT_SETTLED = "handover.count_settled"
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
    # SHF-3 shift events (S01 §3): every one is recorded on the staff phone and may arrive from the
    # offline outbox (ADR-016). Only the four moving kinds carry a projection effect.
    CARD_TRANSFER_REPORTED = "card.transfer_reported"
    REFUND_REQUESTED = "refund.requested"
    CAMPAIGN_DISCOUNT_APPLIED = "campaign.discount_applied"
    PATIENT_ITEM_HELD = "patient.item_held"
    PATIENT_ITEM_DELIVERED = "patient.item_delivered"
    EQUIPMENT_FAULT_REPORTED = "equipment.fault_reported"
    EQUIPMENT_FAULT_ASSIGNED = "equipment.fault_assigned"
    EQUIPMENT_FAULT_FIXED = "equipment.fault_fixed"
    VITALS_RECORDED = "vitals.recorded"
    VITALS_FOLLOWUP_OPENED = "vitals.followup_opened"
    CLEANING_CONFIRMED = "cleaning.confirmed"
    EVENT_APPROVED = "event.approved"
    EVENT_REJECTED = "event.rejected"


class UnknownEventType(ValueError):
    """The event type is not in the registry."""
