"""SQL access for login sessions: one row per issued cookie, revocable by staff or device."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_COLUMNS = (
    "token_hash",
    "csrf_token",
    "staff_id",
    "device_id",
    "issued_at",
    "expires_at",
    "revoked_at",
    "revoked_reason",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM sessions"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """An issued session; the raw token is never stored, only its SHA-256."""

    token_hash: str
    csrf_token: str
    staff_id: str
    device_id: str
    issued_at: str
    expires_at: str
    revoked_at: str | None
    revoked_reason: str | None


def _record(row: Mapping[str, Any]) -> SessionRecord:
    revoked_at = row["revoked_at"]
    revoked_reason = row["revoked_reason"]
    return SessionRecord(
        token_hash=str(row["token_hash"]),
        csrf_token=str(row["csrf_token"]),
        staff_id=str(row["staff_id"]),
        device_id=str(row["device_id"]),
        issued_at=str(row["issued_at"]),
        expires_at=str(row["expires_at"]),
        revoked_at=None if revoked_at is None else str(revoked_at),
        revoked_reason=None if revoked_reason is None else str(revoked_reason),
    )


def insert(
    connection: Connection,
    *,
    token_hash: str,
    csrf_token: str,
    staff_id: str,
    device_id: str,
    issued_at: str,
    expires_at: str,
) -> None:
    """Store one issued session."""
    connection.exec_driver_sql(
        "INSERT INTO sessions (token_hash, csrf_token, staff_id, device_id, issued_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token_hash, csrf_token, staff_id, device_id, issued_at, expires_at),
    )


def find(connection: Connection, token_hash: str) -> SessionRecord | None:
    """Return a session by cookie digest, whoever it belongs to."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE token_hash = ? LIMIT 1", (token_hash,)).mappings().first()
    return _record(row) if row is not None else None


def revoke(connection: Connection, *, token_hash: str, revoked_at: str, reason: str) -> int:
    """Revoke one session once; returns 1 the first time and 0 afterwards."""
    result = connection.exec_driver_sql(
        "UPDATE sessions SET revoked_at = ?, revoked_reason = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (revoked_at, reason, token_hash),
    )
    return int(result.rowcount or 0)


def revoke_for_staff(connection: Connection, *, staff_id: str, revoked_at: str, reason: str) -> int:
    """Revoke every live session of one person (shift close, PIN reset)."""
    result = connection.exec_driver_sql(
        "UPDATE sessions SET revoked_at = ?, revoked_reason = ? WHERE staff_id = ? AND revoked_at IS NULL",
        (revoked_at, reason, staff_id),
    )
    return int(result.rowcount or 0)


def cap_for_staff(connection: Connection, *, staff_id: str, until: str) -> int:
    """End every live session of one person at `until` at the latest (the shift-close wind-down)."""
    result = connection.exec_driver_sql(
        "UPDATE sessions SET expires_at = ? WHERE staff_id = ? AND revoked_at IS NULL AND expires_at > ?",
        (until, staff_id, until),
    )
    return int(result.rowcount or 0)


def revoke_for_device(connection: Connection, *, device_id: str, revoked_at: str, reason: str) -> int:
    """Revoke every live session opened from one device (device revocation)."""
    result = connection.exec_driver_sql(
        "UPDATE sessions SET revoked_at = ?, revoked_reason = ? WHERE device_id = ? AND revoked_at IS NULL",
        (revoked_at, reason, device_id),
    )
    return int(result.rowcount or 0)


def purge_expired(connection: Connection, *, before: str) -> int:
    """Delete sessions that expired before `before`; the table never grows unbounded."""
    result = connection.exec_driver_sql("DELETE FROM sessions WHERE expires_at <= ?", (before,))
    return int(result.rowcount or 0)
