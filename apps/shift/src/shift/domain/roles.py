"""The closed role and permission vocabulary (R-07, R-08); the grants live in `permission_grants`."""

from enum import StrEnum


class Role(StrEnum):
    RECEPTION = "reception"
    NURSE = "nurse"
    STOREKEEPER = "storekeeper"
    PURCHASER = "purchaser"
    ACCOUNTANT = "accountant"
    SHIFT_DOCTOR = "shift_doctor"
    MANAGER = "manager"


class Permission(StrEnum):
    EVENTS_APPEND = "events.append"
    HANDOVER_START = "handover.start"
    HANDOVER_COUNT_SUBMIT = "handover.count.submit"
    HANDOVER_COUNT_OUTGOING = "handover.count.outgoing"
    HANDOVER_CASH_SUBMIT = "handover.cash.submit"
    HANDOVER_SIGN = "handover.sign"
    HANDOVER_BASELINE = "handover.baseline"
    CASH_WITHDRAW_REQUEST = "cash.withdraw.request"
    CASH_WITHDRAW_APPROVE = "cash.withdraw.approve"
    CASH_DEPOSIT_RECORD = "cash.deposit.record"
    STOCK_COUNT_SUBMIT = "stock.count.submit"
    STOCK_RESTOCK_CONFIRM = "stock.restock.confirm"
    STOCK_WITHDRAW_EMERGENCY = "stock.withdraw.emergency"
    STOCK_WASTE_REPORT = "stock.waste.report"
    PURCHASE_ORDER_DRAFT = "purchase.order.draft"
    PURCHASE_ORDER_DECIDE = "purchase.order.decide"
    PURCHASE_ORDER_PLACE = "purchase.order.place"
    PURCHASE_DELIVERY_RECEIVE = "purchase.delivery.receive"
    PURCHASE_PAYMENT_RECORD = "purchase.payment.record"
    PARCEL_HANDLE = "parcel.handle"
    CATALOG_MANAGE = "catalog.manage"
    # Shift events (S01 §3, SHF-3). A button only appears when its permission is held.
    CARD_TRANSFER_REPORT = "card.transfer.report"
    REFUND_REQUEST = "refund.request"
    CAMPAIGN_DISCOUNT_APPLY = "campaign.discount.apply"
    PATIENT_ITEM_HANDLE = "patient.item.handle"
    EQUIPMENT_FAULT_REPORT = "equipment.fault.report"
    FAULT_ASSIGN = "fault.assign"
    FAULT_FIX = "fault.fix"
    VITALS_RECORD = "vitals.record"
    CLEANING_CONFIRM = "cleaning.confirm"
    SHIFT_EVENT_APPROVE = "shift_event.approve"
    # The manager's own pages: the daily card and its history (S01 §6, SHF-6.1).
    DAILY_CARD_VIEW = "daily_card.view"
    SETTINGS_CHANGE = "settings.change"
    STAFF_MANAGE = "staff.manage"
    STAFF_PIN_RESET = "staff.pin.reset"
    STAFF_UNLOCK = "staff.unlock"
    DEVICE_ENROLL = "device.enroll"
    DEVICE_REVOKE = "device.revoke"


class UnknownRole(ValueError):
    """A role string outside the closed vocabulary; the caller must reject the login."""


def parse_role(value: str) -> Role:
    """Return the role for `value`, raising `UnknownRole` for anything else."""
    try:
        return Role(value.strip())
    except ValueError as exc:
        raise UnknownRole(f"unknown role: {value!r}") from exc
