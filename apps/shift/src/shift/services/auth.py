"""Login, sessions and credential administration: mobile + PIN on an enrolled device.

Sessions are fixed 12-hour tokens (design §3): the cookie carries 32 random bytes and
the database only their SHA-256, so a leaked database cannot be replayed as a cookie.
Five consecutive wrong PINs lock the account until the manager unlocks it (S4).
"""

import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from sqlalchemy import Connection

from shift.adapters.db import devices_repo, sessions_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.devices_repo import DeviceRecord
from shift.adapters.db.sessions_repo import SessionRecord
from shift.adapters.db.staff_repo import StaffRecord
from shift.common.clock import Clock, SystemClock, parse_iso, utc_iso
from shift.common.hashing import sha256_hex
from shift.config.logging import get_audit_logger
from shift.domain.permissions import Permission, UnknownRole, permissions_for
from shift.domain.pin_policy import (
    LOCKOUT_MINUTES,
    MAX_FAILED_ATTEMPTS,
    normalise_digits,
    validate_new_pin,
)

SESSION_TTL = timedelta(hours=12)
SESSION_COOKIE = "shift_session"
TOKEN_BYTES = 32
_MOBILE = re.compile(r"09\d{9}")
# The one sentence every "this person may not log in" refusal carries: see `login`.
_WRONG_MOBILE_OR_PIN = "mobile or PIN is wrong"
PIN_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID)

log = get_audit_logger()


class AuthError(Exception):
    """Base class for every refusal the auth service can return."""


class InvalidCredentials(AuthError):
    """Mobile unknown, PIN wrong, or the mobile is not a mobile number."""


class AccountLocked(AuthError):
    """Too many wrong PINs; only a manager unlock clears the lock."""

    def __init__(self, until: datetime) -> None:
        super().__init__(f"account locked until {utc_iso(until)}")
        self.until = until


class StaffInactive(AuthError):
    """The staff row was deactivated; the PIN must not work any more."""


class DeviceNotEnrolled(AuthError):
    """The request did not come from a device paired to this staff member."""


class DeviceRevoked(AuthError):
    """The device was revoked; it may no longer authenticate anyone."""


class StaffNotFound(AuthError):
    """Credential administration asked for a staff id that does not exist."""


class SessionInvalid(AuthError):
    """No usable session for the presented cookie (`reason` says why)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"session rejected: {reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller of one request, with the union of its roles' powers."""

    staff_id: str
    full_name: str
    roles: tuple[str, ...]
    permissions: frozenset[Permission]
    device_id: str
    device_label: str
    session_hash: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A freshly minted session: the raw token goes into the cookie, nothing else."""

    token: str
    csrf_token: str
    principal: Principal


@dataclass(frozen=True, slots=True)
class StaffSummary:
    """A staff row as the credential administration screen shows it (never the hash)."""

    staff_id: str
    full_name: str
    mobile: str
    roles: tuple[str, ...]
    active: bool
    locked_until: str | None


def normalise_mobile(raw: str) -> str:
    """Return `09xxxxxxxxx`, accepting Persian digits, spaces, dashes and `+98`."""
    text = normalise_digits(raw).replace(" ", "").replace("-", "")
    if text.startswith("+98"):
        text = "0" + text[3:]
    elif text.startswith("0098"):
        text = "0" + text[4:]
    if not _MOBILE.fullmatch(text):
        raise InvalidCredentials("mobile must be an 09xxxxxxxxx number")
    return text


def pin_material(pin: str, pepper: str = "") -> str:
    """The value that is hashed: the install pepper, a separator, then the PIN.

    The separator keeps `("12", "345678")` and `("123", "45678")` distinct.
    """
    return f"{pepper}\x1f{pin}"


def hash_pin(pin: str, *, pepper: str = "", hasher: PasswordHasher = PIN_HASHER) -> str:
    """Argon2id digest of a PIN; the PIN itself is never stored or logged."""
    return hasher.hash(pin_material(pin, pepper))


def verify_pin(pin_hash: str, pin: str, *, pepper: str = "", hasher: PasswordHasher = PIN_HASHER) -> bool:
    """Constant-work PIN check that treats a corrupt digest as a mismatch."""
    try:
        return hasher.verify(pin_hash, pin_material(pin, pepper))
    except (VerificationError, InvalidHashError):
        return False


class AuthService:
    """Orchestrates staff rows, devices and sessions; no SQL and no HTTP types."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Clock | None = None,
        hasher: PasswordHasher = PIN_HASHER,
        session_ttl: timedelta = SESSION_TTL,
        pepper: str = "",
    ) -> None:
        self._database = database
        self._clock = clock or SystemClock()
        self._hasher = hasher
        self._session_ttl = session_ttl
        # The per-install secret from `Settings.secret_file`: a stolen database alone is
        # not enough to run an offline PIN search (S02 §4 asks for salt *and* pepper).
        self._pepper = pepper
        # Built on the first "unknown mobile" login, not at startup: verifying against a
        # real digest keeps that path as slow as a wrong PIN, so response time does not
        # reveal which mobiles exist.
        self._decoy_hash: str | None = None

    def login(self, *, mobile: str, pin: str, device_id: str) -> IssuedSession:
        """Open a session, or raise the specific `AuthError` that refused it.

        Argon2id runs outside every transaction and runs exactly once on every path — against
        a cached decoy digest when the mobile is unknown — so the single SQLite writer stays
        free for the ~0.3 s the hash needs (S2 single-writer contract) and no refusal can be
        told apart by how much CPU it cost.

        A phone enrolled for somebody else is refused exactly like an unknown mobile: the
        refusal types below describe the reason for the audit log, never for the client (all
        of them become one `401` envelope in `api.errors.describe_refusal`).
        """
        now = self._clock.now_utc()
        normalised_mobile = normalise_mobile(mobile)
        normalised_pin = normalise_digits(pin)
        device, staff = self._identify(device_id=device_id, mobile=normalised_mobile)
        matches = self._pin_matches(staff=staff, pin=normalised_pin)
        if staff is None:
            log.info("auth.login result=unknown_mobile device=%s", device_id)
            raise InvalidCredentials(_WRONG_MOBILE_OR_PIN)
        if not staff.active:
            log.info("auth.login result=inactive staff=%s", staff.staff_id)
            raise StaffInactive("staff account is deactivated")
        if device.staff_id != staff.staff_id:
            log.info("auth.login result=device_not_owned staff=%s device=%s", staff.staff_id, device_id)
            raise InvalidCredentials(_WRONG_MOBILE_OR_PIN)
        self._require_unlocked(staff, now)
        outcome = self._record_attempt(staff=staff, device=device, matches=matches, now=now)
        if isinstance(outcome, AuthError):
            raise outcome
        return outcome

    def _identify(self, *, device_id: str, mobile: str) -> tuple[DeviceRecord, StaffRecord | None]:
        """Read the phone and the staff row in one read transaction, never the writer."""
        with self._database.read() as connection:
            device = self._require_usable_device(devices_repo.find_device(connection, device_id))
            return device, staff_repo.find_by_mobile(connection, mobile)

    def _pin_matches(self, *, staff: StaffRecord | None, pin: str) -> bool:
        """One Argon2id verification: the real digest, or the decoy when nobody owns `staff`."""
        digest = staff.pin_hash if staff is not None else self._decoy()
        return verify_pin(digest, pin, pepper=self._pepper, hasher=self._hasher)

    def _record_attempt(
        self, *, staff: StaffRecord, device: DeviceRecord, matches: bool, now: datetime
    ) -> AuthError | IssuedSession:
        """Take the writer only to record the attempt or open the session, and let it commit.

        The row is re-read inside the transaction because the PIN was hashed outside it: the
        attempts other requests recorded meanwhile must be counted, not overwritten with the
        snapshot this request saw before hashing, and a PIN reset that landed in between must
        win over the digest this request verified (otherwise a reset could not end a session
        that was opened with the old PIN). A refusal is returned, not raised, so the
        transaction commits the counter before the caller raises.
        """
        with self._database.write() as connection:
            current = staff_repo.find(connection, staff.staff_id)
            if current is None or current.pin_hash != staff.pin_hash:
                return InvalidCredentials(_WRONG_MOBILE_OR_PIN)
            if not current.active:
                return StaffInactive("staff account is deactivated")
            until = self._locked_until(current)
            if until is not None and until > now:
                return AccountLocked(until)
            if not matches:
                return self._register_failure(connection, current, now)
            return self._issue(connection, current, device, now)

    def authenticate(self, token: str) -> Principal:
        """Resolve a session cookie into a `Principal`, failing closed on any doubt."""
        if not token:
            raise SessionInvalid("absent")
        now = self._clock.now_utc()
        with self._database.read() as connection:
            session = sessions_repo.find(connection, sha256_hex(token))
            if session is None:
                raise SessionInvalid("unknown")
            self._require_live_session(session, now)
            staff = staff_repo.find(connection, session.staff_id)
            if staff is None or not staff.active:
                raise SessionInvalid("staff_inactive")
            device = devices_repo.find_device(connection, session.device_id)
            if device is None or not device.is_active:
                raise SessionInvalid("device_revoked")
            return self._principal(connection, staff, device, session)

    def logout(self, *, token: str, reason: str = "logout") -> None:
        """Revoke the presented session; logging out twice is not an error."""
        now = utc_iso(self._clock.now_utc())
        with self._database.write() as connection:
            sessions_repo.revoke(connection, token_hash=sha256_hex(token), revoked_at=now, reason=reason)
        log.info("auth.logout reason=%s", reason)

    def revoke_staff_sessions(self, *, staff_ids: Sequence[str], reason: str) -> int:
        """Kill every live session of these people; the shift-close hook calls this."""
        now = utc_iso(self._clock.now_utc())
        revoked = 0
        with self._database.write() as connection:
            for staff_id in staff_ids:
                revoked += sessions_repo.revoke_for_staff(connection, staff_id=staff_id, revoked_at=now, reason=reason)
        log.info("auth.revoke_sessions reason=%s staff=%s count=%d", reason, list(staff_ids), revoked)
        return revoked

    def directory(self) -> Sequence[StaffSummary]:
        """Every staff member with roles and lock state, in one query."""
        with self._database.read() as connection:
            return [
                StaffSummary(
                    staff_id=entry.staff.staff_id,
                    full_name=entry.staff.full_name,
                    mobile=entry.staff.mobile,
                    roles=entry.roles,
                    active=entry.staff.active,
                    locked_until=entry.staff.locked_until,
                )
                for entry in staff_repo.list_with_roles(connection)
            ]

    def unlock(self, *, staff_id: str, actor_id: str) -> None:
        """Clear the failure counter and lock; the manager-only recovery path (S4)."""
        with self._database.write() as connection:
            staff = staff_repo.find(connection, staff_id)
            if staff is None:
                raise StaffNotFound(staff_id)
            staff_repo.clear_failures(connection, staff_id=staff_id)
        log.info("auth.unlock staff=%s actor=%s", staff_id, actor_id)

    def reset_pin(self, *, staff_id: str, pin: str, actor_id: str) -> None:
        """Set a policy-compliant PIN and drop every session of that person."""
        validated = validate_new_pin(pin)
        # Hashed before the writer is taken: Argon2id must never run inside `BEGIN IMMEDIATE`.
        digest = hash_pin(validated, pepper=self._pepper, hasher=self._hasher)
        now = self._clock.now_utc()
        with self._database.write() as connection:
            if staff_repo.find(connection, staff_id) is None:
                raise StaffNotFound(staff_id)
            staff_repo.set_pin_hash(connection, staff_id=staff_id, pin_hash=digest, updated_at=utc_iso(now))
            staff_repo.clear_failures(connection, staff_id=staff_id)
            sessions_repo.revoke_for_staff(connection, staff_id=staff_id, revoked_at=utc_iso(now), reason="pin_reset")
        log.info("auth.pin_reset staff=%s actor=%s", staff_id, actor_id)

    def _decoy(self) -> str:
        """A stable digest to verify against when the mobile is unknown (timing parity)."""
        if self._decoy_hash is None:
            self._decoy_hash = hash_pin("000000", pepper=self._pepper, hasher=self._hasher)
        return self._decoy_hash

    def _require_usable_device(self, device: DeviceRecord | None) -> DeviceRecord:
        if device is None:
            raise DeviceNotEnrolled("device is not enrolled")
        if not device.is_active:
            raise DeviceRevoked(f"device was revoked: {device.revoked_reason or 'unspecified'}")
        return device

    def _require_unlocked(self, staff: StaffRecord, now: datetime) -> None:
        until = self._locked_until(staff)
        if until is not None and until > now:
            raise AccountLocked(until)

    def _locked_until(self, staff: StaffRecord) -> datetime | None:
        """The lock deadline of a row, or `None` when it is not locked."""
        return None if staff.locked_until is None else parse_iso(staff.locked_until)

    def _require_live_session(self, session: SessionRecord, now: datetime) -> None:
        if session.revoked_at is not None:
            raise SessionInvalid(f"revoked:{session.revoked_reason or 'unspecified'}")
        if parse_iso(session.expires_at) <= now:
            raise SessionInvalid("expired")

    def _register_failure(self, connection: Connection, staff: StaffRecord, now: datetime) -> AuthError:
        """Count the wrong PIN; returns the refusal to raise once the write commits."""
        attempts = staff.failed_attempts + 1
        until = now + timedelta(minutes=LOCKOUT_MINUTES)
        if attempts >= MAX_FAILED_ATTEMPTS:
            staff_repo.record_failure(
                connection, staff_id=staff.staff_id, failed_attempts=0, locked_until=utc_iso(until)
            )
            log.info("auth.lock staff=%s until=%s", staff.staff_id, utc_iso(until))
            return AccountLocked(until)
        staff_repo.record_failure(connection, staff_id=staff.staff_id, failed_attempts=attempts, locked_until=None)
        log.info("auth.login result=bad_pin staff=%s attempts=%d", staff.staff_id, attempts)
        return InvalidCredentials("mobile or PIN is wrong")

    def _issue(self, connection: Connection, staff: StaffRecord, device: DeviceRecord, now: datetime) -> IssuedSession:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        csrf_token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = now + self._session_ttl
        session = SessionRecord(
            token_hash=sha256_hex(token),
            csrf_token=csrf_token,
            staff_id=staff.staff_id,
            device_id=device.device_id,
            issued_at=utc_iso(now),
            expires_at=utc_iso(expires_at),
            revoked_at=None,
            revoked_reason=None,
        )
        sessions_repo.insert(
            connection,
            token_hash=session.token_hash,
            csrf_token=csrf_token,
            staff_id=staff.staff_id,
            device_id=device.device_id,
            issued_at=session.issued_at,
            expires_at=session.expires_at,
        )
        if staff.failed_attempts:
            staff_repo.clear_failures(connection, staff_id=staff.staff_id)
        sessions_repo.purge_expired(connection, before=session.issued_at)
        principal = self._principal(connection, staff, device, session)
        log.info("auth.login result=ok staff=%s device=%s", staff.staff_id, device.device_id)
        return IssuedSession(token=token, csrf_token=csrf_token, principal=principal)

    def _principal(
        self, connection: Connection, staff: StaffRecord, device: DeviceRecord, session: SessionRecord
    ) -> Principal:
        roles = staff_repo.roles_for(connection, staff.staff_id)
        try:
            permissions = permissions_for(roles)
        except UnknownRole as exc:
            raise SessionInvalid(f"unknown_role:{exc}") from exc
        return Principal(
            staff_id=staff.staff_id,
            full_name=staff.full_name,
            roles=roles,
            permissions=permissions,
            device_id=device.device_id,
            device_label=device.label,
            session_hash=session.token_hash,
            csrf_token=session.csrf_token,
            expires_at=parse_iso(session.expires_at),
        )
