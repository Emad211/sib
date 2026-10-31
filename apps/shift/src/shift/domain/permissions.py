"""Role and permission matrix (R-07, R-08): pure data; roles union, `purchaser` is a role.

The closed vocabulary lives in `roles.py` and the grants in `permission_grants.py`; this module is
the stable import path every caller already uses, so the split stays invisible to them.
"""

from shift.domain.permission_grants import ROLE_PERMISSIONS, has_permission, permissions_for
from shift.domain.roles import Permission, Role, UnknownRole, parse_role

__all__ = [
    "ROLE_PERMISSIONS",
    "Permission",
    "Role",
    "UnknownRole",
    "has_permission",
    "parse_role",
    "permissions_for",
]
