"""Role and permission matrix (R-07, R-08): pure data; roles union, `purchaser` is a role."""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType


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
    CASH_WITHDRAW_REQUEST = "cash.withdraw.request"
    CASH_WITHDRAW_APPROVE = "cash.withdraw.approve"
    CASH_DEPOSIT_RECORD = "cash.deposit.record"
    STOCK_COUNT_SUBMIT = "stock.count.submit"
    STOCK_RESTOCK_CONFIRM = "stock.restock.confirm"
    STOCK_WITHDRAW_EMERGENCY = "stock.withdraw.emergency"
    STOCK_WASTE_REPORT = "stock.waste.report"
    PURCHASE_ORDER_DRAFT = "purchase.order.draft"
    PURCHASE_ORDER_DECIDE = "purchase.order.decide"
    PURCHASE_DELIVERY_RECEIVE = "purchase.delivery.receive"
    PURCHASE_PAYMENT_RECORD = "purchase.payment.record"
    PARCEL_HANDLE = "parcel.handle"
    CATALOG_MANAGE = "catalog.manage"
    SETTINGS_CHANGE = "settings.change"
    STAFF_MANAGE = "staff.manage"
    STAFF_PIN_RESET = "staff.pin.reset"
    STAFF_UNLOCK = "staff.unlock"
    DEVICE_ENROLL = "device.enroll"
    DEVICE_REVOKE = "device.revoke"


class UnknownRole(ValueError):
    """A role string outside the closed vocabulary; the caller must reject the login."""


_P = Permission

_ROLE_GRANTS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.RECEPTION: frozenset(
            {
                _P.EVENTS_APPEND,
                _P.HANDOVER_START,
                _P.HANDOVER_COUNT_SUBMIT,
                _P.HANDOVER_COUNT_OUTGOING,
                _P.HANDOVER_CASH_SUBMIT,
                _P.HANDOVER_SIGN,
                _P.CASH_WITHDRAW_REQUEST,
                _P.STOCK_WITHDRAW_EMERGENCY,
                _P.STOCK_WASTE_REPORT,
                _P.PARCEL_HANDLE,
            }
        ),
        Role.NURSE: frozenset(
            {
                _P.EVENTS_APPEND,
                _P.HANDOVER_COUNT_SUBMIT,
                _P.HANDOVER_COUNT_OUTGOING,
                _P.CASH_WITHDRAW_REQUEST,
                _P.STOCK_RESTOCK_CONFIRM,
                _P.STOCK_WITHDRAW_EMERGENCY,
                _P.STOCK_WASTE_REPORT,
                _P.PARCEL_HANDLE,
            }
        ),
        Role.STOREKEEPER: frozenset(
            {
                _P.EVENTS_APPEND,
                _P.HANDOVER_COUNT_SUBMIT,
                _P.HANDOVER_COUNT_OUTGOING,
                _P.CASH_WITHDRAW_REQUEST,
                _P.STOCK_COUNT_SUBMIT,
                _P.STOCK_RESTOCK_CONFIRM,
                _P.STOCK_WITHDRAW_EMERGENCY,
                _P.STOCK_WASTE_REPORT,
                _P.PURCHASE_ORDER_DRAFT,
                _P.PURCHASE_DELIVERY_RECEIVE,
                _P.PARCEL_HANDLE,
            }
        ),
        Role.PURCHASER: frozenset(
            {
                _P.EVENTS_APPEND,
                _P.PURCHASE_ORDER_DRAFT,
                _P.PURCHASE_DELIVERY_RECEIVE,
                _P.PURCHASE_PAYMENT_RECORD,
            }
        ),
        Role.ACCOUNTANT: frozenset(
            {
                _P.EVENTS_APPEND,
                _P.HANDOVER_CASH_SUBMIT,
                _P.HANDOVER_SIGN,
                _P.CASH_DEPOSIT_RECORD,
                _P.STOCK_COUNT_SUBMIT,
                _P.PURCHASE_PAYMENT_RECORD,
            }
        ),
        Role.SHIFT_DOCTOR: frozenset(
            {
                _P.EVENTS_APPEND,
                _P.HANDOVER_COUNT_SUBMIT,
                _P.HANDOVER_COUNT_OUTGOING,
                _P.STOCK_WITHDRAW_EMERGENCY,
                _P.STOCK_WASTE_REPORT,
                _P.PARCEL_HANDLE,
            }
        ),
        Role.MANAGER: frozenset(Permission),
    }
)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = _ROLE_GRANTS


def parse_role(value: str) -> Role:
    """Return the role for `value`, raising `UnknownRole` for anything else."""
    try:
        return Role(value.strip())
    except ValueError as exc:
        raise UnknownRole(f"unknown role: {value!r}") from exc


def permissions_for(roles: Iterable[Role | str]) -> frozenset[Permission]:
    """Union of the permissions of every role a person holds (multi-role staff)."""
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS[parse_role(role) if isinstance(role, str) else role]
    return frozenset(granted)


def has_permission(roles: Iterable[Role | str], permission: Permission) -> bool:
    """True when any held role grants `permission`."""
    return permission in permissions_for(roles)
