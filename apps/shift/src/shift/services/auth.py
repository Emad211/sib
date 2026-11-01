"""Login, sessions and credential administration: mobile + PIN on an enrolled device.

Sessions are fixed 12-hour tokens (design §3): the cookie carries 32 random bytes and
the database only their SHA-256, so a leaked database cannot be replayed as a cookie.
Five consecutive wrong PINs lock the account until the manager unlocks it (S4).
"""

import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from shift.adapters.db import devices_repo, sessions_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.devices_repo import DeviceRecord
from shift.adapters.db.sessions_repo import SessionRecord
from shift.adapters.db.staff_repo import StaffRecord
from shift.common.clock import Clock, SystemClock, parse_iso, to_tehran, utc_iso
from shift.common.hashing import sha256_hex
from shift.common.ids import new_ulid
from shift.config.logging import get_audit_logger
from shift.domain.on_shift import effective_permissions
from shift.domain.permissions import Permission, Role, UnknownRole, parse_role
from shift.domain.pin_policy import (
    LOCKOUT_MINUTES,
    MAX_FAILED_ATTEMPTS,
    normalise_digits,
    validate_new_pin,
)
from shift.domain.shift_clock import covering
from shift.services.audit import AuditOutcome, AuditService, record_action
from shift.services.on_shift import on_shift_roles

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


class StaffInputError(AuthError):
    """A staff change the manager asked for is not acceptable; `message_fa` is what the screen shows."""

    def __init__(self, message_fa: str, *, conflict: bool = False) -> None:
        super().__init__(message_fa)
        self.message_fa = message_fa
        self.conflict = conflict


class OtpRequired(AuthError):
    """The PIN was right and this account has the two-step login on: the six-digit code comes next."""


class TwoStepRequired(AuthError):
    """A manager signing in from outside the clinic without the two-step login turned on (ADR-027)."""


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
    # ADR-021: the bound roles the person is on shift for right now; everything else they hold is
    # waiting for a handover. The permission dependency reads the effective set already.
    on_shift_roles: tuple[str, ...] = ()


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
    # The roles from the next shift on (R-09): what the manager's latest change will make true.
    next_roles: tuple[str, ...] = ()


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
        audit: AuditService | None = None,
        two_step: Any = None,
    ) -> None:
        self._database = database
        # ADR-027: the TOTP check after the PIN; None in scripts and unit tests that never use it.
        self._two_step = two_step
        self._clock = clock or SystemClock()
        self._hasher = hasher
        self._session_ttl = session_ttl
        # The per-install secret from `Settings.secret_file`: a stolen database alone is
        # not enough to run an offline PIN search (S02 §4 asks for salt *and* pepper).
        self._pepper = pepper
        # The lifespan injects the durable audit trail; without it (a unit test, a script)
        # every audited action still reaches the audit sink and nothing else changes.
        self._audit = audit
        # Built on the first "unknown mobile" login, not at startup: verifying against a
        # real digest keeps that path as slow as a wrong PIN, so response time does not
        # reveal which mobiles exist.
        self._decoy_hash: str | None = None

    def login(
        self, *, mobile: str, pin: str, device_id: str, otp: str | None = None, remote: bool = False
    ) -> IssuedSession:
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
            self._note(
                "auth.login",
                "auth.login result=unknown_mobile device=%s",
                (device_id,),
                actor="unknown",
                outcome=AuditOutcome.DENIED,
                subject=device_id,
                detail={"result": "unknown_mobile", "device": device_id},
            )
            raise InvalidCredentials(_WRONG_MOBILE_OR_PIN)
        if not staff.active:
            self._note(
                "auth.login",
                "auth.login result=inactive staff=%s",
                (staff.staff_id,),
                actor=staff.staff_id,
                outcome=AuditOutcome.DENIED,
                subject=staff.staff_id,
                detail={"result": "inactive"},
            )
            raise StaffInactive("staff account is deactivated")
        if device.staff_id != staff.staff_id:
            self._note(
                "auth.login",
                "auth.login result=device_not_owned staff=%s device=%s",
                (staff.staff_id, device_id),
                actor=staff.staff_id,
                outcome=AuditOutcome.DENIED,
                subject=staff.staff_id,
                detail={"result": "device_not_owned", "device": device_id},
            )
            raise InvalidCredentials(_WRONG_MOBILE_OR_PIN)
        self._require_unlocked(staff, now)
        if matches:
            matches = self._second_step(staff, otp=otp, remote=remote, now=now)
        outcome = self._record_attempt(staff=staff, device=device, matches=matches, now=now)
        # Outside the transaction on purpose: the audit write takes the single writer again.
        self._note_attempt(outcome, staff=staff, device=device)
        if isinstance(outcome, AuthError):
            raise outcome
        return outcome

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
        """Audit one action: a durable row plus the audit sink, never the public log (S02 §3.2)."""
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

    def _note_attempt(self, outcome: AuthError | IssuedSession, *, staff: StaffRecord, device: DeviceRecord) -> None:
        """Audit what the committed attempt did: opened a session, locked the row or refused it."""
        attempts = staff.failed_attempts + 1
        if isinstance(outcome, IssuedSession):
            self._note(
                "auth.login",
                "auth.login result=ok staff=%s device=%s",
                (staff.staff_id, device.device_id),
                actor=staff.staff_id,
                subject=staff.staff_id,
                detail={"result": "ok", "device": device.device_id},
            )
        elif isinstance(outcome, AccountLocked):
            self._note(
                "auth.lock",
                "auth.lock staff=%s until=%s",
                (staff.staff_id, utc_iso(outcome.until)),
                actor=staff.staff_id,
                outcome=AuditOutcome.DENIED,
                subject=staff.staff_id,
                detail={"result": "locked", "until": utc_iso(outcome.until)},
            )
        elif isinstance(outcome, InvalidCredentials):
            self._note(
                "auth.login",
                "auth.login result=bad_pin staff=%s attempts=%d",
                (staff.staff_id, attempts),
                actor=staff.staff_id,
                outcome=AuditOutcome.DENIED,
                subject=staff.staff_id,
                detail={"result": "bad_pin", "attempts": attempts},
            )
        else:  # the row was deactivated between `_identify` and the writer
            self._note(
                "auth.login",
                "auth.login result=inactive staff=%s",
                (staff.staff_id,),
                actor=staff.staff_id,
                outcome=AuditOutcome.DENIED,
                subject=staff.staff_id,
                detail={"result": "inactive"},
            )

    def _identify(self, *, device_id: str, mobile: str) -> tuple[DeviceRecord, StaffRecord | None]:
        """Read the phone and the staff row in one read transaction, never the writer."""
        with self._database.read() as connection:
            device = self._require_usable_device(devices_repo.find_device(connection, device_id))
            return device, staff_repo.find_by_mobile(connection, mobile)

    def _second_step(self, staff: StaffRecord, *, otp: str | None, remote: bool, now: datetime) -> bool:
        """ADR-027: after a right PIN, the code of an account that has the two-step login on.

        A wrong code counts as a wrong PIN (the lock covers both). A manager reaching the app from
        outside the clinic must have it on; inside the clinic it stays the person's own choice.
        """
        if self._two_step is None:
            return True
        if not self._two_step.enabled(staff.staff_id):
            if remote and Role.MANAGER.value in self._roles_of(staff.staff_id, now):
                raise TwoStepRequired("a manager signs in from outside only with the two-step login")
            return True
        if not otp:
            raise OtpRequired("the six-digit code is needed")
        return bool(self._two_step.check(staff.staff_id, otp, now))

    def _roles_of(self, staff_id: str, now: datetime) -> Sequence[str]:
        with self._database.read() as connection:
            return staff_repo.roles_for(connection, staff_id, covering(to_tehran(now)).text)

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
            return self._principal(connection, staff, device, session, now)

    def logout(self, *, token: str, reason: str = "logout") -> None:
        """Revoke the presented session; logging out twice is not an error."""
        now = utc_iso(self._clock.now_utc())
        with self._database.write() as connection:
            sessions_repo.revoke(connection, token_hash=sha256_hex(token), revoked_at=now, reason=reason)
        self._note(
            "auth.logout",
            "auth.logout reason=%s",
            (reason,),
            actor="session",
            detail={"reason": reason},
        )

    def revoke_staff_sessions(self, *, staff_ids: Sequence[str], reason: str) -> int:
        """Kill every live session of these people; the shift-close hook calls this."""
        now = utc_iso(self._clock.now_utc())
        revoked = 0
        with self._database.write() as connection:
            for staff_id in staff_ids:
                revoked += sessions_repo.revoke_for_staff(connection, staff_id=staff_id, revoked_at=now, reason=reason)
        self._note(
            "auth.revoke_sessions",
            "auth.revoke_sessions reason=%s staff=%s count=%d",
            (reason, list(staff_ids), revoked),
            actor="shift-close",
            detail={"reason": reason, "staff": list(staff_ids), "count": revoked},
        )
        return revoked

    def directory(self) -> Sequence[StaffSummary]:
        """Every staff member with roles (now and from the next shift) and lock state."""
        shift = covering(to_tehran(self._clock.now_utc()))
        with self._database.read() as connection:
            upcoming = {
                entry.staff.staff_id: entry.roles
                for entry in staff_repo.list_with_roles(connection, shift.following().text)
            }
            return [
                StaffSummary(
                    staff_id=entry.staff.staff_id,
                    full_name=entry.staff.full_name,
                    mobile=entry.staff.mobile,
                    roles=entry.roles,
                    active=entry.staff.active,
                    locked_until=entry.staff.locked_until,
                    next_roles=upcoming.get(entry.staff.staff_id, entry.roles),
                )
                for entry in staff_repo.list_with_roles(connection, shift.text)
            ]

    def create_staff(
        self, *, full_name: str, mobile: str, pin: str, roles: Sequence[str], actor_id: str
    ) -> StaffSummary:
        """Add one person who can sign in at once: name, mobile, a policy-compliant PIN and their roles.

        The starting roles live in `staff_role` and apply immediately, because there is no earlier
        shift to wait out; every later change goes through the catalog and starts at the next shift
        (ADR-022). The PIN is hashed before the writer is taken, like every Argon2id run here.
        """
        name = " ".join(full_name.split())
        if not 1 <= len(name) <= 80:
            raise StaffInputError("نام باید بین ۱ تا ۸۰ نویسه باشد.")
        try:
            normalised = normalise_mobile(mobile)
        except InvalidCredentials as exc:
            raise StaffInputError("موبایل باید شمارهٔ ۰۹xxxxxxxxx باشد.") from exc
        try:
            parsed = tuple(dict.fromkeys(parse_role(value) for value in roles))
        except UnknownRole as exc:
            raise StaffInputError("نقش انتخاب‌شده شناخته‌شده نیست.") from exc
        if not parsed:
            raise StaffInputError("دست‌کم یک نقش لازم است.")
        digest = hash_pin(validate_new_pin(pin), pepper=self._pepper, hasher=self._hasher)
        stamp = utc_iso(self._clock.now_utc())
        staff_id = f"staff-{new_ulid().lower()[-10:]}"
        try:
            with self._database.write() as connection:
                staff_repo.insert(
                    connection, staff_id=staff_id, full_name=name, mobile=normalised, pin_hash=digest, created_at=stamp
                )
                for role in parsed:
                    staff_repo.insert_role(connection, staff_id=staff_id, role=role.value, granted_at=stamp)
        except IntegrityError as exc:
            raise StaffInputError("این موبایل قبلاً برای یک کارمند ثبت شده است.", conflict=True) from exc
        self._note(
            "staff.create",
            "staff.create staff=%s actor=%s roles=%s",
            (staff_id, actor_id, [role.value for role in parsed]),
            actor=actor_id,
            subject=staff_id,
            detail={"roles": [role.value for role in parsed]},
        )
        role_names = tuple(sorted(role.value for role in parsed))
        return StaffSummary(staff_id, name, normalised, role_names, True, None, role_names)

    def set_active(self, *, staff_id: str, active: bool, actor_id: str) -> None:
        """Deactivate or reactivate one person; a deactivated person loses every session at once.

        A manager cannot lock themselves out, and the clinic never ends up with no active manager.
        """
        if not active and staff_id == actor_id:
            raise StaffInputError("نمی‌توانید حساب خودتان را غیرفعال کنید.")
        now = self._clock.now_utc()
        with self._database.write() as connection:
            if staff_repo.find(connection, staff_id) is None:
                raise StaffNotFound(staff_id)
            if not active:
                # The clinic needs a manager from the *next* shift on, not only for the shift that is
                # running when the button is pressed (ADR-022 §2).
                self._require_other_manager(connection, staff_id, covering(to_tehran(now)).following().text)
            staff_repo.set_active(connection, staff_id=staff_id, active=active)
            if not active:
                sessions_repo.revoke_for_staff(
                    connection, staff_id=staff_id, revoked_at=utc_iso(now), reason="deactivated"
                )
        self._note(
            "staff.activate" if active else "staff.deactivate",
            "staff.set_active staff=%s active=%s actor=%s",
            (staff_id, active, actor_id),
            actor=actor_id,
            subject=staff_id,
            detail={"active": active},
        )

    def require_manager_remains(self, *, staff_id: str, role: str) -> None:
        """Refuse a role change that would leave the clinic without an active manager from the next shift."""
        if role != Role.MANAGER.value:
            return
        following = covering(to_tehran(self._clock.now_utc())).following().text
        with self._database.read() as connection:
            self._require_other_manager(connection, staff_id, following)

    @staticmethod
    def _require_other_manager(connection: Connection, staff_id: str, shift_key: str) -> None:
        """Some other active person holds the manager role at `shift_key`, or the change is refused."""
        for entry in staff_repo.list_with_roles(connection, shift_key):
            if entry.staff.staff_id != staff_id and entry.staff.active and Role.MANAGER.value in entry.roles:
                return
        raise StaffInputError("دست‌کم یک مدیر فعال باید بماند.")

    def unlock(self, *, staff_id: str, actor_id: str) -> None:
        """Clear the failure counter and lock; the manager-only recovery path (S4)."""
        with self._database.write() as connection:
            staff = staff_repo.find(connection, staff_id)
            if staff is None:
                raise StaffNotFound(staff_id)
            staff_repo.clear_failures(connection, staff_id=staff_id)
        self._note(
            "auth.unlock",
            "auth.unlock staff=%s actor=%s",
            (staff_id, actor_id),
            actor=actor_id,
            subject=staff_id,
            detail={"rationale": "manager unlock"},
        )

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
        self._note(
            "auth.pin_reset",
            "auth.pin_reset staff=%s actor=%s",
            (staff_id, actor_id),
            actor=actor_id,
            subject=staff_id,
            detail={"sessions_revoked": True},
        )

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
            return AccountLocked(until)
        staff_repo.record_failure(connection, staff_id=staff.staff_id, failed_attempts=attempts, locked_until=None)
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
        principal = self._principal(connection, staff, device, session, now)
        return IssuedSession(token=token, csrf_token=csrf_token, principal=principal)

    def _principal(
        self, connection: Connection, staff: StaffRecord, device: DeviceRecord, session: SessionRecord, now: datetime
    ) -> Principal:
        # One source of truth (ADR-022): the roles the clinic clock's running shift sees.
        roles = staff_repo.roles_for(connection, staff.staff_id, covering(to_tehran(now)).text)
        try:
            # ADR-021: handing over is the key to access. The effective set — free roles always,
            # bound roles only on shift, plus the always-allowed few — is what every guard reads.
            working = on_shift_roles(connection, staff_id=staff.staff_id, roles=roles, instant=now)
            permissions = effective_permissions(roles, working)
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
            on_shift_roles=working,
        )
