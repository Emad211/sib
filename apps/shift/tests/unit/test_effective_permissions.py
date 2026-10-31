"""On-shift access control (ADR-021): the effective set per role and per combination.

Full depth because this is auth: a mistake here either locks the clinic out or hands a signed-over
person the box again.
"""

import pytest

from shift.domain.on_shift import (
    ALWAYS_ALLOWED,
    BOUND_ROLES,
    OFF_SHIFT_NOTICE,
    effective_permissions,
    is_on_shift,
    off_shift_notice,
)
from shift.domain.permissions import ROLE_PERMISSIONS, Permission, Role, permissions_for

FREE_ROLES = (Role.MANAGER, Role.ACCOUNTANT, Role.STOREKEEPER, Role.PURCHASER)
BOUND = (Role.RECEPTION, Role.NURSE, Role.SHIFT_DOCTOR)


@pytest.mark.parametrize("role", FREE_ROLES)
def test_a_free_role_never_waits_for_a_shift(role: Role) -> None:
    assert effective_permissions([role]) == permissions_for([role])
    assert is_on_shift([role], []) is True


@pytest.mark.parametrize("role", BOUND)
def test_a_bound_role_off_shift_keeps_only_what_the_always_set_covers(role: Role) -> None:
    off = effective_permissions([role])

    assert off == ALWAYS_ALLOWED & permissions_for([role])
    assert not off & (permissions_for([role]) - ALWAYS_ALLOWED)
    assert is_on_shift([role], []) is False


@pytest.mark.parametrize("role", BOUND)
def test_a_bound_role_on_shift_gets_its_whole_grant(role: Role) -> None:
    assert effective_permissions([role], [role]) == permissions_for([role])


def test_the_bound_set_is_the_three_owner_named_roles() -> None:
    assert BOUND_ROLES == {Role.RECEPTION, Role.NURSE, Role.SHIFT_DOCTOR}


def test_the_always_set_is_handover_rewards_and_the_presence_button() -> None:
    assert ALWAYS_ALLOWED == {
        Permission.HANDOVER_START,
        Permission.HANDOVER_COUNT_SUBMIT,
        Permission.HANDOVER_COUNT_OUTGOING,
        Permission.HANDOVER_CASH_SUBMIT,
        Permission.HANDOVER_SIGN,
        # Owner decision 9: the first count happens before this person holds any custody.
        Permission.HANDOVER_BASELINE_OWN,
        Permission.SHIFT_PRESENCE_DECLARE,
        Permission.SHIFT_RECORD_HOLD,
        Permission.REWARD_VIEW,
    }, "off shift: take over, count first and hand over, say they are here, hand a record in, read rewards"


def test_a_multi_role_person_keeps_the_free_half_off_shift() -> None:
    """ADR-021: storekeeper and nurse keeps the store always and the nursing only on shift."""
    off = effective_permissions(["storekeeper", "nurse"])

    assert off == ROLE_PERMISSIONS[Role.STOREKEEPER] | (ROLE_PERMISSIONS[Role.NURSE] & ALWAYS_ALLOWED)
    assert Permission.STOCK_COUNT_SUBMIT in off, "the free role's powers never wait"
    assert Permission.VITALS_RECORD not in off, "the bound role's powers wait"
    assert Permission.VITALS_RECORD in effective_permissions(["storekeeper", "nurse"], ["nurse"])


def test_a_receptionist_who_also_keeps_the_books_stays_the_accountant() -> None:
    off = effective_permissions([Role.RECEPTION, Role.ACCOUNTANT])

    assert off == ROLE_PERMISSIONS[Role.ACCOUNTANT] | ALWAYS_ALLOWED
    assert Permission.ACCOUNTANT_QUEUE_VIEW in off
    assert Permission.PARCEL_HANDLE not in off, "the counter's own work is on shift only"
    assert Permission.PARCEL_HANDLE in effective_permissions([Role.RECEPTION, Role.ACCOUNTANT], [Role.RECEPTION])


def test_a_person_with_more_bound_roles_never_loses_a_permission() -> None:
    one = effective_permissions([Role.RECEPTION, Role.NURSE], [Role.RECEPTION])
    both = effective_permissions([Role.RECEPTION, Role.NURSE], [Role.RECEPTION, Role.NURSE])

    assert one < both


def test_the_notice_is_persian_and_only_for_a_bound_role() -> None:
    assert OFF_SHIFT_NOTICE == "شیفت شما باز نیست. برای شروع، شیفت را تحویل بگیرید."
    assert off_shift_notice([Role.NURSE], []) == OFF_SHIFT_NOTICE
    assert off_shift_notice([Role.NURSE], [Role.NURSE]) is None
    assert off_shift_notice([Role.MANAGER], []) is None


def test_the_always_set_never_holds_a_role_that_moves_money() -> None:
    """No deposit, no payment, no cash approve: an off-shift person moves nothing (ADR-021)."""
    forbidden = {
        Permission.CASH_DEPOSIT_RECORD,
        Permission.PURCHASE_PAYMENT_RECORD,
        Permission.CASH_WITHDRAW_APPROVE,
        Permission.STOCK_COUNT_SUBMIT,
    }

    assert not ALWAYS_ALLOWED & forbidden
