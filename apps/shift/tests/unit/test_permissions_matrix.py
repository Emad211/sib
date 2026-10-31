"""The role/permission matrix (R-07, R-08): closed vocabulary, least privilege, union."""

import pytest
from hypothesis import given, strategies

from shift.domain.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    UnknownRole,
    has_permission,
    parse_role,
    permissions_for,
)

ALL_ROLES = sorted(role.value for role in Role)
NON_MANAGER_ROLES = [role for role in ALL_ROLES if role != Role.MANAGER]


def test_every_permission_is_granted_to_someone() -> None:
    granted = set().union(*ROLE_PERMISSIONS.values())

    assert granted == set(Permission), "a permission nobody holds can only be a typo"


def test_the_manager_holds_the_entire_vocabulary() -> None:
    assert ROLE_PERMISSIONS[Role.MANAGER] == frozenset(Permission)


@pytest.mark.parametrize("role", NON_MANAGER_ROLES)
def test_only_the_manager_may_approve_spending_and_change_the_system(role: str) -> None:
    forbidden = {
        Permission.CASH_WITHDRAW_APPROVE,
        Permission.PURCHASE_ORDER_DECIDE,
        Permission.CATALOG_MANAGE,
        Permission.SETTINGS_CHANGE,
        Permission.STAFF_MANAGE,
        Permission.STAFF_PIN_RESET,
        Permission.STAFF_UNLOCK,
        Permission.DEVICE_ENROLL,
        Permission.DEVICE_REVOKE,
    }

    assert not (ROLE_PERMISSIONS[parse_role(role)] & forbidden)


def test_purchaser_is_a_role_with_the_buying_permissions() -> None:
    granted = ROLE_PERMISSIONS[Role.PURCHASER]

    assert Role.PURCHASER.value == "purchaser"
    assert {Permission.PURCHASE_ORDER_DRAFT, Permission.PURCHASE_DELIVERY_RECEIVE} <= granted
    assert Permission.PURCHASE_ORDER_DECIDE not in granted, "whoever drafts must not approve"


def test_reception_keeps_the_counter_and_loses_the_store() -> None:
    granted = ROLE_PERMISSIONS[Role.RECEPTION]

    assert Permission.HANDOVER_CASH_SUBMIT in granted
    assert Permission.STOCK_COUNT_SUBMIT not in granted
    assert Permission.CASH_DEPOSIT_RECORD not in granted


def test_the_nurse_starts_and_signs_the_nursing_flow_but_never_the_cash() -> None:
    """H-12 gives the nursing flow its own two signatures, so a nurse holds start and sign (H-10)."""
    granted = ROLE_PERMISSIONS[Role.NURSE]

    assert Permission.HANDOVER_START in granted
    assert Permission.HANDOVER_SIGN in granted
    assert Permission.HANDOVER_CASH_SUBMIT not in granted, "only the reception flow counts the box"


def test_multi_role_staff_get_the_union_and_not_more() -> None:
    combined = permissions_for(["reception", "accountant"])

    assert combined == ROLE_PERMISSIONS[Role.RECEPTION] | ROLE_PERMISSIONS[Role.ACCOUNTANT]
    assert ROLE_PERMISSIONS[Role.RECEPTION] < combined
    assert ROLE_PERMISSIONS[Role.ACCOUNTANT] < combined


def test_unknown_roles_are_rejected_instead_of_ignored() -> None:
    with pytest.raises(UnknownRole):
        parse_role("purchasing_owner")
    with pytest.raises(UnknownRole):
        permissions_for(["reception", "owner"])


def test_role_names_are_normalised_but_never_case_folded() -> None:
    assert parse_role(" reception ") is Role.RECEPTION
    assert has_permission([" nurse "], Permission.STOCK_RESTOCK_CONFIRM)
    with pytest.raises(UnknownRole):
        parse_role("NURSE")


def test_the_matrix_cannot_be_edited_at_runtime() -> None:
    with pytest.raises(TypeError):
        ROLE_PERMISSIONS[Role.NURSE] = frozenset()  # type: ignore[index]
    assert all(isinstance(granted, frozenset) for granted in ROLE_PERMISSIONS.values())
    assert set(ROLE_PERMISSIONS) == set(Role)


@given(strategies.lists(strategies.sampled_from(ALL_ROLES), min_size=1, max_size=4))
def test_a_person_with_more_roles_never_loses_a_permission(roles: list[str]) -> None:
    smaller = permissions_for(roles[:-1]) if len(roles) > 1 else frozenset()
    larger = permissions_for(roles)

    assert smaller <= larger
    assert permissions_for(roles) == permissions_for(set(roles)), "roles are a set, not a list"
