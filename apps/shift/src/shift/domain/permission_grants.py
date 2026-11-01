"""Who holds which permission (R-07, R-08): pure data, roles union, `purchaser` is a role.

The vocabulary itself lives in `roles.py` so this matrix can grow with each feature without
crowding the enum. A permission nobody holds is a typo, not a feature; the unit matrix enforces
both the closed vocabulary and the manager's whole set.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from shift.domain.roles import Permission, Role, parse_role

_P = Permission

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
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
                _P.CARD_TRANSFER_REPORT,
                _P.REFUND_REQUEST,
                _P.CAMPAIGN_DISCOUNT_APPLY,
                _P.PATIENT_ITEM_HANDLE,
                _P.EQUIPMENT_FAULT_REPORT,
                _P.FAULT_FIX,
                _P.CLEANING_CONFIRM,
                _P.REWARD_VIEW,
                # ADR-021: the second receptionist of a shift says «حضور در شیفت» to come on shift.
                _P.SHIFT_PRESENCE_DECLARE,
                # R-15: a queued record made while off shift is held for the manager, never refused.
                _P.SHIFT_RECORD_HOLD,
                # Owner decision 9 (ADR-022 item 5): reception records the reception flow's first count.
                _P.HANDOVER_BASELINE_OWN,
                # The owner's document: the parcel arrives in whatever shift it arrives in, so the
                # counter takes it in and flags a problem instead of waiting for the storekeeper.
                _P.PURCHASE_DELIVERY_RECEIVE,
            }
        ),
        Role.NURSE: frozenset(
            {
                _P.EVENTS_APPEND,
                # H-12: the nursing flow needs its own start and its own two signatures (H-10: no box).
                _P.HANDOVER_START,
                _P.HANDOVER_SIGN,
                _P.HANDOVER_COUNT_SUBMIT,
                _P.HANDOVER_COUNT_OUTGOING,
                _P.CASH_WITHDRAW_REQUEST,
                _P.STOCK_RESTOCK_CONFIRM,
                _P.STOCK_WITHDRAW_EMERGENCY,
                _P.STOCK_WASTE_REPORT,
                _P.PARCEL_HANDLE,
                _P.PATIENT_ITEM_HANDLE,
                _P.EQUIPMENT_FAULT_REPORT,
                _P.FAULT_FIX,
                _P.VITALS_RECORD,
                _P.CLEANING_CONFIRM,
                _P.REWARD_VIEW,
                # R-15: the nurse's queued record made while off shift is held, not refused.
                _P.SHIFT_RECORD_HOLD,
                # Owner decision 9 (ADR-022 item 5): the nurse records the nursing flow's first count.
                _P.HANDOVER_BASELINE_OWN,
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
                _P.EQUIPMENT_FAULT_REPORT,
                _P.FAULT_FIX,
                _P.SHIFT_EVENT_APPROVE,
                _P.REWARD_VIEW,
            }
        ),
        Role.PURCHASER: frozenset(
            {
                _P.EVENTS_APPEND,
                # Only the purchaser hands an approved order to a vendor and records it (P-03). Owner
                # decision 2026-11-02: that is all the purchaser does; no draft, no receipt, no payment,
                # so no price and no stock figure ever reaches this role.
                _P.PURCHASE_ORDER_PLACE,
                _P.EQUIPMENT_FAULT_REPORT,
                _P.FAULT_FIX,
                _P.REWARD_VIEW,
            }
        ),
        # ADR-022 (owner decision 10): the accountant counts no cash and signs no handover. The
        # counters of the box are the two people of a reception flow; the accountant sees the result.
        Role.ACCOUNTANT: frozenset(
            {
                _P.EVENTS_APPEND,
                _P.CASH_DEPOSIT_RECORD,
                _P.STOCK_COUNT_SUBMIT,
                _P.PURCHASE_PAYMENT_RECORD,
                _P.EQUIPMENT_FAULT_REPORT,
                _P.FAULT_FIX,
                _P.SHIFT_EVENT_APPROVE,
                _P.REWARD_VIEW,
                _P.ACCOUNTANT_QUEUE_VIEW,
                _P.CAMPAIGN_REPORT_VIEW,
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
                _P.EQUIPMENT_FAULT_REPORT,
                _P.FAULT_FIX,
                _P.VITALS_RECORD,
                _P.SHIFT_EVENT_APPROVE,
                _P.REWARD_VIEW,
                # ADR-021: «حضور در شیفت»; R-15: an off-shift queued record is held, not refused.
                _P.SHIFT_PRESENCE_DECLARE,
                _P.SHIFT_RECORD_HOLD,
            }
        ),
        Role.MANAGER: frozenset(Permission),
    }
)


def permissions_for(roles: Iterable[Role | str]) -> frozenset[Permission]:
    """Union of the permissions of every role a person holds (multi-role staff)."""
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS[parse_role(role) if isinstance(role, str) else role]
    return frozenset(granted)


def has_permission(roles: Iterable[Role | str], permission: Permission) -> bool:
    """True when any held role grants `permission`."""
    return permission in permissions_for(roles)
