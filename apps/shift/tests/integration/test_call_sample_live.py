"""The manager's «نمونهٔ تماس» reads name and mobile live, and the mirror learns who is first (decision 5).

Two things here can hurt the clinic and are pinned end to end. The card and the shift database must
never hold a patient's name or mobile: the stored card keeps patient ids, and the page reads
`v_shift_call_sample_v1` through the read-only accounting connection at the moment it is rendered, for
the manager alone, with `no-store`. And the mirror must take accounting's `first_for_phone` when the
accounting copy has migration 8, counting every registration as the first when it does not, so an
install that has not been upgraded pays the reward it always paid (S01 §5, S04).

The accounting copy is the synthetic one built by `tools/make_synthetic_acc_db.py`; every patient here
is invented (ADR-006).
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from clocks import FixedClock
from fastapi.testclient import TestClient
from make_synthetic_acc_db import SyntheticAccounting, build

from shift.adapters.accounting import views
from shift.adapters.accounting.readonly import open_accounting
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.config.settings import Settings
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore

CARD_DAY = date(2026, 10, 27)  # Tuesday's card covers Monday
REGISTERED_AT = "2026-10-26 10:00:00"
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=TEHRAN)
FIRST_NAME = "بیمار مصنوعی یکم"
SECOND_NAME = "بیمار مصنوعی دوم"
# Mobiles of this test alone: the synthetic scenario has its own patient on `09123456789`.
FIRST_MOBILE = "09129990001"
SECOND_MOBILE = "09129990002"
VALID_NATIONAL_ID = "4001234505"  # invented, check digit correct
OTHER_NATIONAL_ID = "4001234602"
THIRD_NATIONAL_ID = "5123456708"


def _patient(connection: sqlite3.Connection, *, name: str, national_id: str, mobile: str) -> None:
    """One invented patient in the synthetic accounting copy, registered on the card's covered day."""
    given, family = name.split(" ", 1)
    connection.execute(
        "INSERT INTO patients (name, family_name, national_id, phone_number, is_foreign, created_by, created_at) "
        "VALUES (?, ?, ?, ?, 0, 'reception1', ?)",
        (given, family, national_id, mobile, REGISTERED_AT),
    )


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting copy with two patients sharing one mobile and one with its own."""
    accounting = build(tmp_path / "accounting.db")
    connection = sqlite3.connect(accounting.path)
    try:
        _patient(connection, name=FIRST_NAME, national_id=VALID_NATIONAL_ID, mobile=FIRST_MOBILE)
        _patient(connection, name=SECOND_NAME, national_id=OTHER_NATIONAL_ID, mobile=FIRST_MOBILE)
        _patient(connection, name="بیمار مصنوعی سوم", national_id=THIRD_NATIONAL_ID, mobile=SECOND_MOBILE)
        connection.commit()
    finally:
        connection.close()
    return accounting


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture()
def bridge(settings: Settings, scenario: SyntheticAccounting, clock: FixedClock) -> AccountingBridge:
    """The real bridge, polling the synthetic copy into a real migrated shift database."""
    database = Database(settings.database_path)
    database.migrate()
    try:
        yield AccountingBridge(database, EventStore(database, clock=clock), clock=clock)
    finally:
        database.close()


def _drawn_ids(scenario: SyntheticAccounting) -> tuple[int, ...]:
    """The ids of the three invented patients, in insertion order: the two sharing a mobile, then one."""
    connection = sqlite3.connect(scenario.path)
    try:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT id FROM patients WHERE national_id IN (?, ?, ?) ORDER BY id",
                (VALID_NATIONAL_ID, OTHER_NATIONAL_ID, THIRD_NATIONAL_ID),
            )
        )
    finally:
        connection.close()


def test_the_mirror_takes_the_duplicate_mobile_flag_from_v2(scenario: SyntheticAccounting) -> None:
    """The two patients on one mobile arrive as one first and one duplicate."""
    database = Database(Path(scenario.path).with_name("shift-mirror.db"))
    database.migrate()
    try:
        bridge = AccountingBridge(database, EventStore(database, clock=FixedClock(NOW)), clock=FixedClock(NOW))
        report = bridge.poll(scenario.path)
        assert report.error is None
        with database.read() as connection:
            flags = [
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT first_for_phone FROM acc_registration WHERE patient_id IN (?, ?, ?) ORDER BY patient_id",
                    _drawn_ids(scenario),
                )
            ]
    finally:
        database.close()
    assert flags == [1, 0, 1], "one mobile pays once; the third patient is alone"


def test_an_accounting_copy_below_migration_8_counts_every_registration_as_first(
    scenario: SyntheticAccounting,
) -> None:
    """Without v2 the bridge reads v1, and every row is the first for its mobile (S01 §5)."""
    connection = sqlite3.connect(scenario.path)
    try:
        connection.execute("DROP VIEW v_shift_patient_registrations_v2")
        connection.execute("DROP VIEW v_shift_call_sample_v1")
        connection.commit()
    finally:
        connection.close()

    database = Database(Path(scenario.path).with_name("shift-v1.db"))
    database.migrate()
    try:
        bridge = AccountingBridge(database, EventStore(database, clock=FixedClock(NOW)), clock=FixedClock(NOW))
        assert bridge.poll(scenario.path).error is None
        with database.read() as connection:
            flags = [row[0] for row in connection.exec_driver_sql("SELECT first_for_phone FROM acc_registration")]
            events = connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar_one()
    finally:
        database.close()
    assert flags and set(flags) == {1}
    assert events > 0, "the v1 fallback must still ingest, not crash on the missing view"


@pytest.fixture()
def manager_client(settings: Settings, scenario: SyntheticAccounting, clock: FixedClock) -> Iterator[TestClient]:
    """One app on the synthetic accounting copy, with a Tuesday card and a signed-in manager."""
    with TestClient(create_app(replace(settings, accounting_db_path=scenario.path))) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        AccountingBridge(
            client.app.state.database,
            client.app.state.events,
            clock=client.app.state.clock,
        ).poll(scenario.path)
        assert client.app.state.daily_card.regenerate(CARD_DAY) is not None
        yield client


def _signed_in(client: TestClient, role: str = "manager") -> TestClient:
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return client


def _stored_card_text(database: Database) -> str:
    with database.read() as connection:
        tables = [
            row[0]
            for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")
            if not row[0].startswith("sqlite_")
        ]
        return "\n".join(str(connection.exec_driver_sql(f"SELECT * FROM {table}").all()) for table in tables)


def test_the_card_shows_the_live_name_and_mobile_and_stores_neither(
    manager_client: TestClient, scenario: SyntheticAccounting
) -> None:
    """The page carries the drawn patients' names and `tel:` links; the database and the card carry neither."""
    client = _signed_in(manager_client)
    card = client.app.state.daily_card.get(CARD_DAY)
    assert card is not None and card.call_sample, "the fixture must draw a sample for the card"
    drawn = [int(entry.patient_id) for entry in card.call_sample]
    with open_accounting(scenario.path) as accounting, accounting.connect() as connection:
        contacts = views.call_sample_contacts(connection, drawn)

    page = client.get(f"/manager/card/{CARD_DAY.isoformat()}")

    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    text = page.text
    assert "نمونهٔ تماس" in text
    for patient_id in drawn:
        assert contacts[patient_id]["full_name"] in text, "the drawn patient is named from accounting"
        assert f'href="tel:{contacts[patient_id]["phone_number"]}"' in text, "the mobile is one tap away"
    stored = _stored_card_text(client.app.state.database)
    for patient_id in drawn:
        assert str(contacts[patient_id]["phone_number"]) not in stored, "no mobile in the shift database"
        assert str(contacts[patient_id]["full_name"]) not in stored, "no patient name in the shift database"


def test_a_non_manager_gets_no_call_sample(manager_client: TestClient) -> None:
    """The card is the manager's page: another role receives a 403 that names no patient."""
    client = _signed_in(manager_client, role="nurse")

    page = client.get(f"/manager/card/{CARD_DAY.isoformat()}")

    assert page.status_code == 403
    assert FIRST_MOBILE not in page.text and FIRST_NAME not in page.text
