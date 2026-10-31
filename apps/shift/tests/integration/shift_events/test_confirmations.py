"""A named person or a role confirms (the owner's rules 3 and 4): waste, a withdrawal, a restock.

The goods and the cash really moved when the event was recorded, so a confirmation changes a status
and never a projection — that is the whole reason the clinic asks for it. What a confirmation changes
is who may close the record: for waste the «تأییدکننده» the recorder named, for a withdrawal its
«گیرنده», and for a mid-shift entry the storekeeper or the reception. The recorder never confirms
their own record (E-12) and nothing confirms itself: when the recipient a withdrawal named is its own
recorder, any other staff member answers instead. The manager may always decide an open record.

The real `ShiftEventService` on a migrated synthetic database; every value below is invented
(ADR-006).
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, SPARE, Seeded, seed_roster
from clocks import FixedClock, inside_shift
from synthetic_catalog import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import events_repo, projections_repo
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
SHIFT = ShiftKey(WORK_DATE, 2)
CLIENT_AT = inside_shift(WORK_DATE, SHIFT.rank)
OPENING_CASH = 1_000_000
ITEM = "para_500_amp"
SHELF = "shelf_reception"
SHELF_BEFORE = 10
WITHDRAWAL = {"amount": 100_000, "recipient_id": ROLES["nurse"].staff_id, "reason": "خرید فوری", "manager_id": None}
WASTE = {
    "item_id": ITEM,
    "location_id": SHELF,
    "quantity": 2,
    "waste_kind": "broken",
    "culprit_id": SPARE.staff_id,
    "confirmer_id": ROLES["nurse"].staff_id,
}
RESTOCK = {"item_id": ITEM, "location_id": SHELF, "quantity": 5, "source": "store", "reference": None}
FAULT = {"device": "دستگاه فشار", "description": "صفحه نمایش روشن نمی‌شود", "location_id": "cpr_room"}


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
    ref_event_id: str | None = None,
) -> SubmissionResult:
    """One phone event of this shift, minted the way the offline outbox mints it."""
    event_id = new_ulid(CLIENT_AT)
    return service.record(
        EventSubmission(
            type=event_type,
            payload=payload,
            actor_id=principal.staff_id,
            device_id=principal.device_id,
            client_at=CLIENT_AT,
            client_event_id=event_id,
            shift_id=SHIFT.text,
            ref_event_id=ref_event_id,
            idempotency_key=event_id,
            shift_work_date=SHIFT.work_date,
        ),
        principal=principal,
        shift=SHIFT,
    )


def _shelf(database: Database) -> int:
    """The projected quantity of the counted shelf."""
    with database.read() as connection:
        rows = {(row["item_id"], row["location_id"]): row["qty"] for row in projections_repo.stock_rows(connection)}
    return rows[(ITEM, SHELF)]


def _cash(database: Database) -> list[dict[str, Any]]:
    """The reception box as the projection holds it."""
    with database.read() as connection:
        return projections_repo.cash_rows(connection)


def _event_types(database: Database) -> list[str]:
    """Every type in the log, in append order."""
    with database.read() as connection:
        return [event.type for event in events_repo.iter_events(connection)]


@pytest.fixture()
def clinic(database: Database) -> Database:
    """A synthetic clinic with a roster, the catalog, one opened box and one counted shelf."""
    seed_roster(database, pepper=b"synthetic-pepper")
    seed_catalog(database)
    EventStore(database).append_many(
        [
            build_event("handover.started", payload={"opening_cash": OPENING_CASH}, shift_id=SHIFT.text),
            build_event(
                "stock.weekly_count",
                payload={"item_id": ITEM, "location_id": SHELF, "counted": SHELF_BEFORE},
                shift_id=SHIFT.text,
            ),
        ]
    )
    ProjectionEngine(database).sync()
    return database


def test_waste_waits_for_the_person_it_names(clinic: Database) -> None:
    """The shelf drops at once and the named second person — not a role — closes the record."""
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    nurse = _principal(ROLES["nurse"])
    recorded = _record(service, reception, "stock.waste_reported", WASTE)

    assert _shelf(clinic) == SHELF_BEFORE - 2, "E-05: the item left the shelf when it was recorded"
    pending = service.pending_for(nurse)
    assert [entry.event_id for entry in pending] == [recorded.event_id]
    assert (pending[0].code, pending[0].summary["quantity"]) == ("ض", 2)
    assert service.pending_for(_principal(ROLES["storekeeper"])) == [], "a storekeeper is not the person named"
    assert service.pending_for(_principal(SPARE)) == [], "another nurse was not the person named"
    assert [entry.event_id for entry in service.pending_for(_principal(ROLES["manager"]))] == [recorded.event_id], (
        "the manager may always decide an open record"
    )

    decision = _record(service, nurse, "event.approved", {}, ref_event_id=recorded.event_id)

    assert decision.created
    assert service.pending_for(nurse) == []
    assert _shelf(clinic) == SHELF_BEFORE - 2, "the confirmation never takes the quantity a second time"


def test_a_withdrawal_waits_for_its_recipient(clinic: Database) -> None:
    """The «گیرنده» confirms the withdrawal; no other role does, but the manager may step in."""
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    nurse = _principal(ROLES["nurse"])
    recorded = _record(service, reception, "cash.withdrawn", WITHDRAWAL)

    pending = service.pending_for(nurse)
    assert [entry.event_id for entry in pending] == [recorded.event_id]
    entry = pending[0]
    # R-01: the row is the record itself — its summary, its author, its time — and no shift figure.
    assert set(entry.to_payload()) == {
        "event_id",
        "type",
        "code",
        "label",
        "at_tehran",
        "shift_id",
        "actor",
        "summary",
    }
    assert entry.summary == {"amount": 100_000, "recipient_id": nurse.staff_id, "reason": "خرید فوری"}
    assert not {"expected", "counted", "variance"} & set(entry.summary)
    assert service.pending_for(_principal(ROLES["accountant"])) == [], "the accountant approves money, not this"
    assert [entry.event_id for entry in service.pending_for(_principal(ROLES["manager"]))] == [recorded.event_id]
    cash = _cash(clinic)

    assert _record(service, nurse, "event.approved", {}, ref_event_id=recorded.event_id).created
    assert service.pending_for(nurse) == []
    assert _cash(clinic) == cash, "the box moved when the withdrawal was recorded, not when confirmed"


def test_a_withdrawal_its_recipient_recorded_is_confirmed_by_anybody_else(clinic: Database) -> None:
    """A recipient may take money themselves, but never sign for it: another person answers instead."""
    service = _service(clinic)
    nurse = _principal(ROLES["nurse"])
    spare = _principal(SPARE)
    accountant = _principal(ROLES["accountant"])
    own = _record(service, nurse, "cash.withdrawn", {**WITHDRAWAL, "recipient_id": nurse.staff_id})

    assert service.pending_for(nurse) == [], "the recorder is never the confirmer"
    assert [entry.event_id for entry in service.pending_for(accountant)] == [own.event_id]
    assert [entry.event_id for entry in service.pending_for(spare)] == [own.event_id]
    with pytest.raises(ShiftEventRuleError, match="ثبت‌کننده"):
        _record(service, nurse, "event.approved", {}, ref_event_id=own.event_id)
    assert "event.approved" not in _event_types(clinic), "nothing confirms a record by itself"

    assert _record(service, spare, "event.approved", {}, ref_event_id=own.event_id).created
    assert service.pending_for(accountant) == []


def test_a_mid_shift_entry_waits_for_the_storekeeper_or_the_reception(clinic: Database) -> None:
    """The shelf grows at once; the owner's «انباردار یا پذیرش» confirms the entry afterwards."""
    service = _service(clinic)
    nurse = _principal(ROLES["nurse"])
    storekeeper = _principal(ROLES["storekeeper"])
    reception = _principal(ROLES["reception"])
    recorded = _record(service, nurse, "stock.shelf_restocked", RESTOCK)

    assert _shelf(clinic) == SHELF_BEFORE + 5, "E-06: the goods already arrived"
    assert [entry.event_id for entry in service.pending_for(storekeeper)] == [recorded.event_id]
    assert [entry.event_id for entry in service.pending_for(reception)] == [recorded.event_id]

    assert _record(service, storekeeper, "event.approved", {}, ref_event_id=recorded.event_id).created
    assert service.pending_for(storekeeper) == []
    assert service.pending_for(reception) == [], "one decision closes the entry for both roles"
    assert _shelf(clinic) == SHELF_BEFORE + 5


def test_a_record_no_one_confirms_answers_it_needs_no_confirmation(clinic: Database) -> None:
    """A fault is closed by its fix, never by a decision, and it is nobody's open item."""
    service = _service(clinic)
    reception = _principal(ROLES["reception"])
    accountant = _principal(ROLES["accountant"])
    fault = _record(service, reception, "equipment.fault_reported", FAULT)

    assert service.pending_for(accountant) == []
    with pytest.raises(ShiftEventRuleError, match="نیاز به تأیید ندارد"):
        _record(service, accountant, "event.approved", {}, ref_event_id=fault.event_id)

    assert "event.approved" not in _event_types(clinic), "a refused decision writes nothing"
