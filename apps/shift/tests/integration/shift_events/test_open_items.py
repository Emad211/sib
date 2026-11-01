"""An open item outlives its shift (F2): who closes it, and in which shift (SHF-3).

The night shift records a withdrawal, a fault or a held belonging; the person who has to close it —
the recipient, the manager, the assignee, the patient at the desk next morning — arrives later. So a
decision may decide an undecided target of an earlier shift, the three life-cycle references
(assignment, fix, delivery) may point across shifts, and the pending list reads the whole log instead
of the running shift. Every other reference keeps the same-shift rule, because a shift never borrows
another shift's records (R-01).

The real `ShiftEventService` on a migrated synthetic database; every value below is invented
(ADR-006).
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, Seeded, seed_roster
from clocks import FixedClock, inside_shift
from photo_fixtures import RECEIPT_SHA, with_photo
from synthetic_catalog import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.permissions import permissions_for
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, SubmissionResult
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventRuleError, ShiftEventService

WORK_DATE = date(2026, 10, 28)
# The night shift records the item, the morning shift that follows has to close it.
NIGHT = ShiftKey(WORK_DATE, 3)
MORNING = NIGHT.following()
CLIENT_AT = inside_shift(WORK_DATE, NIGHT.rank)
OPENING_CASH = 1_000_000
ITEM = "para_500_amp"
SHELF = "shelf_reception"
SHELF_BEFORE = 10
WITHDRAWAL = {"amount": 100_000, "recipient_id": ROLES["nurse"].staff_id, "reason": "خرید فوری", "manager_id": None}
TRANSFER = {"amount": 150_000, "admission_no": "A-1001"}
FAULT = {"device": "دستگاه فشار", "description": "صفحه نمایش روشن نمی‌شود", "location_id": "cpr_room"}
HELD = {"item_name": "عینک بیمار", "patient_name": "بیمار آزمایشی", "admission_no": "A-2001"}
READINGS = {"systolic": 120, "diastolic": 80, "sugar": 110, "admission_no": "A-2001"}


def _principal(person: Seeded) -> Principal:
    """The session a route would hand the service for one synthetic staff member."""
    return Principal(
        staff_id=person.staff_id,
        full_name=person.full_name,
        roles=person.roles,
        permissions=permissions_for(person.roles),
        device_id=person.device_id,
        device_label="گوشی آزمایشی",
        session_hash=f"session-{person.staff_id}",
        csrf_token=f"csrf-{person.staff_id}",
        expires_at=datetime(2026, 10, 29, 23, 0, tzinfo=UTC),
    )


def _service(database: Database) -> ShiftEventService:
    """The real service over the synthetic database, wired as the app wires it."""
    return ShiftEventService(
        database,
        EventStore(database),
        ProjectionEngine(database),
        CatalogService(database),
        SettingsService(database),
        clock=FixedClock(CLIENT_AT),
    )


def _record(
    service: ShiftEventService,
    principal: Principal,
    event_type: str,
    payload: dict[str, Any],
    *,
    shift: ShiftKey,
    ref_event_id: str | None = None,
) -> SubmissionResult:
    """One phone event of `shift`, stamped the way the route stamps it from the running shift."""
    moment = inside_shift(shift.work_date, shift.rank)
    event_id = new_ulid(moment)
    return service.record(
        EventSubmission(
            type=event_type,
            payload=with_photo(event_type, payload),
            actor_id=principal.staff_id,
            device_id=principal.device_id,
            client_at=moment,
            client_event_id=event_id,
            shift_id=shift.text,
            ref_event_id=ref_event_id,
            idempotency_key=event_id,
            shift_work_date=shift.work_date,
        ),
        principal=principal,
        shift=shift,
    )


def _cash(database: Database) -> list[dict[str, Any]]:
    """The reception box as the projection holds it."""
    with database.read() as connection:
        return projections_repo.cash_rows(connection)


@pytest.fixture()
def clinic(database: Database) -> Database:
    """A synthetic clinic with a roster, the catalog, one opened box and one counted shelf."""
    seed_roster(database, pepper=b"synthetic-pepper")
    seed_catalog(database)
    EventStore(database).append_many(
        [
            build_event(
                "handover.started",
                payload={"opening_cash": OPENING_CASH},
                shift_id=NIGHT.text,
                work_date=WORK_DATE,
            ),
            build_event(
                "stock.weekly_count",
                payload={"item_id": ITEM, "location_id": SHELF, "counted": SHELF_BEFORE},
                shift_id=NIGHT.text,
                work_date=WORK_DATE,
            ),
        ]
    )
    ProjectionEngine(database).sync()
    return database


def test_a_night_shift_withdrawal_is_confirmed_the_next_morning(clinic: Database) -> None:
    """The recipient reads last night's withdrawal in the morning and closes it from there."""
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    nurse = _principal(ROLES["nurse"])
    recorded = _record(service, reception, "cash.withdrawn", WITHDRAWAL, shift=NIGHT)
    cash = _cash(clinic)

    pending = service.pending_for(nurse)
    assert [entry.event_id for entry in pending] == [recorded.event_id]
    entry = pending[0]
    # The row names its own shift, its own author and its own summary — never the running shift.
    assert (entry.shift_id, entry.code, entry.label_fa) == (NIGHT.text, "د", "برداشت از صندوق")
    assert entry.actor["id"] == reception.staff_id
    assert entry.summary == {
        "amount": 100_000,
        "recipient_id": nurse.staff_id,
        "reason": "خرید فوری",
        "photo_sha256": RECEIPT_SHA,
    }

    decision = _record(service, nurse, "event.approved", {}, shift=MORNING, ref_event_id=recorded.event_id)

    assert decision.created
    assert service.pending_for(nurse) == []
    assert _cash(clinic) == cash, "the box moved when the withdrawal was recorded, not when confirmed"


def test_open_items_of_every_shift_come_oldest_first(clinic: Database) -> None:
    """One list per person, across shifts, in the order the log recorded them."""
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    nurse = _principal(ROLES["nurse"])
    accountant = _principal(ROLES["accountant"])

    older = _record(service, reception, "cash.withdrawn", WITHDRAWAL, shift=NIGHT)
    transfer = _record(service, reception, "card.transfer_reported", TRANSFER, shift=NIGHT)
    newer = _record(service, reception, "cash.withdrawn", {**WITHDRAWAL, "amount": 50_000}, shift=MORNING)

    assert [entry.event_id for entry in service.pending_for(nurse)] == [older.event_id, newer.event_id]
    assert [entry.event_id for entry in service.pending_for(accountant)] == [transfer.event_id]


def test_a_fault_reported_at_night_is_assigned_and_fixed_in_the_morning(clinic: Database) -> None:
    """The fault chain is one incident, so its two notes reach the report of an earlier shift."""
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    manager = _principal(ROLES["manager"])
    nurse = _principal(ROLES["nurse"])
    fault = _record(service, reception, "equipment.fault_reported", FAULT, shift=NIGHT)

    assigned = _record(
        service,
        manager,
        "equipment.fault_assigned",
        {"assignee_id": nurse.staff_id, "due_on": MORNING.work_date.isoformat()},
        shift=MORNING,
        ref_event_id=fault.event_id,
    )
    fixed = _record(
        service,
        nurse,
        "equipment.fault_fixed",
        {"note": "صفحه نمایش تعویض شد"},
        shift=MORNING,
        ref_event_id=fault.event_id,
    )

    assert (assigned.created, fixed.created) == (True, True)
    with pytest.raises(ShiftEventRuleError):
        _record(service, nurse, "equipment.fault_fixed", {}, shift=MORNING, ref_event_id=fault.event_id)


def test_a_belonging_held_at_night_is_delivered_in_the_morning(clinic: Database) -> None:
    """The patient comes back the next morning; the delivery still closes last night's held event."""
    service = _service(clinic)
    nurse = _principal(ROLES["nurse"])
    held = _record(service, nurse, "patient.item_held", HELD, shift=NIGHT)

    delivered = _record(
        service,
        nurse,
        "patient.item_delivered",
        {"note": "به همراه بیمار تحویل شد"},
        shift=MORNING,
        ref_event_id=held.event_id,
    )

    assert delivered.created
    with pytest.raises(ShiftEventRuleError):
        _record(service, nurse, "patient.item_delivered", {}, shift=MORNING, ref_event_id=held.event_id)


def test_a_belonging_of_an_earlier_shift_shows_its_patients_initials_only(clinic: Database) -> None:
    """R-01, E02 §1: tomorrow's desk sees the item and the admission, not the full name of the patient."""
    service = _service(clinic)
    nurse = _principal(ROLES["nurse"])
    reception = _principal(ROLES["reception"])
    manager = _principal(ROLES["manager"])
    held = _record(service, nurse, "patient.item_held", HELD, shift=NIGHT)

    def row(principal: Principal, shift: ShiftKey) -> dict:
        return next(t.summary for t in service.open_targets(principal, shift) if t.event_id == held.event_id)

    morning = row(reception, MORNING)
    assert morning["patient_name"] == "ب. آ."
    assert (morning["item_name"], morning["admission_no"]) == (HELD["item_name"], HELD["admission_no"])
    assert row(reception, NIGHT)["patient_name"] == HELD["patient_name"], "its own shift reads the slip"
    assert row(nurse, MORNING)["patient_name"] == HELD["patient_name"], "the person who wrote it reads it"
    assert row(manager, MORNING)["patient_name"] == HELD["patient_name"]


def test_every_other_reference_stays_inside_its_own_shift(clinic: Database) -> None:
    """A follow-up is the night shift's reading, not a bridge to tomorrow: R-01 keeps that rule."""
    service = _service(clinic)
    nurse = _principal(ROLES["nurse"])
    readings = _record(service, nurse, "vitals.recorded", READINGS, shift=NIGHT)

    with pytest.raises(ShiftEventRuleError, match="همین شیفت"):
        _record(
            service,
            nurse,
            "vitals.followup_opened",
            {"note": "کنترل دوبارهٔ فشار"},
            shift=MORNING,
            ref_event_id=readings.event_id,
        )

    opened = _record(
        service,
        nurse,
        "vitals.followup_opened",
        {"note": "کنترل دوبارهٔ فشار"},
        shift=NIGHT,
        ref_event_id=readings.event_id,
    )
    assert opened.created
