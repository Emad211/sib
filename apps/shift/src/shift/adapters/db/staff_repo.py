"""SQL access for staff identities and their roles (mobile + PIN login, R-08)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

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


def roles_for(connection: Connection, staff_id: str) -> tuple[str, ...]:
    """Every role the person holds, in a stable order."""
    rows = connection.exec_driver_sql("SELECT role FROM staff_role WHERE staff_id = ? ORDER BY role", (staff_id,))
    return tuple(str(row[0]) for row in rows)


def list_with_roles(connection: Connection) -> list[StaffWithRoles]:
    """All staff with their roles in one query (no per-row round trip)."""
    rows = connection.exec_driver_sql(
        "SELECT s.staff_id, s.full_name, s.mobile, s.pin_hash, s.pin_updated_at, s.active, "
        "s.failed_attempts, s.locked_until, s.created_at, r.role "
        "FROM staff AS s LEFT JOIN staff_role AS r ON r.staff_id = s.staff_id "
        "ORDER BY s.full_name, s.staff_id, r.role"
    ).mappings()
    grouped: dict[str, tuple[StaffRecord, list[str]]] = {}
    for row in rows:
        staff_id = str(row["staff_id"])
        if staff_id not in grouped:
            grouped[staff_id] = (_record(row), [])
        if row["role"] is not None:
            grouped[staff_id][1].append(str(row["role"]))
    return [StaffWithRoles(staff, tuple(roles)) for staff, roles in grouped.values()]


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
