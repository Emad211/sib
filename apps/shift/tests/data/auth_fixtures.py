"""Synthetic staff, phones and sessions for the auth tests; every value is made up."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from shift.adapters.db import devices_repo, staff_repo
from shift.adapters.db.core import Database
from shift.common.clock import utc_iso
from shift.services.auth import hash_pin

PIN = "482916"
ALTERNATE_PIN = "739251"
FIXED_NOW = datetime(2026, 10, 28, 6, 30, tzinfo=UTC)
TEST_HASHER = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
PUBKEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE-test-key"


class FixedClock:
    """A clock the test moves by hand; production uses `SystemClock`."""

    def __init__(self, now: datetime = FIXED_NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass(frozen=True, slots=True)
class Seeded:
    """A staff member, the phone they log in from, and their roles."""

    staff_id: str
    full_name: str
    mobile: str
    device_id: str
    roles: tuple[str, ...]


ROLES: dict[str, Seeded] = {
    "reception": Seeded("staff-reception", "پذیرش یک", "09120000001", "device-reception", ("reception",)),
    "nurse": Seeded("staff-nurse", "پرستار یک", "09120000002", "device-nurse", ("nurse",)),
    "storekeeper": Seeded("staff-store", "انباردار یک", "09120000003", "device-store", ("storekeeper",)),
    "purchaser": Seeded("staff-purchaser", "خریدار یک", "09120000004", "device-purchaser", ("purchaser",)),
    "accountant": Seeded("staff-accountant", "حسابدار یک", "09120000005", "device-accountant", ("accountant",)),
    "shift_doctor": Seeded("staff-doctor", "پزشک شیفت", "09120000006", "device-doctor", ("shift_doctor",)),
    "manager": Seeded("staff-manager", "مدیر درمانگاه", "09120000007", "device-manager", ("manager",)),
    "multi_role": Seeded("staff-multi", "پذیرش و حساب", "09120000008", "device-multi", ("reception", "accountant")),
}
SPARE = Seeded("staff-spare", "کارمند ذخیره", "09120000009", "device-spare", ("nurse",))


def create_staff(
    database: Database,
    *,
    staff_id: str,
    full_name: str,
    mobile: str,
    roles: Iterable[str],
    pin: str = PIN,
    active: bool = True,
    pepper: str = "",
) -> None:
    """Insert one staff row with roles and a cheap (test-only) argon2id digest."""
    with database.write() as connection:
        staff_repo.insert(
            connection,
            staff_id=staff_id,
            full_name=full_name,
            mobile=mobile,
            pin_hash=hash_pin(pin, pepper=pepper, hasher=TEST_HASHER),
            created_at=utc_iso(FIXED_NOW),
        )
        for role in roles:
            staff_repo.insert_role(connection, staff_id=staff_id, role=role, granted_at=utc_iso(FIXED_NOW))
        if not active:
            staff_repo.set_active(connection, staff_id=staff_id, active=False)


def create_device(database: Database, *, device_id: str, staff_id: str, label: str = "گوشی آزمایشی") -> None:
    """Pair one device with a staff member without going through a QR."""
    with database.write() as connection:
        devices_repo.insert_device(
            connection,
            device_id=device_id,
            staff_id=staff_id,
            label=label,
            pubkey=PUBKEY,
            enrolled_at=utc_iso(FIXED_NOW),
        )


def seed_roster(database: Database, *, pepper: str = "") -> None:
    """Every role from the route matrix plus one spare target for the manager routes."""
    for person in (*ROLES.values(), SPARE):
        create_staff(
            database,
            staff_id=person.staff_id,
            full_name=person.full_name,
            mobile=person.mobile,
            roles=person.roles,
            pepper=pepper,
        )
        create_device(database, device_id=person.device_id, staff_id=person.staff_id)


def login(client: TestClient, *, mobile: str, device_id: str, pin: str = PIN):
    """Walk the real login handshake: visit `/login` for the CSRF cookie, then POST."""
    client.get("/login")
    csrf = client.cookies.get("shift_csrf", "")
    return client.post(
        "/api/v1/auth/login",
        json={"mobile": mobile, "pin": pin, "device_id": device_id},
        headers={"X-CSRF-Token": csrf},
    )
