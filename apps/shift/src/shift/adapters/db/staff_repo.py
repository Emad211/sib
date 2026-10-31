"""SQL access for staff identities and their roles (mobile + PIN login, R-08)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import catalog_repo

_COLUMNS = (
    "staff_id",
    "full_name",
    "mobile",
    "pin_hash",
    "pin_updated_at",
    "active",
    "failed_attempts",
    "locked_until",
    "created_at",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM staff"


@dataclass(frozen=True, slots=True)
class StaffRecord:
    """One staff row; `pin_hash` is an argon2id digest and never leaves the service."""

    staff_id: str
    full_name: str
    mobile: str
    pin_hash: str
    pin_updated_at: str
    active: bool
    failed_attempts: int
    locked_until: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class StaffWithRoles:
    """A staff row plus every role held, for the credential administration screen."""

    staff: StaffRecord
    roles: tuple[str, ...]


def _record(row: Mapping[str, Any]) -> StaffRecord:
    locked_until = row["locked_until"]
    return StaffRecord(
        staff_id=str(row["staff_id"]),
        full_name=str(row["full_name"]),
        mobile=str(row["mobile"]),
        pin_hash=str(row["pin_hash"]),
        pin_updated_at=str(row["pin_updated_at"]),
        active=bool(row["active"]),
        failed_attempts=int(row["failed_attempts"]),
        locked_until=None if locked_until is None else str(locked_until),
        created_at=str(row["created_at"]),
    )


def insert(
    connection: Connection,
    *,
    staff_id: str,
    full_name: str,
    mobile: str,
    pin_hash: str,
    created_at: str,
) -> None:
    """Create one active staff row with a fresh PIN and no failed attempts."""
    connection.exec_driver_sql(
        "INSERT INTO staff (staff_id, full_name, mobile, pin_hash, pin_updated_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (staff_id, full_name, mobile, pin_hash, created_at, created_at),
    )


def find(connection: Connection, staff_id: str) -> StaffRecord | None:
    """Return one staff row by id."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE staff_id = ? LIMIT 1", (staff_id,)).mappings().first()
    return _record(row) if row is not None else None


def find_by_mobile(connection: Connection, mobile: str) -> StaffRecord | None:
    """Return the staff row that owns a normalised mobile number."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE mobile = ? LIMIT 1", (mobile,)).mappings().first()
    return _record(row) if row is not None else None


def insert_role(connection: Connection, *, staff_id: str, role: str, granted_at: str) -> None:
    """Grant one role; re-granting the same role is a no-op."""
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO staff_role (staff_id, role, granted_at) VALUES (?, ?, ?)",
        (staff_id, role, granted_at),
    )


def _overlay(base: set[str], decisions: Mapping[str, bool]) -> tuple[str, ...]:
    """One source of truth for a person's roles (ADR-022): the catalog decision wins, the base fills the gaps.

    `staff_role` holds the roles a person was given when they were created (and the older installs'
    roles); `catalog_staff_role_versions` holds every later change, effective from one shift. For each
    role the newest decision that has started decides; a role with no decision keeps what `staff_role`
    says. Nothing is ever deleted, so history stays and the answer is the same on every machine.
    """
    held = {role for role in base if decisions.get(role, True)}
    held |= {role for role, granted in decisions.items() if granted}
    return tuple(sorted(held))


def roles_for(connection: Connection, staff_id: str, shift_key: str | None = None) -> tuple[str, ...]:
    """Every role the person holds, in a stable order.

    With `shift_key` the roles are the ones that shift sees (the manager's changes apply from the next
    shift, R-09); without it they are the base roles alone, which is what a fresh account carries.
    """
    rows = connection.exec_driver_sql("SELECT role FROM staff_role WHERE staff_id = ? ORDER BY role", (staff_id,))
    base = {str(row[0]) for row in rows}
    if shift_key is None:
        return tuple(sorted(base))
    decisions = {
        row.role: row.granted for row in catalog_repo.list_staff_roles_effective(connection, staff_id, shift_key)
    }
    return _overlay(base, decisions)


def list_with_roles(connection: Connection, shift_key: str | None = None) -> list[StaffWithRoles]:
    """All staff with their roles (as `shift_key` sees them) in two queries, not one per person."""
    rows = connection.exec_driver_sql(
        "SELECT s.staff_id, s.full_name, s.mobile, s.pin_hash, s.pin_updated_at, s.active, "
        "s.failed_attempts, s.locked_until, s.created_at, r.role "
        "FROM staff AS s LEFT JOIN staff_role AS r ON r.staff_id = s.staff_id "
        "ORDER BY s.full_name, s.staff_id, r.role"
    ).mappings()
    grouped: dict[str, tuple[StaffRecord, set[str]]] = {}
    for row in rows:
        staff_id = str(row["staff_id"])
        if staff_id not in grouped:
            grouped[staff_id] = (_record(row), set())
        if row["role"] is not None:
            grouped[staff_id][1].add(str(row["role"]))
    decisions: dict[str, dict[str, bool]] = {staff_id: {} for staff_id in grouped}
    if shift_key is not None:
        for staff_id in grouped:
            for row in catalog_repo.list_staff_roles_effective(connection, staff_id, shift_key):
                decisions[staff_id][row.role] = row.granted
    return [
        StaffWithRoles(
            staff, _overlay(base, decisions[staff.staff_id]) if shift_key is not None else tuple(sorted(base))
        )
        for staff, base in grouped.values()
    ]


def set_active(connection: Connection, *, staff_id: str, active: bool) -> None:
    """Deactivate (or reactivate) an account; a deactivated person cannot log in."""
    connection.exec_driver_sql("UPDATE staff SET active = ? WHERE staff_id = ?", (int(active), staff_id))


def set_pin_hash(connection: Connection, *, staff_id: str, pin_hash: str, updated_at: str) -> None:
    """Replace the PIN digest and stamp when it changed."""
    connection.exec_driver_sql(
        "UPDATE staff SET pin_hash = ?, pin_updated_at = ? WHERE staff_id = ?",
        (pin_hash, updated_at, staff_id),
    )


def record_failure(connection: Connection, *, staff_id: str, failed_attempts: int, locked_until: str | None) -> None:
    """Store the failure counter and lock deadline computed by the PIN policy."""
    connection.exec_driver_sql(
        "UPDATE staff SET failed_attempts = ?, locked_until = ? WHERE staff_id = ?",
        (failed_attempts, locked_until, staff_id),
    )


def clear_failures(connection: Connection, *, staff_id: str) -> None:
    """Forget the failure counter and any lock (successful login or manager unlock)."""
    connection.exec_driver_sql(
        "UPDATE staff SET failed_attempts = 0, locked_until = NULL WHERE staff_id = ?",
        (staff_id,),
    )
