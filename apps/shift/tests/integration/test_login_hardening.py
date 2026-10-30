"""Login hardening: no writer lock while hashing, and identical Argon2id work on every path.

Both properties are read from the hasher and from the transaction boundary (call counts), never
from a stopwatch, so they stay deterministic on a busy machine.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from argon2 import PasswordHasher
from auth_fixtures import (
    ALTERNATE_PIN,
    FIXED_NOW,
    PIN,
    ROLES,
    SPARE,
    TEST_HASHER,
    FixedClock,
    create_device,
    create_staff,
)
from sqlalchemy import Connection

from shift.adapters.db import staff_repo
from shift.adapters.db.core import Database
from shift.common.clock import utc_iso
from shift.services.auth import (
    AccountLocked,
    AuthService,
    InvalidCredentials,
    StaffInactive,
    hash_pin,
)

RECEPTION = ROLES["reception"]
NURSE = ROLES["nurse"]
STOREKEEPER = ROLES["storekeeper"]


class CountingHasher:
    """A `PasswordHasher` stand-in that counts the work the service asked for."""

    def __init__(self, inner: PasswordHasher = TEST_HASHER) -> None:
        self._inner = inner
        self.hashes = 0
        self.verifications = 0

    def hash(self, password: str, *args: Any, **kwargs: Any) -> str:
        self.hashes += 1
        return self._inner.hash(password, *args, **kwargs)

    def verify(self, digest: str, password: str, *args: Any, **kwargs: Any) -> bool:
        self.verifications += 1
        return self._inner.verify(digest, password, *args, **kwargs)


class WriteSpy:
    """Replaces `Database.write` to see whether the service held the writer while hashing."""

    def __init__(self, database: Database) -> None:
        self._original = database.write
        self.opened = 0
        self.open_now = 0
        self.seen: list[int] = []
        database.write = self._track  # type: ignore[method-assign]

    @contextmanager
    def _track(self, *, commit: bool = True) -> Iterator[Connection]:
        self.opened += 1
        self.open_now += 1
        try:
            with self._original(commit=commit) as connection:
                yield connection
        finally:
            self.open_now -= 1


class WatchingHasher(CountingHasher):
    """Counts the hasher calls and records how many write transactions were open meanwhile."""

    def __init__(self, spy: WriteSpy) -> None:
        super().__init__()
        self._spy = spy

    def hash(self, password: str, *args: Any, **kwargs: Any) -> str:
        self._spy.seen.append(self._spy.open_now)
        return super().hash(password, *args, **kwargs)

    def verify(self, digest: str, password: str, *args: Any, **kwargs: Any) -> bool:
        self._spy.seen.append(self._spy.open_now)
        return super().verify(digest, password, *args, **kwargs)


@dataclass(frozen=True, slots=True)
class Attempt:
    """One login attempt: the refusal it must produce (`None` means a session opens)."""

    name: str
    mobile: str
    device_id: str
    pin: str
    refusal: type[Exception] | None = None
    decoy_hashes: int = 0


ATTEMPTS = (
    Attempt(name="accepted", mobile=RECEPTION.mobile, device_id=RECEPTION.device_id, pin=PIN),
    Attempt(
        name="wrong pin",
        mobile=RECEPTION.mobile,
        device_id=RECEPTION.device_id,
        pin=ALTERNATE_PIN,
        refusal=InvalidCredentials,
    ),
    Attempt(
        name="unknown mobile",
        mobile="09129999999",
        device_id=RECEPTION.device_id,
        pin=PIN,
        refusal=InvalidCredentials,
        decoy_hashes=1,
    ),
    Attempt(
        name="borrowed device",
        mobile=RECEPTION.mobile,
        device_id=NURSE.device_id,
        pin=PIN,
        refusal=InvalidCredentials,
    ),
    Attempt(name="inactive staff", mobile=SPARE.mobile, device_id=SPARE.device_id, pin=PIN, refusal=StaffInactive),
    Attempt(
        name="locked staff", mobile=STOREKEEPER.mobile, device_id=STOREKEEPER.device_id, pin=PIN, refusal=AccountLocked
    ),
)


def _seed_roster(database: Database) -> None:
    """Every role plus the spare, with the spare deactivated and the storekeeper locked."""
    for person in (*ROLES.values(), SPARE):
        create_staff(
            database,
            staff_id=person.staff_id,
            full_name=person.full_name,
            mobile=person.mobile,
            roles=person.roles,
        )
        create_device(database, device_id=person.device_id, staff_id=person.staff_id)
    with database.write() as connection:
        staff_repo.set_active(connection, staff_id=SPARE.staff_id, active=False)
        staff_repo.record_failure(
            connection,
            staff_id=STOREKEEPER.staff_id,
            failed_attempts=0,
            locked_until=utc_iso(FIXED_NOW + timedelta(minutes=15)),
        )


@pytest.mark.parametrize("attempt", ATTEMPTS, ids=lambda attempt: attempt.name)
def test_every_login_outcome_costs_exactly_one_argon2_verification(database: Database, attempt: Attempt) -> None:
    _seed_roster(database)
    hasher = CountingHasher()
    service = AuthService(database, clock=FixedClock(), hasher=hasher)

    if attempt.refusal is None:
        assert service.login(mobile=attempt.mobile, pin=attempt.pin, device_id=attempt.device_id).token
    else:
        with pytest.raises(attempt.refusal):
            service.login(mobile=attempt.mobile, pin=attempt.pin, device_id=attempt.device_id)

    assert hasher.verifications == 1, f"{attempt.name} must cost one Argon2id check, like a success"
    assert hasher.hashes == attempt.decoy_hashes


def test_unknown_mobiles_share_one_decoy_digest_and_one_verification_each(database: Database) -> None:
    _seed_roster(database)
    hasher = CountingHasher()
    service = AuthService(database, clock=FixedClock(), hasher=hasher)

    for mobile in ("09129999991", "09129999992", "09129999993"):
        with pytest.raises(InvalidCredentials):
            service.login(mobile=mobile, pin=PIN, device_id=RECEPTION.device_id)

    assert hasher.hashes == 1, "the decoy digest is built once and reused"
    assert hasher.verifications == 3


def test_no_write_transaction_is_open_while_a_pin_is_hashed(database: Database) -> None:
    _seed_roster(database)
    spy = WriteSpy(database)
    hasher = WatchingHasher(spy)
    service = AuthService(database, clock=FixedClock(), hasher=hasher)

    with pytest.raises(InvalidCredentials):
        service.login(mobile=RECEPTION.mobile, pin=ALTERNATE_PIN, device_id=RECEPTION.device_id)
    assert service.login(mobile=RECEPTION.mobile, pin=PIN, device_id=RECEPTION.device_id).token
    with pytest.raises(InvalidCredentials):
        service.login(mobile="09129999997", pin=PIN, device_id=RECEPTION.device_id)
    service.reset_pin(staff_id=NURSE.staff_id, pin=ALTERNATE_PIN, actor_id="staff-manager")

    assert hasher.verifications == 3
    assert hasher.hashes == 2, "the decoy digest and the reset digest"
    assert spy.seen == [0, 0, 0, 0, 0], "the writer must be free for every hash and verification"
    assert spy.opened == 3, "one write for the failure, one for the session, one for the reset"
    assert spy.open_now == 0


def test_a_locked_account_is_refused_without_taking_the_writer(database: Database) -> None:
    _seed_roster(database)
    spy = WriteSpy(database)
    hasher = WatchingHasher(spy)
    service = AuthService(database, clock=FixedClock(), hasher=hasher)

    with pytest.raises(AccountLocked):
        service.login(mobile=STOREKEEPER.mobile, pin=PIN, device_id=STOREKEEPER.device_id)

    assert spy.opened == 0
    assert hasher.verifications == 1, "a locked account still pays one Argon2id check"


def test_a_failure_counted_while_the_pin_is_hashed_is_not_lost(database: Database) -> None:
    create_staff(database, staff_id="staff-race", full_name="هم‌زمان", mobile="09121111112", roles=("nurse",))
    create_device(database, device_id="device-race", staff_id="staff-race")

    class RacingHasher(CountingHasher):
        def verify(self, digest: str, password: str, *args: Any, **kwargs: Any) -> bool:
            # A second request records its own failure while this login is hashing.
            with database.write() as connection:
                staff_repo.record_failure(connection, staff_id="staff-race", failed_attempts=1, locked_until=None)
            return super().verify(digest, password, *args, **kwargs)

    service = AuthService(database, clock=FixedClock(), hasher=RacingHasher())

    with pytest.raises(InvalidCredentials):
        service.login(mobile="09121111112", pin=ALTERNATE_PIN, device_id="device-race")

    with database.read() as connection:
        assert staff_repo.find(connection, "staff-race").failed_attempts == 2, "the counter must add up"


def test_a_login_whose_pin_is_reset_while_it_hashes_opens_no_session(database: Database) -> None:
    create_staff(database, staff_id="staff-reset", full_name="بازنشانی", mobile="09121111113", roles=("nurse",))
    create_device(database, device_id="device-reset", staff_id="staff-reset")
    reset_done = False

    class ResettingHasher(CountingHasher):
        """A manager reset lands between this login's read and its write, once."""

        def verify(self, digest: str, password: str, *args: Any, **kwargs: Any) -> bool:
            nonlocal reset_done
            matched = super().verify(digest, password, *args, **kwargs)
            if not reset_done:
                reset_done = True
                with database.write() as connection:
                    staff_repo.set_pin_hash(
                        connection,
                        staff_id="staff-reset",
                        pin_hash=hash_pin(ALTERNATE_PIN, hasher=TEST_HASHER),
                        updated_at=utc_iso(FIXED_NOW),
                    )
            return matched

    service = AuthService(database, clock=FixedClock(), hasher=ResettingHasher())

    with pytest.raises(InvalidCredentials):
        service.login(mobile="09121111113", pin=PIN, device_id="device-reset")

    with database.read() as connection:
        sessions = connection.exec_driver_sql("SELECT COUNT(*) FROM sessions").scalar_one()
    assert sessions == 0, "a digest that is no longer stored proves nothing"
    assert service.login(mobile="09121111113", pin=ALTERNATE_PIN, device_id="device-reset").token


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        pytest.param(
            lambda connection: staff_repo.set_active(connection, staff_id="staff-late", active=False),
            StaffInactive,
            id="deactivated",
        ),
        pytest.param(
            lambda connection: staff_repo.record_failure(
                connection,
                staff_id="staff-late",
                failed_attempts=0,
                locked_until=utc_iso(FIXED_NOW + timedelta(minutes=15)),
            ),
            AccountLocked,
            id="locked",
        ),
    ],
)
def test_a_login_whose_row_changes_while_it_hashes_is_refused(
    database: Database, change: Callable[[Connection], None], refusal: type[Exception]
) -> None:
    create_staff(database, staff_id="staff-late", full_name="دیرشده", mobile="09121111114", roles=("nurse",))
    create_device(database, device_id="device-late", staff_id="staff-late")

    class ChangingHasher(CountingHasher):
        """Another request changes the row between this login's read and its write."""

        def verify(self, digest: str, password: str, *args: Any, **kwargs: Any) -> bool:
            matched = super().verify(digest, password, *args, **kwargs)
            with database.write() as connection:
                change(connection)
            return matched

    service = AuthService(database, clock=FixedClock(), hasher=ChangingHasher())

    with pytest.raises(refusal):
        service.login(mobile="09121111114", pin=PIN, device_id="device-late")

    with database.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM sessions").scalar_one() == 0
        staff = staff_repo.find(connection, "staff-late")
    assert staff.failed_attempts == 0, "a request refused by the new state must not count a failure"
