"""SQL access for enrolled devices and one-time enrollment tokens (design §3, step 8)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_DEVICE_COLUMNS = ("device_id", "staff_id", "label", "pubkey", "enrolled_at", "revoked_at", "revoked_reason")
_TOKEN_COLUMNS = ("token_hash", "device_id", "staff_id", "created_by", "created_at", "expires_at", "consumed_at")
_DEVICE_SELECT = f"SELECT {', '.join(_DEVICE_COLUMNS)} FROM devices"
_TOKEN_SELECT = f"SELECT {', '.join(_TOKEN_COLUMNS)} FROM enrollment_tokens"


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """A paired phone; `revoked_at` set means the device may no longer log in."""

    device_id: str
    staff_id: str
    label: str
    pubkey: str
    enrolled_at: str
    revoked_at: str | None
    revoked_reason: str | None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class EnrollmentTokenRecord:
    """A one-time pairing token, stored only as its SHA-256 digest."""

    token_hash: str
    device_id: str
    staff_id: str
    created_by: str
    created_at: str
    expires_at: str
    consumed_at: str | None


def _device(row: Mapping[str, Any]) -> DeviceRecord:
    revoked_at = row["revoked_at"]
    revoked_reason = row["revoked_reason"]
    return DeviceRecord(
        device_id=str(row["device_id"]),
        staff_id=str(row["staff_id"]),
        label=str(row["label"]),
        pubkey=str(row["pubkey"]),
        enrolled_at=str(row["enrolled_at"]),
        revoked_at=None if revoked_at is None else str(revoked_at),
        revoked_reason=None if revoked_reason is None else str(revoked_reason),
    )


def _token(row: Mapping[str, Any]) -> EnrollmentTokenRecord:
    consumed_at = row["consumed_at"]
    return EnrollmentTokenRecord(
        token_hash=str(row["token_hash"]),
        device_id=str(row["device_id"]),
        staff_id=str(row["staff_id"]),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
        expires_at=str(row["expires_at"]),
        consumed_at=None if consumed_at is None else str(consumed_at),
    )


def insert_device(
    connection: Connection,
    *,
    device_id: str,
    staff_id: str,
    label: str,
    pubkey: str,
    enrolled_at: str,
) -> None:
    """Register a paired device for one staff member."""
    connection.exec_driver_sql(
        "INSERT INTO devices (device_id, staff_id, label, pubkey, enrolled_at) VALUES (?, ?, ?, ?, ?)",
        (device_id, staff_id, label, pubkey, enrolled_at),
    )


def find_device(connection: Connection, device_id: str) -> DeviceRecord | None:
    """Return one device by its id."""
    row = connection.exec_driver_sql(f"{_DEVICE_SELECT} WHERE device_id = ? LIMIT 1", (device_id,)).mappings().first()
    return _device(row) if row is not None else None


def list_devices(connection: Connection) -> list[DeviceRecord]:
    """Every device, newest first, for the administration screen."""
    rows = connection.exec_driver_sql(f"{_DEVICE_SELECT} ORDER BY enrolled_at DESC, device_id").mappings()
    return [_device(row) for row in rows]


def revoke_device(connection: Connection, *, device_id: str, revoked_at: str, reason: str) -> int:
    """Mark a device revoked once; returns 1 the first time and 0 afterwards."""
    result = connection.exec_driver_sql(
        "UPDATE devices SET revoked_at = ?, revoked_reason = ? WHERE device_id = ? AND revoked_at IS NULL",
        (revoked_at, reason, device_id),
    )
    return int(result.rowcount or 0)


def insert_token(
    connection: Connection,
    *,
    token_hash: str,
    device_id: str,
    staff_id: str,
    created_by: str,
    created_at: str,
    expires_at: str,
) -> None:
    """Record a one-time token bound to the device id it may create."""
    connection.exec_driver_sql(
        "INSERT INTO enrollment_tokens (token_hash, device_id, staff_id, created_by, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token_hash, device_id, staff_id, created_by, created_at, expires_at),
    )


def find_token(connection: Connection, token_hash: str) -> EnrollmentTokenRecord | None:
    """Return an enrollment token by digest, consumed or not."""
    row = connection.exec_driver_sql(f"{_TOKEN_SELECT} WHERE token_hash = ? LIMIT 1", (token_hash,)).mappings().first()
    return _token(row) if row is not None else None


def consume_token(connection: Connection, *, token_hash: str, now: str) -> bool:
    """Claim a live token atomically; `False` means it was used, expired or unknown."""
    result = connection.exec_driver_sql(
        "UPDATE enrollment_tokens SET consumed_at = ? WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?",
        (now, token_hash, now),
    )
    return int(result.rowcount or 0) == 1


def purge_expired_tokens(connection: Connection, *, now: str) -> int:
    """Delete tokens that expired before `now`; keeps the table small."""
    result = connection.exec_driver_sql("DELETE FROM enrollment_tokens WHERE expires_at <= ?", (now,))
    return int(result.rowcount or 0)
