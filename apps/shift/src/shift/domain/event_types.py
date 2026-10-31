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
    # An internal record of the flow's own log: a cash event that occurred before this flow's cutoff
    # arrived while it was being counted, so the reference it compares against moved by this delta
    # (H-11). Metadata, never money, and it keeps a rebuild identical to the live comparison.
    HANDOVER_CASH_REFERENCE_ADJUSTED = "handover.cash_reference_adjusted"
    # An internal record of a cash event that occurred before a flow's cutoff but arrived after that
    # flow settled: the manager is told, and its own effect cancels the late event's box change so
    # the settled figure cannot be moved twice (H-11).
    HANDOVER_LATE_CASH_RECORDED = "handover.late_cash_recorded"
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
    # F5: a record whose own instant is in the future or more than three days old is never dropped —
    # it is kept, the manager is told, and only `event.released` lets its movement into the books.
    EVENT_HELD = "event.held"
    EVENT_RELEASED = "event.released"
    EVENT_HOLD_REJECTED = "event.hold_rejected"
    # ADR-021: a second receptionist or the shift doctor says «حضور در شیفت»; the record carries who
    # and when, and who is on shift is derived from it, never stored as a flag.
    SHIFT_PRESENCE_DECLARED = "shift.presence_declared"
    # SHF-5 rewards (S01 §5): written by the reward service or the manager's screen, never a phone
    # button, and none of them moves a projection — the ledger is derived from these three records.
    REWARD_EARNED = "reward.earned"
    REWARD_CONFIRMED = "reward.confirmed"
    REWARD_REVOKED = "reward.revoked"
    # SHF-4 purchasing chain (S01 §4, S03 §3): documents of the order → delivery → invoice → payment
    # path, plus the two warehouse records. Only `restock_confirmed` moves stock.
    ORDER_DRAFTED = "order_drafted"
    ORDER_DECIDED = "order_decided"
    ORDER_PLACED = "order_placed"
    DELIVERY_RECEIVED = "delivery_received"
    DELIVERY_FLAGGED = "delivery_flagged"
    DELIVERY_TRANSFERRED = "delivery_transferred"
    INVOICE_DIGITIZED = "invoice_digitized"
    PAYMENT_RECORDED = "payment_recorded"
    VENDOR_RETURN = "vendor_return"
    VENDOR_CREDIT = "vendor_credit"
    RESTOCK_CONFIRMED = "restock_confirmed"
    EXPIRY_FLAGGED = "expiry_flagged"
    # The main store's own records (B1, G3, C1): the receiver's count that lets a parcel join the store, the
    # giver's half of a shelf charge, the storekeeper's opening count, the accountant's monthly sample
    # and the manager's answer on a card row that is not an approvable record.
    DELIVERY_CONFIRMED = "delivery_confirmed"
    RESTOCK_OFFERED = "restock_offered"
    STOCK_STORE_BASELINED = "stock.store_baselined"
    STOCK_SAMPLE_COUNT = "stock.sample_count"
    MANAGER_REVIEWED = "manager.reviewed"


class UnknownEventType(ValueError):
    """The event type is not in the registry."""
