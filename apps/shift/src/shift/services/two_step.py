"""The two-step login (ADR-027): a TOTP code from the person's authenticator app after the PIN.

Anyone may turn it on for their own account; the head and the manager must have it on before they can
sign in from outside the clinic (through the relay's public name). The secret is sealed at rest with the
install's key, shown once as a QR while it is being set up, and confirmed with a first code before it
counts. A code is accepted once: its time step is remembered, so a code seen over a shoulder is dead.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection

from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, utc_iso
from shift.common.seal import SealBroken, seal, unseal
from shift.domain import totp

SECRET_BYTES = 20
WRONG_CODE = "کد شش‌رقمی درست نیست؛ کد تازهٔ برنامه را بزنید."
NOT_STARTED = "ابتدا «روشن کردن ورود دومرحله‌ای» را بزنید."


class TwoStepError(ValueError):
    """A setup step was refused; the message is Persian and safe to show."""


@dataclass(frozen=True, slots=True)
class TwoStepRow:
    staff_id: str
    secret: str
    enabled_at: str | None
    last_step: int


@dataclass(frozen=True, slots=True)
class Setup:
    secret: str
    uri: str


def _find(connection: Connection, staff_id: str) -> TwoStepRow | None:
    row = connection.exec_driver_sql(
        "SELECT staff_id, secret, enabled_at, last_step FROM staff_totp WHERE staff_id = ?", (staff_id,)
    ).first()
    return None if row is None else TwoStepRow(str(row[0]), str(row[1]), row[2], int(row[3]))


class TwoStepService:
    """Setup, confirmation and the per-login check; the only reader and writer of `staff_totp`."""

    def __init__(self, database: Database, *, key: str, clock: Clock | None = None) -> None:
        self._database = database
        self._key = key
        self._clock = clock or SystemClock()

    def enabled(self, staff_id: str) -> bool:
        with self._database.read() as connection:
            row = _find(connection, staff_id)
        return row is not None and row.enabled_at is not None

    def begin(self, staff_id: str, account: str) -> Setup:
        """A new secret for this person, waiting for its first code; any earlier secret is replaced."""
        secret = totp.base32_secret(secrets.token_bytes(SECRET_BYTES))
        now = utc_iso(self._clock.now_utc())
        with self._database.write() as connection:
            connection.exec_driver_sql(
                "INSERT INTO staff_totp (staff_id, secret, created_at, enabled_at, last_step) VALUES (?, ?, ?, NULL, 0) "
                "ON CONFLICT(staff_id) DO UPDATE SET secret = excluded.secret, created_at = excluded.created_at, "
                "enabled_at = NULL, last_step = 0",
                (staff_id, seal(self._key, secret), now),
            )
        return Setup(secret=secret, uri=totp.provisioning_uri(secret, account))

    def confirm(self, staff_id: str, code: str) -> None:
        """The first right code switches the two-step login on."""
        with self._database.write() as connection:
            row = _find(connection, staff_id)
            if row is None:
                raise TwoStepError(NOT_STARTED)
            step = self._step(row, code, self._clock.now_utc())
            if step is None:
                raise TwoStepError(WRONG_CODE)
            connection.exec_driver_sql(
                "UPDATE staff_totp SET enabled_at = ?, last_step = ? WHERE staff_id = ?",
                (utc_iso(self._clock.now_utc()), step, staff_id),
            )

    def disable(self, staff_id: str, code: str) -> None:
        """Turning it off needs a current code, so a borrowed session cannot quietly remove it."""
        with self._database.write() as connection:
            row = _find(connection, staff_id)
            if row is None or row.enabled_at is None:
                return
            if self._step(row, code, self._clock.now_utc()) is None:
                raise TwoStepError(WRONG_CODE)
            connection.exec_driver_sql("DELETE FROM staff_totp WHERE staff_id = ?", (staff_id,))

    def check(self, staff_id: str, code: str, now: datetime) -> bool:
        """One login's code: right and not used before; the step is remembered when it is right."""
        with self._database.write() as connection:
            row = _find(connection, staff_id)
            if row is None or row.enabled_at is None:
                return False
            step = self._step(row, code, now)
            if step is None:
                return False
            connection.exec_driver_sql("UPDATE staff_totp SET last_step = ? WHERE staff_id = ?", (step, staff_id))
            return True

    def _step(self, row: TwoStepRow, code: str, now: datetime) -> int | None:
        try:
            secret = unseal(self._key, row.secret)
        except SealBroken:
            return None
        return totp.matching_step(secret, code, now.timestamp(), after_step=row.last_step)
