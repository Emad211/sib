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
        Permission.HANDOVER_BASELINE,
        Permission.SHIFT_PRESENCE_DECLARE,
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

    A role with no grant in `ALWAYS_ALLOWED` contributes nothing while off shift, so a receptionist
    who has signed over sees the handover screen and their rewards and nothing else (ADR-021).
    """
    held = held_roles(roles)
    working = held_roles(on_shift)
    granted: set[Permission] = set(ALWAYS_ALLOWED)
    for role in held:
        if role not in BOUND_ROLES or role in working:
            granted |= ROLE_PERMISSIONS[role]
    return frozenset(granted)


def is_on_shift(roles: Iterable[Role | str], on_shift: Iterable[Role | str]) -> bool:
    """True when the person holds no bound role at all, or is on shift for one of them."""
    held = held_roles(roles)
    working = held_roles(on_shift)
    return not (held & BOUND_ROLES) or bool(held & BOUND_ROLES & working)


def off_shift_notice(roles: Iterable[Role | str], on_shift: Iterable[Role | str]) -> str | None:
    """The Persian line an off-shift bound person reads, or None when there is nothing to say."""
    return None if is_on_shift(roles, on_shift) else OFF_SHIFT_NOTICE
