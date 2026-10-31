"""The equipment fault chain (S01 §3 «خرابی»): one incident, three notes — report, assign, fix.

A member of staff reports what is broken, the manager assigns it to a person with a deadline, and the
assignee — or the manager — closes it with «تعمیر شد». The chain only records: none of the three moves
a shelf or the cash box, and none is an approval, so a reported fault never waits in the approval queue
and is closed by a fix, not by a decision (S01 §3 E-09).

Real `ShiftEventService` on a synthetic database; every value is invented (ADR-006).
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, SPARE, Seeded, seed_roster
from seed_reference_data import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
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
SHIFT = ShiftKey(WORK_DATE, 2)
CLIENT_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
REPORTED = "equipment.fault_reported"
ASSIGNED = "equipment.fault_assigned"
FIXED = "equipment.fault_fixed"
# What the phone asks the reporter: what is broken, where it stands, and what a person sees.
FAULT = {"device": "دستگاه فشار", "description": "صفحه نمایش روشن نمی‌شود", "location_id": "cpr_room"}
LATER = "2026-11-02"
BEFORE_THE_SHIFT = "2026-10-27"


def _principal(person: Seeded) -> Principal:
    """The signed-in principal the route would build for one synthetic staff member."""
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
    """The real service over the synthetic database, wired as the app wires it."""
    return ShiftEventService(
        database,
        EventStore(database),
        ProjectionEngine(database),
        CatalogService(database),
        SettingsService(database),
    )


def _submission(
    event_type: str,
    payload: dict[str, Any],
    principal: Principal,
    *,
    ref_event_id: str | None = None,
) -> EventSubmission:
    """One phone event of this shift, minted the way the offline outbox mints it."""
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


def _shift_events(database: Database) -> list[EventRecord]:
    """The events stamped with this shift, in server order."""
    with database.read() as connection:
        return list(events_repo.iter_shift(connection, SHIFT.text))


def _projections(database: Database) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The stock and cash projections as stored, for a before/after comparison."""
    with database.read() as connection:
        return projections_repo.stock_rows(connection), projections_repo.cash_rows(connection)


@pytest.fixture()
def clinic(database: Database) -> Database:
    """A synthetic clinic: a roster, the reference catalog, one stocked shelf and one opened box.

    The shelf and the box are seeded through the event log, so "the fault chain moves nothing" is
    measured against a real projection and not against an empty table.
    """
    seed_roster(database, pepper=b"synthetic-pepper")
    seed_catalog(database)
    EventStore(database).append_many(
        [
            build_event("handover.started", payload={"opening_cash": 500_000}, shift_id="shift-seed"),
            build_event(
                "stock.weekly_count",
                payload={"item_id": "para_500_amp", "location_id": "shelf_reception", "counted": 7},
                shift_id="shift-seed",
            ),
        ]
    )
    ProjectionEngine(database).sync()
    return database


def test_the_chain_records_three_notes_moves_nothing_and_never_pends(clinic: Database) -> None:
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    manager = _principal(ROLES["manager"])
    nurse = _principal(ROLES["nurse"])
    before = _projections(clinic)

    reported = service.record(_submission(REPORTED, FAULT, reception), principal=reception, shift=SHIFT)
    # Assignment is the next step, not an approval: nothing waits for a manager's decision here.
    assert service.pending_for(manager) == []

    assigned = service.record(
        _submission(
            ASSIGNED,
            {"assignee_id": nurse.staff_id, "due_on": WORK_DATE.isoformat()},
            manager,
            ref_event_id=reported.event_id,
        ),
        principal=manager,
        shift=SHIFT,
    )
    fixed = service.record(
        _submission(FIXED, {"note": "کابل دستگاه تعویض و دستگاه آزمایش شد"}, nurse, ref_event_id=reported.event_id),
        principal=nurse,
        shift=SHIFT,
    )

    assert (reported.created, assigned.created, fixed.created) == (True, True, True)
    rows = _shift_events(clinic)
    assert [(row.type, row.ref_event_id) for row in rows] == [
        (REPORTED, None),
        (ASSIGNED, reported.event_id),
        (FIXED, reported.event_id),
    ]
    assert rows[0].payload == FAULT
    assert rows[1].payload == {"assignee_id": nurse.staff_id, "due_on": WORK_DATE.isoformat()}
    assert rows[2].payload == {"note": "کابل دستگاه تعویض و دستگاه آزمایش شد"}
    # H-01, H-02: an equipment note is neither stock nor money.
    assert _projections(clinic) == before


def test_only_the_manager_assigns_to_a_real_person_with_a_deadline_still_ahead(clinic: Database) -> None:
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    manager = _principal(ROLES["manager"])
    nurse = _principal(ROLES["nurse"])
    fault = service.record(_submission(REPORTED, FAULT, reception), principal=reception, shift=SHIFT)

    def assign(
        principal: Principal,
        *,
        assignee: str = ROLES["nurse"].staff_id,
        due_on: str = LATER,
        reference: str | None = None,
    ) -> Any:
        reference = fault.event_id if reference is None else reference
        return service.record(
            _submission(ASSIGNED, {"assignee_id": assignee, "due_on": due_on}, principal, ref_event_id=reference),
            principal=principal,
            shift=SHIFT,
        )

    # S01 §3 E-09: a nurse may report a fault, never decide who fixes it.
    with pytest.raises(ShiftEventRuleError):
        assign(nurse)
    assert [row.type for row in _shift_events(clinic)] == [REPORTED], "a refusal writes nothing"

    # The shift's own day is still a deadline; an earlier day is a deadline nobody can meet.
    assert assign(manager, due_on=WORK_DATE.isoformat()).created is True
    with pytest.raises(ShiftEventRuleError):
        assign(manager, due_on=BEFORE_THE_SHIFT)
    # An assignment is a note about a fault, so it needs one behind it and never another kind.
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission(ASSIGNED, {"assignee_id": nurse.staff_id, "due_on": LATER}, manager),
            principal=manager,
            shift=SHIFT,
        )
    with pytest.raises(ShiftEventRuleError):
        assign(manager, reference=_shift_events(clinic)[1].event_id)
    # A job is given to somebody on the roster, not to a name that is not there.
    with pytest.raises(ShiftEventRuleError):
        assign(manager, assignee="staff-ghost")

    assert [row.type for row in _shift_events(clinic)] == [REPORTED, ASSIGNED]


def test_only_the_assignee_or_a_manager_closes_a_fault_and_only_once(clinic: Database) -> None:
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    manager = _principal(ROLES["manager"])
    nurse = _principal(ROLES["nurse"])
    spare = _principal(SPARE)
    nobody = service.record(_submission(REPORTED, FAULT, reception), principal=reception, shift=SHIFT)

    def fix(principal: Principal, reference: str | None, note: str | None = None) -> Any:
        payload = {} if note is None else {"note": note}
        return service.record(
            _submission(FIXED, payload, principal, ref_event_id=reference),
            principal=principal,
            shift=SHIFT,
        )

    # «تعمیر شد» answers an assignment: a fault nobody was given to is the manager's to close.
    with pytest.raises(ShiftEventRuleError):
        fix(reception, nobody.event_id)
    assert fix(manager, nobody.event_id, "کابل دستگاه تعویض شد").created is True
    # One fix closes a fault.
    with pytest.raises(ShiftEventRuleError):
        fix(manager, nobody.event_id)
    # A fix names the report it closes.
    with pytest.raises(ShiftEventRuleError):
        fix(nurse, None)

    fault = service.record(_submission(REPORTED, FAULT, reception), principal=reception, shift=SHIFT)
    service.record(
        _submission(ASSIGNED, {"assignee_id": nurse.staff_id, "due_on": LATER}, manager, ref_event_id=fault.event_id),
        principal=manager,
        shift=SHIFT,
    )
    # Another member of staff — even one holding the fix permission — cannot close somebody else's job.
    with pytest.raises(ShiftEventRuleError):
        fix(spare, fault.event_id)
    assert fix(nurse, fault.event_id, "صفحه نمایش تعویض شد").created is True


def test_a_fault_already_fixed_is_not_handed_to_anybody_again(clinic: Database) -> None:
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    manager = _principal(ROLES["manager"])
    nurse = _principal(ROLES["nurse"])
    fault = service.record(_submission(REPORTED, FAULT, reception), principal=reception, shift=SHIFT)
    service.record(
        _submission(FIXED, {"note": "دستگاه تعمیر شد"}, manager, ref_event_id=fault.event_id),
        principal=manager,
        shift=SHIFT,
    )

    # The chain is closed: a repaired fault is not work to give out again.
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission(
                ASSIGNED,
                {"assignee_id": nurse.staff_id, "due_on": LATER},
                manager,
                ref_event_id=fault.event_id,
            ),
            principal=manager,
            shift=SHIFT,
        )

    assert [row.type for row in _shift_events(clinic)] == [REPORTED, FIXED]
