"""On-shift access control (ADR-021): handing over is the key to access.

The owner's decision: reception and nursing are shift-bound, the shift doctor is bound too but
declares presence instead of holding the box, and four roles (manager, accountant, storekeeper,
purchaser) are never bound. A person with both kinds keeps the free role's powers always and the
bound one's only while on shift.

`effective_permissions` is the one place that answer is computed; the server's permission
dependency reads it, so no route can be forgotten.
"""

from collections.abc import Iterable

from shift.domain.permission_grants import ROLE_PERMISSIONS
from shift.domain.permissions import Permission, Role, parse_role

# Roles whose powers only exist while the person is on shift (ADR-021).
BOUND_ROLES: frozenset[Role] = frozenset({Role.RECEPTION, Role.NURSE, Role.SHIFT_DOCTOR})

# Off shift a bound person keeps exactly these: taking over and handing over, and their own rewards.
ALWAYS_ALLOWED: frozenset[Permission] = frozenset(
    {
        Permission.HANDOVER_START,
        Permission.HANDOVER_COUNT_SUBMIT,
        Permission.HANDOVER_COUNT_OUTGOING,
        Permission.HANDOVER_CASH_SUBMIT,
        Permission.HANDOVER_SIGN,
        Permission.SHIFT_PRESENCE_DECLARE,
        # R-15: a queued record made off shift is kept for the manager, not refused at the door.
        Permission.SHIFT_RECORD_HOLD,
        Permission.REWARD_VIEW,
    }
)

# What an off-shift person reads on the home screen, in the clinic's words.
OFF_SHIFT_NOTICE = "شیفت شما باز نیست. برای شروع، شیفت را تحویل بگیرید."


def held_roles(roles: Iterable[Role | str]) -> frozenset[Role]:
    """The person's roles as a set, rejecting anything outside the closed vocabulary."""
    return frozenset(parse_role(role) if isinstance(role, str) else role for role in roles)


def effective_permissions(roles: Iterable[Role | str], on_shift: Iterable[Role | str] = ()) -> frozenset[Permission]:
    """The union of the free roles' grants, the bound roles' grants only on shift, plus the always set.

    Off shift a bound role keeps the part of the always set its own grant covers, so the handover
    screen still opens for the person who can walk it and holding it never hands somebody a step
    their role never had: a nurse still never counts the cash box (H-10). A role with no grant in
    `ALWAYS_ALLOWED` contributes nothing while off shift.
    """
    held = held_roles(roles)
    working = held_roles(on_shift)
    granted: set[Permission] = set()
    for role in held:
        if role not in BOUND_ROLES or role in working:
            granted |= ROLE_PERMISSIONS[role]
    kept = held & BOUND_ROLES - working
    if kept:
        bound_grants = set().union(*(ROLE_PERMISSIONS[role] for role in kept))
        granted |= bound_grants & ALWAYS_ALLOWED
    return frozenset(granted)


def has_bound_role(roles: Iterable[Role | str]) -> bool:
    """Whether any of the person's roles is shift-bound, so being off shift is a thing that can happen."""
    return bool(held_roles(roles) & BOUND_ROLES)


def is_on_shift(roles: Iterable[Role | str], on_shift: Iterable[Role | str]) -> bool:
    """True when the person holds no bound role at all, or is on shift for one of them."""
    held = held_roles(roles)
    working = held_roles(on_shift)
    return not (held & BOUND_ROLES) or bool(held & BOUND_ROLES & working)


def off_shift_notice(roles: Iterable[Role | str], on_shift: Iterable[Role | str]) -> str | None:
    """The Persian line an off-shift bound person reads, or None when there is nothing to say."""
    return None if is_on_shift(roles, on_shift) else OFF_SHIFT_NOTICE
