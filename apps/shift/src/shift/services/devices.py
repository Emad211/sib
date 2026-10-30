"""Device pairing: a one-time 10-minute token in a QR fragment creates one device.

The token is minted for a specific staff member and a pre-minted `device_id`; consuming
it is a single conditional UPDATE, so two phones scanning the same QR cannot both win
(design §3, step 8). Only the token's SHA-256 is stored.
"""

import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from shift.adapters.db import devices_repo, sessions_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.devices_repo import DeviceRecord
from shift.common.clock import Clock, SystemClock, parse_iso, utc_iso
from shift.common.hashing import sha256_hex
from shift.common.ids import new_ulid
from shift.config.logging import get_audit_logger
from shift.services.audit import AuditOutcome, AuditService, record_action

ENROLLMENT_TTL = timedelta(minutes=10)
ENROLLMENT_PATH = "/enroll#t="
TOKEN_BYTES = 32
LABEL_MAX = 64
PUBKEY_MAX = 4096
_PUBKEY = re.compile(r"[A-Za-z0-9+/=_\-.]+")

log = get_audit_logger()


class DeviceError(Exception):
    """Base class for refusals from the device service."""


class EnrollmentRejected(DeviceError):
    """The QR token is unknown, already used, expired, or its staff is gone."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"enrollment token rejected: {reason}")
        self.reason = reason


class DeviceNotFound(DeviceError):
    """No device row with that id."""


class UnknownStaff(DeviceError):
    """The staff member a token would be minted for does not exist or is inactive."""


@dataclass(frozen=True, slots=True)
class EnrollmentOffer:
    """What the admin screen shows once: the URL whose fragment carries the token."""

    device_id: str
    staff_id: str
    token: str
    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class EnrolledDevice:
    """The device as the phone and the admin screen see it; never the token."""

    device_id: str
    staff_id: str
    label: str
    pubkey: str
    enrolled_at: str
    revoked_at: str | None
    revoked_reason: str | None


def _summary(device: DeviceRecord) -> EnrolledDevice:
    return EnrolledDevice(
        device_id=device.device_id,
        staff_id=device.staff_id,
        label=device.label,
        pubkey=device.pubkey,
        enrolled_at=device.enrolled_at,
        revoked_at=device.revoked_at,
        revoked_reason=device.revoked_reason,
    )


def _normalise_label(label: str) -> str:
    text = " ".join(label.split())
    if not text or len(text) > LABEL_MAX:
        raise DeviceError(f"device label must be 1..{LABEL_MAX} characters")
    return text


def _normalise_pubkey(pubkey: str) -> str:
    text = pubkey.strip()
    if not 16 <= len(text) <= PUBKEY_MAX or not _PUBKEY.fullmatch(text):
        raise DeviceError("device public key must be 16..4096 base64 characters")
    return text


class DeviceService:
    """Mints enrollment tokens, enrolls phones and revokes them; no SQL, no HTTP."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Clock | None = None,
        token_ttl: timedelta = ENROLLMENT_TTL,
        audit: AuditService | None = None,
    ) -> None:
        self._database = database
        self._clock = clock or SystemClock()
        self._token_ttl = token_ttl
        # The lifespan injects the durable audit trail; a unit test without one keeps the sink.
        self._audit = audit

    def _note(
        self,
        action: str,
        message: str,
        message_args: tuple[object, ...],
        *,
        actor: str,
        outcome: AuditOutcome = AuditOutcome.OK,
        subject: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        """Audit one device action: a durable row plus the audit sink, never the public log."""
        record_action(
            self._audit,
            log,
            action=action,
            actor=actor,
            outcome=outcome,
            subject=subject,
            detail=detail,
            message=message,
            message_args=message_args,
        )

    def mint(self, *, staff_id: str, created_by: str, origin: str) -> EnrollmentOffer:
        """Create a single-use token for one staff member and the URL to render it in."""
        now = self._clock.now_utc()
        expires_at = now + self._token_ttl
        device_id = new_ulid(now)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        with self._database.write() as connection:
            staff = staff_repo.find(connection, staff_id)
            if staff is None or not staff.active:
                raise UnknownStaff(staff_id)
            devices_repo.purge_expired_tokens(connection, now=utc_iso(now))
            devices_repo.insert_token(
                connection,
                token_hash=sha256_hex(token),
                device_id=device_id,
                staff_id=staff_id,
                created_by=created_by,
                created_at=utc_iso(now),
                expires_at=utc_iso(expires_at),
            )
        url = f"{origin.rstrip('/')}{ENROLLMENT_PATH}{token}"
        self._note(
            "device.token_minted",
            "device.token_minted staff=%s device=%s by=%s",
            (staff_id, device_id, created_by),
            actor=created_by,
            subject=device_id,
            detail={"staff": staff_id},
        )
        return EnrollmentOffer(device_id=device_id, staff_id=staff_id, token=token, url=url, expires_at=expires_at)

    def enroll(self, *, token: str, label: str, pubkey: str) -> EnrolledDevice:
        """Consume a token and pair the phone; every refusal leaves the token usable."""
        cleaned_label = _normalise_label(label)
        cleaned_pubkey = _normalise_pubkey(pubkey)
        now = self._clock.now_utc()
        digest = sha256_hex(token.strip())
        with self._database.write() as connection:
            record = devices_repo.find_token(connection, digest)
            if record is None:
                raise EnrollmentRejected("unknown")
            if record.consumed_at is not None:
                raise EnrollmentRejected("used")
            if parse_iso(record.expires_at) <= now:
                raise EnrollmentRejected("expired")
            staff = staff_repo.find(connection, record.staff_id)
            if staff is None or not staff.active:
                raise EnrollmentRejected("staff_inactive")
            if not devices_repo.consume_token(connection, token_hash=digest, now=utc_iso(now)):
                raise EnrollmentRejected("used")
            devices_repo.insert_device(
                connection,
                device_id=record.device_id,
                staff_id=record.staff_id,
                label=cleaned_label,
                pubkey=cleaned_pubkey,
                enrolled_at=utc_iso(now),
            )
        self._note(
            "device.enrolled",
            "device.enrolled staff=%s device=%s label=%s",
            (record.staff_id, record.device_id, cleaned_label),
            actor=record.staff_id,
            subject=record.device_id,
            detail={"staff": record.staff_id, "label": cleaned_label},
        )
        return EnrolledDevice(
            device_id=record.device_id,
            staff_id=record.staff_id,
            label=cleaned_label,
            pubkey=cleaned_pubkey,
            enrolled_at=utc_iso(now),
            revoked_at=None,
            revoked_reason=None,
        )

    def revoke(self, *, device_id: str, actor_id: str, reason: str = "revoked_by_manager") -> int:
        """Revoke a device and every live session it opened; returns sessions killed."""
        now = utc_iso(self._clock.now_utc())
        with self._database.write() as connection:
            if devices_repo.find_device(connection, device_id) is None:
                raise DeviceNotFound(device_id)
            devices_repo.revoke_device(connection, device_id=device_id, revoked_at=now, reason=reason)
            killed = sessions_repo.revoke_for_device(
                connection, device_id=device_id, revoked_at=now, reason="device_revoked"
            )
        self._note(
            "device.revoked",
            "device.revoked device=%s by=%s sessions=%d",
            (device_id, actor_id, killed),
            actor=actor_id,
            subject=device_id,
            detail={"sessions": killed, "reason": reason},
        )
        return killed

    def list_devices(self) -> Sequence[EnrolledDevice]:
        """Every device, newest first."""
        with self._database.read() as connection:
            return [_summary(device) for device in devices_repo.list_devices(connection)]
