"""The care group of the staff phone: patient items, vitals and the cleaning checklist (SHF-3, S01 §3).

Three rules can hurt the clinic from here, and each is walked through the real `ShiftEventService`:
a patient's belonging is recorded outside the shelf count and a delivery closes it once, a vital sign
is recorded with at least one reading and no threshold of ours, and a cleaning checklist may only
confirm items that sit at the chosen location. Every kind is recorded without a projection effect.

The catalog is the synthetic reference data from `tools/seed_reference_data.py`; every patient name,
item and reading below is invented and no production database is read (ADR-006).
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, seed_roster
from seed_reference_data import seed as seed_catalog

from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.permissions import permissions_for
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventRuleError, ShiftEventService

WORK_DATE = date(2026, 10, 28)
CLIENT_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SHIFT = ShiftKey(WORK_DATE, 2)

HELD = {"item_name": "عینک بیمار", "patient_name": "بیمار آزمایشی", "admission_no": "A-2001"}
READINGS = {"systolic": 120, "diastolic": 80, "sugar": 110, "admission_no": "A-2001"}
CLEANING = {"location_id": "shelf_reception", "items": ["para_500_amp", "diclofenac_75_amp"]}


@pytest.fixture
def seeded(database: Database) -> Database:
    seed_roster(database, pepper=b"synthetic-pepper")
    seed_catalog(database)
    return database


def _principal(role: str) -> Principal:
    person = ROLES[role]
    return Principal(
        staff_id=person.staff_id,
        full_name=person.full_name,
        roles=person.roles,
        permissions=permissions_for(person.roles),
        device_id=person.device_id,
        device_label="گوشی آزمایشی",
        session_hash="synthetic",
        csrf_token="synthetic-csrf",
        expires_at=datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
    )


def _service(database: Database) -> ShiftEventService:
    return ShiftEventService(
        database,
        EventStore(database),
        ProjectionEngine(database),
        CatalogService(database),
        SettingsService(database),
    )


def _submission(
    event_type: str,
    payload: dict,
    principal: Principal,
    *,
    ref_event_id: str | None = None,
) -> EventSubmission:
    event_id = new_ulid(CLIENT_AT)
    return EventSubmission(
        type=event_type,
        payload=payload,
        actor_id=principal.staff_id,
        device_id=principal.device_id,
        client_at=CLIENT_AT,
        client_event_id=event_id,
        shift_id=SHIFT.text,
        ref_event_id=ref_event_id,
        idempotency_key=event_id,
        shift_work_date=WORK_DATE,
    )


def _projections(database: Database) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The box and the shelves, the two projections a care event must never touch."""
    with database.read() as connection:
        return projections_repo.cash_rows(connection), projections_repo.stock_rows(connection)


def test_a_held_patient_item_is_recorded_and_moves_no_projection(seeded: Database) -> None:
    service = _service(seeded)
    nurse = _principal("nurse")
    before = _projections(seeded)

    result = service.record(_submission("patient.item_held", HELD, nurse), principal=nurse, shift=SHIFT)

    assert result.created is True
    assert _projections(seeded) == before, "a belonging of a patient is outside the shelf count"


def test_a_delivery_closes_its_held_item_once_and_never_another_kind(seeded: Database) -> None:
    service = _service(seeded)
    nurse = _principal("nurse")
    held = service.record(_submission("patient.item_held", HELD, nurse), principal=nurse, shift=SHIFT)

    # A delivery without the held item says nothing about what went back to the patient.
    with pytest.raises(ShiftEventRuleError):
        service.record(_submission("patient.item_delivered", {}, nurse), principal=nurse, shift=SHIFT)

    # A vitals record is not a held belonging, even though it is a shift event too.
    readings = service.record(_submission("vitals.recorded", READINGS, nurse), principal=nurse, shift=SHIFT)
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission("patient.item_delivered", {}, nurse, ref_event_id=readings.event_id),
            principal=nurse,
            shift=SHIFT,
        )

    delivered = service.record(
        _submission("patient.item_delivered", {"note": "به همراه بیمار تحویل شد"}, nurse, ref_event_id=held.event_id),
        principal=nurse,
        shift=SHIFT,
    )
    assert delivered.created is True

    # One held belonging goes back once; a second delivery of it would double the record.
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission("patient.item_delivered", {}, nurse, ref_event_id=held.event_id),
            principal=nurse,
            shift=SHIFT,
        )


def test_vitals_are_recorded_with_one_reading_and_move_no_projection(seeded: Database) -> None:
    service = _service(seeded)
    nurse = _principal("nurse")
    before = _projections(seeded)

    # No threshold rule of ours: an empty set of readings is the only thing refused here.
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission("vitals.recorded", {"admission_no": "A-2001"}, nurse),
            principal=nurse,
            shift=SHIFT,
        )

    result = service.record(_submission("vitals.recorded", READINGS, nurse), principal=nurse, shift=SHIFT)

    assert result.created is True
    assert _projections(seeded) == before, "a vital sign is a record, not a shelf or a cash movement"


def test_a_followup_is_opened_against_a_vitals_record(seeded: Database) -> None:
    service = _service(seeded)
    nurse = _principal("nurse")
    readings = service.record(_submission("vitals.recorded", READINGS, nurse), principal=nurse, shift=SHIFT)

    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission("vitals.followup_opened", {"note": "کنترل دوبارهٔ فشار"}, nurse),
            principal=nurse,
            shift=SHIFT,
        )

    held = service.record(_submission("patient.item_held", HELD, nurse), principal=nurse, shift=SHIFT)
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission("vitals.followup_opened", {"note": "کنترل دوبارهٔ فشار"}, nurse, ref_event_id=held.event_id),
            principal=nurse,
            shift=SHIFT,
        )

    opened = service.record(
        _submission(
            "vitals.followup_opened",
            {"note": "کنترل دوبارهٔ فشار"},
            nurse,
            ref_event_id=readings.event_id,
        ),
        principal=nurse,
        shift=SHIFT,
    )
    assert opened.created is True


def test_cleaning_confirms_only_the_items_of_the_chosen_location(seeded: Database) -> None:
    service = _service(seeded)
    reception = _principal("reception")

    # Normal saline lives in the CPR room, so a confirmation of the reception shelf cannot name it.
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission(
                "cleaning.confirmed",
                {"location_id": "shelf_reception", "items": ["para_500_amp", "saline_500_bag"]},
                reception,
            ),
            principal=reception,
            shift=SHIFT,
        )

    result = service.record(
        _submission("cleaning.confirmed", CLEANING, reception),
        principal=reception,
        shift=SHIFT,
    )

    assert result.created is True
