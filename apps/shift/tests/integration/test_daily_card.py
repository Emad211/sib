"""The manager's daily card end to end: every section from one synthetic clinic day (S01 §6, SHF-6.1).

The card is the page the manager opens at 07:30, so this module builds one real day of synthetic work
and reads the stored document back: a closed reception flow, an emergency nursing flow, a named and an
unnamed discrepancy, cash exceptions, a flow nobody closed, and the shift events of SHF-3 — a waiting
transfer, an approved one, a stock entry, a care record and a fault chain. It also pins the rules that
cost the clinic money or break R-02: regenerating a day replaces its one document instead of stacking a
duplicate, a forgotten fault reaches `open_events` however old it is, a decision is never a row of its
own, a stored schema-2 card still reads with the two new sections empty, and no frozen reference number
ever reaches the card, its stored document or its rendered page — the audit trail keeps those alone.

Every value here is invented; nothing reads a production database or real patient data (ADR-006).
"""

import json
from datetime import date, datetime

import pytest
from auth_fixtures import PIN, create_device, create_staff, login
from clocks import FixedClock
from fastapi.testclient import TestClient
from seed_reference_data import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import card_repo, handover_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import TEHRAN, tehran_iso
from shift.common.hashing import canonical_json
from shift.config.settings import Settings
from shift.domain.daily_card import CARD_SCHEMA_VERSION
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.domain.shift_events import DECISION_TYPES
from shift.services.audit import AuditEntry, AuditService
from shift.services.daily_card import SECTION_NAMES, Card, DailyCardService, Person
from shift.services.event_store import EventStore

GENERATED_AT = datetime(2026, 10, 27, 7, 31, tzinfo=TEHRAN)  # Tuesday's card, just after 07:30
MONDAY = date(2026, 10, 26)
CARD_DAY = date(2026, 10, 27)
THURSDAY = date(2026, 10, 29)
FRIDAY = date(2026, 10, 30)
SATURDAY = date(2026, 10, 31)
# A frozen reference as `handover.reference.<id>` holds it: the card must never carry either figure.
CASH_REFERENCE = 1_234_567
ITEM_REFERENCE = 987_654
# The two faults nobody closed three days before the card (SHF-3): one still free, one handed over.
OLD_FAULT_DEVICE = "یخچال دارو"
ASSIGNED_FAULT_DEVICE = "چراغ اتاق CPR"
STAFF = {
    "staff-out": "Sara Rahimi",
    "staff-in": "Nima Karimi",
    "night-out": "Reza Moradi",
    "nurse-out": "Mina Sadeghi",
    "nurse-in": "Roya Esmaili",
    "nurse-2-in": "Ali Nouri",
    "manager-1": "Dr. Hamid Tavakoli",
    "manager-2": "Dr. Parisa Jalali",
}
STAFF_ROLES = {
    "staff-out": ("reception",),
    "staff-in": ("reception",),
    "night-out": ("nurse",),
    "nurse-out": ("nurse",),
    "nurse-in": ("nurse",),
    "nurse-2-in": ("nurse",),
    "manager-1": ("manager",),
    "manager-2": ("manager",),
}
# Named so the page test can sign in as the manager the card is for.
STAFF_MOBILES = {staff_id: f"091210000{index:02d}" for index, staff_id in enumerate(STAFF)}


def _monday(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 26, hour, minute, tzinfo=TEHRAN)


def _opened_earlier(hour: int, minute: int = 0) -> datetime:
    """Three days before the card: a fault recorded then is still the manager's problem now."""
    return datetime(2026, 10, 23, hour, minute, tzinfo=TEHRAN)


@pytest.fixture()
def clock() -> FixedClock:
    """The instant the Tuesday card is built; every append below moves it, the fixture resets it."""
    return FixedClock(GENERATED_AT)


@pytest.fixture()
def service(database: Database, clock: FixedClock) -> DailyCardService:
    return DailyCardService(database, clock=clock)


def _staff(database: Database, *, pepper: str = "") -> None:
    """Synthetic roster and one paired phone per person; `night-in` is absent, as a leaver would be."""
    for staff_id, full_name in STAFF.items():
        create_staff(
            database,
            staff_id=staff_id,
            full_name=full_name,
            mobile=STAFF_MOBILES[staff_id],
            roles=STAFF_ROLES[staff_id],
            pepper=pepper,
        )
        create_device(database, device_id=f"device-{staff_id}", staff_id=staff_id)


def _flow(
    database: Database,
    *,
    handover_id: str,
    shift_id: str,
    flow: HandoverFlow,
    opened_at: datetime,
    outgoing: str,
    incoming: str,
    signed_at: datetime | None = None,
    emergency_by: str | None = None,
    early_left: bool = False,
) -> None:
    with database.write() as connection:
        handover_repo.insert(
            connection,
            handover_id=handover_id,
            shift_id=shift_id,
            flow=flow,
            opened_at=opened_at,
            work_date=ShiftKey.parse(shift_id).work_date,
            outgoing_id=outgoing,
            incoming_id=incoming,
            created_at=opened_at,
        )
        if signed_at is not None:
            handover_repo.mark_signed(connection, handover_id, signed_at)
        if early_left:
            handover_repo.mark_early_left(connection, handover_id)
        if emergency_by is not None:
            handover_repo.mark_emergency(connection, handover_id, emergency_by)


def _event(
    store: EventStore,
    clock: FixedClock,
    at: datetime,
    event_type: str,
    *,
    shift_id: str,
    actor_id: str,
    payload: dict[str, object],
    handover_id: str | None = None,
    ref_event_id: str | None = None,
) -> str:
    """Append one event and return its id, so a decision or a fault closing can reference it."""
    clock.moment = at
    result = store.append(
        build_event(
            event_type,
            payload=payload,
            handover_id=handover_id,
            shift_id=shift_id,
            actor_id=actor_id,
            client_at=at,
            work_date=ShiftKey.parse(shift_id).work_date,
            ref_event_id=ref_event_id,
        )
    )
    return str(result.event.event_id)


def _audit(
    audit: AuditService,
    clock: FixedClock,
    at: datetime,
    action: str,
    *,
    subject_id: str,
    detail: dict[str, object],
) -> None:
    clock.moment = at
    audit.record(AuditEntry(actor_id="staff-in", action=action, subject_id=subject_id, detail=detail))


def _seed_the_day(database: Database, clock: FixedClock, *, pepper: str = "") -> None:
    """One Monday of synthetic work, its two forgotten faults, and the frozen reference audit row."""
    store = EventStore(database, clock=clock)
    audit = AuditService(database, clock=clock)
    seed_catalog(database)
    _staff(database, pepper=pepper)
    _flow(
        database,
        handover_id="ho-open",
        shift_id="2026-10-25#3",
        flow=HandoverFlow.NURSING,
        opened_at=datetime(2026, 10, 25, 20, 0, tzinfo=TEHRAN),
        outgoing="night-out",
        incoming="night-in",  # no longer on the roster: the card keeps the id and shows no name
    )
    _flow(
        database,
        handover_id="ho-reception",
        shift_id="2026-10-26#1",
        flow=HandoverFlow.RECEPTION,
        opened_at=_monday(8, 5),
        outgoing="staff-out",
        incoming="staff-in",
        signed_at=_monday(8, 40),
    )
    _flow(
        database,
        handover_id="ho-nursing",
        shift_id="2026-10-26#2",
        flow=HandoverFlow.NURSING,
        opened_at=_monday(14, 0),
        outgoing="nurse-out",
        incoming="nurse-in",
        signed_at=_monday(14, 30),
        emergency_by="manager-1",
        early_left=True,
    )
    _flow(
        database,
        handover_id="ho-nursing-2",
        shift_id="2026-10-26#3",
        flow=HandoverFlow.NURSING,
        opened_at=_monday(17, 20),
        outgoing="nurse-out",
        incoming="nurse-2-in",
        signed_at=_monday(17, 40),
        emergency_by="manager-2",
        early_left=True,
    )
    # Three days before the card: two faults nobody closed. The second was handed to a person but
    # never fixed, so it reaches the card as `assigned`; the first was never even assigned.
    _event(
        store,
        clock,
        _opened_earlier(10, 0),
        "equipment.fault_reported",
        shift_id="2026-10-23#2",
        actor_id="nurse-out",
        payload={"device": OLD_FAULT_DEVICE, "description": "دما بالا می‌رود"},
    )
    assigned_fault = _event(
        store,
        clock,
        _opened_earlier(11, 0),
        "equipment.fault_reported",
        shift_id="2026-10-23#2",
        actor_id="nurse-out",
        payload={"device": ASSIGNED_FAULT_DEVICE, "description": "روشن نمی‌شود"},
    )
    _event(
        store,
        clock,
        _opened_earlier(11, 30),
        "equipment.fault_assigned",
        shift_id="2026-10-23#2",
        actor_id="manager-2",
        payload={"assignee_id": "nurse-2-in", "due_on": "2026-10-25"},
        ref_event_id=assigned_fault,
    )
    # Monday, in the order the server recorded the events; the card reads the newest first.
    _event(
        store,
        clock,
        _monday(9, 15),
        "discrepancy.recorded",
        handover_id="ho-reception",
        shift_id="2026-10-26#1",
        actor_id="staff-in",
        payload={
            "scope": "item",
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "difference": -91_111,
            "culprit_id": "staff-out",
        },
    )
    _event(
        store,
        clock,
        _monday(9, 40),
        "card.transfer_reported",
        shift_id="2026-10-26#1",
        actor_id="staff-in",
        payload={"amount": 150_000, "admission_no": "A-1001"},
    )
    approved_transfer = _event(
        store,
        clock,
        _monday(10, 10),
        "card.transfer_reported",
        shift_id="2026-10-26#1",
        actor_id="staff-in",
        payload={"amount": 90_000, "admission_no": "A-1002"},
    )
    _event(
        store,
        clock,
        _monday(10, 20),
        "event.approved",
        shift_id="2026-10-26#1",
        actor_id="manager-1",
        payload={"note": "تأیید شد"},
        ref_event_id=approved_transfer,
    )
    _event(
        store,
        clock,
        _monday(11, 0),
        "stock.shelf_restocked",
        shift_id="2026-10-26#2",
        actor_id="nurse-in",
        payload={
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 5,
            "source": "store",
            "reference": None,
        },
    )
    _event(
        store,
        clock,
        _monday(12, 0),
        "vitals.recorded",
        shift_id="2026-10-26#2",
        actor_id="nurse-in",
        payload={"systolic": 120, "diastolic": 80, "sugar": 110},
    )
    _event(
        store,
        clock,
        _monday(12, 30),
        "cleaning.confirmed",
        shift_id="2026-10-26#2",
        actor_id="nurse-in",
        payload={"location_id": "shelf_reception", "items": ["para_500_amp", "diclofenac_75_amp"]},
    )
    fixed_fault = _event(
        store,
        clock,
        _monday(13, 0),
        "equipment.fault_reported",
        shift_id="2026-10-26#2",
        actor_id="nurse-in",
        payload={"device": "دستگاه فشار", "description": "صفحه نمایش روشن نمی‌شود"},
    )
    _event(
        store,
        clock,
        _monday(13, 20),
        "equipment.fault_assigned",
        shift_id="2026-10-26#2",
        actor_id="manager-1",
        payload={"assignee_id": "nurse-2-in", "due_on": "2026-10-28"},
        ref_event_id=fixed_fault,
    )
    _event(
        store,
        clock,
        _monday(13, 40),
        "equipment.fault_fixed",
        shift_id="2026-10-26#2",
        actor_id="nurse-2-in",
        payload={"note": "تعمیر شد"},
        ref_event_id=fixed_fault,
    )
    _event(
        store,
        clock,
        _monday(14, 10),
        "handover.emergency_start_confirmed",
        handover_id="ho-nursing",
        shift_id="2026-10-26#2",
        actor_id="nurse-in",
        payload={"manager_id": "manager-1"},
    )
    _event(
        store,
        clock,
        _monday(14, 20),
        "discrepancy.recorded",
        handover_id="ho-nursing",
        shift_id="2026-10-26#2",
        actor_id="nurse-in",
        payload={"scope": "cash", "difference": 5_000, "culprit_id": None},
    )
    _event(
        store,
        clock,
        _monday(17, 30),
        "handover.emergency_start_confirmed",
        handover_id="ho-nursing-2",
        shift_id="2026-10-26#3",
        actor_id="nurse-2-in",
        payload={"manager_id": "manager-2"},
    )
    _audit(
        audit,
        clock,
        _monday(10, 0),
        "handover.cash_exception",
        subject_id="ho-reception",
        detail={"flow": "reception", "amount": 450_000},
    )
    _audit(
        audit,
        clock,
        _monday(16, 0),
        "handover.cash_exception",
        subject_id="ho-reception",
        detail={"flow": "reception", "amount": 120_000},
    )
    # The frozen references live here and nowhere else (R-02, ADR-011); the card must not repeat them.
    _audit(
        audit,
        clock,
        _monday(8, 6),
        "handover.reference.ho-reception",
        subject_id="ho-reception",
        detail={
            "cash": CASH_REFERENCE,
            "items": [{"item_id": "para_500_amp", "location_id": "shelf_reception", "reference": ITEM_REFERENCE}],
        },
    )
    clock.moment = GENERATED_AT


@pytest.fixture()
def synthetic_day(database: Database, clock: FixedClock) -> None:
    _seed_the_day(database, clock)


def _stored_payload(database: Database, card_date: date) -> str:
    with database.read() as connection:
        return str(
            connection.exec_driver_sql(
                "SELECT payload FROM daily_cards WHERE card_date = ?", (card_date.isoformat(),)
            ).scalar_one()
        )


def _actions(database: Database) -> list[str]:
    with database.read() as connection:
        return [str(row[0]) for row in connection.exec_driver_sql("SELECT action FROM audit_log ORDER BY id")]


def _stored_audit_detail(database: Database, card_date: date) -> dict[str, object]:
    with database.read() as connection:
        row = connection.exec_driver_sql(
            "SELECT detail FROM audit_log WHERE action = 'daily_card.generated' AND subject_id = ?",
            (card_date.isoformat(),),
        ).first()
    assert row is not None
    return json.loads(str(row[0]))


def test_a_regenerated_card_reports_the_whole_covered_day(service: DailyCardService, synthetic_day: None) -> None:
    card = service.regenerate(CARD_DAY)

    assert card is not None
    assert (card.card_date, card.covered_from, card.covered_to, card.day_count) == (CARD_DAY, MONDAY, MONDAY, 1)
    assert card.generated_at == GENERATED_AT
    # H-08: who started the handover alone, which manager confirmed it, and when — newest first.
    assert [entry.handover_id for entry in card.emergency_starts] == ["ho-nursing-2", "ho-nursing"]
    assert card.emergency_starts[0].recorded_by == Person("nurse-2-in", "Ali Nouri")
    assert card.emergency_starts[0].manager == Person("manager-2", "Dr. Parisa Jalali")
    assert card.emergency_starts[0].at_tehran == tehran_iso(_monday(17, 30))
    assert card.emergency_starts[1].flow == "nursing"
    assert card.emergency_starts[1].manager == Person("manager-1", "Dr. Hamid Tavakoli")
    # The audit trail's cash exceptions, newest first: the receipts total, never a reference.
    assert [(entry.amount, entry.flow) for entry in card.cash_exceptions] == [
        (120_000, "reception"),
        (450_000, "reception"),
    ]
    assert card.cash_exceptions[0].at_tehran == tehran_iso(_monday(16, 0))
    # The gap is rendered as a magnitude; an unnamed culprit stays unnamed (H-08, H-07).
    assert card.discrepancies[0].culprit is None
    assert (card.discrepancies[0].flow, card.discrepancies[0].item_id, card.discrepancies[0].difference) == (
        "nursing",
        None,
        5_000,
    )
    assert card.discrepancies[1].culprit == Person("staff-out", "Sara Rahimi")
    assert card.discrepancies[1].difference == 91_111
    # The size alone is not enough for a manager: the card says which way the gap went.
    assert card.discrepancies[0].direction == "shortage"
    assert card.discrepancies[1].direction == "surplus"
    assert card.discrepancies[1].at_tehran == tehran_iso(_monday(9, 15))
    # The flows of the covered day only, oldest first, with the two flags the log carries.
    assert [flow.handover_id for flow in card.flows] == ["ho-reception", "ho-nursing", "ho-nursing-2"]
    reception = card.flows[0]
    assert (reception.flow, reception.shift, reception.closed, reception.emergency, reception.early_left) == (
        "reception",
        "2026-10-26#1",
        True,
        False,
        False,
    )
    assert reception.opened_at_tehran == tehran_iso(_monday(8, 5))
    assert reception.outgoing == Person("staff-out", "Sara Rahimi")
    assert reception.incoming == Person("staff-in", "Nima Karimi")
    assert (card.flows[1].emergency, card.flows[1].early_left, card.flows[1].closed) == (True, True, True)
    # A flow nobody closed is on the card whatever day it was opened on, oldest first.
    assert [flow.handover_id for flow in card.open_flows] == ["ho-open"]
    assert card.open_flows[0].age_hours == 35
    assert card.open_flows[0].incoming == Person("night-in", None)


def test_shift_events_read_every_kind_with_its_status_and_its_names(
    service: DailyCardService, synthetic_day: None
) -> None:
    """SHF-3: every registered kind of the window, newest first; a decision only decides a status."""
    card = service.regenerate(CARD_DAY)
    assert card is not None

    assert [(row.event_type, row.status) for row in card.shift_events] == [
        ("equipment.fault_fixed", "recorded"),
        ("equipment.fault_assigned", "recorded"),
        ("equipment.fault_reported", "fixed"),
        ("cleaning.confirmed", "recorded"),
        ("vitals.recorded", "recorded"),
        ("stock.shelf_restocked", "recorded"),
        ("card.transfer_reported", "approved"),
        ("card.transfer_reported", "pending"),
    ]
    # A decision event is never a row of its own; it is the status of the transfer it decided.
    assert all(row.event_type not in DECISION_TYPES for row in card.shift_events)

    fixed, assigned, report, cleaning, vitals, restock, approved, pending = card.shift_events
    assert (approved.status, pending.status) == ("approved", "pending")
    assert approved.summary == {"amount": 90_000, "admission_no": "A-1002"}
    assert pending.summary == {"amount": 150_000, "admission_no": "A-1001"}
    # Ids become the names the roster and the catalog hold now; numbers and notes stay as recorded.
    assert restock.summary == {
        "quantity": 5,
        "item_id": "پاراستامول ۵۰۰ میلی‌گرم",
        "source": "انبار",
        "reference": None,
    }
    assert vitals.summary == {"systolic": 120, "diastolic": 80, "sugar": 110}
    assert cleaning.summary == {
        "location_id": "قفسهٔ پذیرش",
        "items": ["پاراستامول ۵۰۰ میلی‌گرم", "دیکلوفناک ۷۵"],
    }
    assert (report.code, report.label_fa) == ("خ", "خرابی تجهیزات")
    assert report.actor == Person("nurse-in", "Roya Esmaili")
    assert report.summary == {"device": "دستگاه فشار", "description": "صفحه نمایش روشن نمی‌شود"}
    assert report.at_tehran == tehran_iso(_monday(13, 0))
    assert report.ref_event_id is None, "the fault report hangs on nothing"
    # The assignment and the fix point at the one report, and the report reads `fixed` because of it.
    assert assigned.ref_event_id == fixed.ref_event_id
    assert assigned.summary == {"assignee_id": "Ali Nouri", "due_on": "2026-10-28"}
    assert fixed.actor == Person("nurse-2-in", "Ali Nouri")
    assert fixed.summary == {"note": "تعمیر شد"}


def test_open_events_hold_a_forgotten_fault_and_a_waiting_approval(
    service: DailyCardService, synthetic_day: None
) -> None:
    """Open means still waiting: a three-day-old fault and an undecided transfer, oldest first."""
    card = service.regenerate(CARD_DAY)
    assert card is not None

    assert [(entry.row.event_type, entry.row.status) for entry in card.open_events] == [
        ("equipment.fault_reported", "reported"),
        ("equipment.fault_reported", "assigned"),
        ("card.transfer_reported", "pending"),
    ]
    # Whole hours to the build moment: three days plus the morning, and the same night's transfer.
    assert [entry.age_hours for entry in card.open_events] == [93, 92, 21]
    assert all(entry.age_hours >= 72 for entry in card.open_events[:2]), "the forgotten fault shows its age"
    assert card.open_events[0].row.summary["device"] == OLD_FAULT_DEVICE
    assert card.open_events[0].row.at_tehran == tehran_iso(_opened_earlier(10, 0))
    assert card.open_events[1].row.summary["device"] == ASSIGNED_FAULT_DEVICE
    assert card.open_events[2].row.at_tehran == tehran_iso(_monday(9, 40))
    # A fixed fault, an approved transfer and a merely recorded event are waiting on nobody.
    open_times = {entry.row.at_tehran for entry in card.open_events}
    assert tehran_iso(_monday(13, 0)) not in open_times
    assert tehran_iso(_monday(10, 10)) not in open_times
    assert all(entry.row.status in {"pending", "reported", "assigned"} for entry in card.open_events)


def test_the_stored_payload_is_the_card_and_never_a_reference(
    service: DailyCardService, database: Database, synthetic_day: None
) -> None:
    card = service.regenerate(CARD_DAY)
    assert card is not None

    stored = _stored_payload(database, CARD_DAY)
    document = json.loads(stored)

    assert document["schema"] == CARD_SCHEMA_VERSION
    assert set(document) == {"schema", "card_date", "covered_from", "covered_to", "generated_at", "sections"}
    # Stored as canonical JSON, so the keys arrive sorted and the same day always stores the same text.
    assert set(document["sections"]) == {
        "emergency_starts",
        "cash_exceptions",
        "discrepancies",
        "flows",
        "open_flows",
        "shift_events",
        "open_events",
    }
    assert document["card_date"] == "2026-10-27"
    assert document["generated_at"] == "2026-10-27T04:01:00+00:00"
    assert document["sections"]["discrepancies"][0]["culprit"] is None
    # The two shift-event sections are rows of the same document, in the same canonical form.
    assert document["sections"]["shift_events"][0]["status"] == "recorded"
    assert document["sections"]["open_events"][-1]["status"] == "pending"
    assert document["sections"]["open_events"][0]["age_hours"] == 93
    stored_card = service.get(CARD_DAY)
    assert stored_card is not None
    assert stored_card.to_payload() == document
    assert service.latest() == card
    # R-02/ADR-011: the frozen references stay in the audit trail and never reach the card.
    with database.read() as connection:
        reference_row = connection.exec_driver_sql(
            "SELECT detail FROM audit_log WHERE action = 'handover.reference.ho-reception'"
        ).scalar_one()
    assert str(CASH_REFERENCE) in reference_row
    assert str(CASH_REFERENCE) not in stored
    assert str(ITEM_REFERENCE) not in stored


def test_a_schema_2_card_reads_with_empty_shift_events_and_a_new_card_writes_schema_3(
    service: DailyCardService, database: Database, synthetic_day: None
) -> None:
    """The two new sections are additive: a card stored before them still opens, just without them."""
    card = service.regenerate(CARD_DAY)
    assert card is not None
    document = json.loads(_stored_payload(database, CARD_DAY))
    assert document["schema"] == CARD_SCHEMA_VERSION == 3

    legacy = {
        "schema": 2,
        "card_date": document["card_date"],
        "covered_from": document["covered_from"],
        "covered_to": document["covered_to"],
        "generated_at": document["generated_at"],
        "sections": {name: document["sections"][name] for name in SECTION_NAMES[:5]},
    }
    with database.write() as connection:
        card_repo.upsert(
            connection,
            {
                "card_date": CARD_DAY.isoformat(),
                "covered_from": MONDAY.isoformat(),
                "covered_to": MONDAY.isoformat(),
                "schema_version": 2,
                "generated_at_utc": document["generated_at"],
                "generated_at_tehran": tehran_iso(card.generated_at),
                "payload": canonical_json(legacy),
            },
        )

    stored = service.get(CARD_DAY)
    assert stored is not None
    assert stored.present_sections == SECTION_NAMES[:5]
    assert stored.shift_events == ()
    assert stored.open_events == ()
    assert stored.emergency_starts == card.emergency_starts
    # A document with a version this code does not know is not guessed at.
    for schema in (1, 4):
        with pytest.raises(ValueError):
            Card.from_payload({**legacy, "schema": schema})


def test_the_rendered_card_page_shows_the_shift_events_and_no_reference(settings: Settings) -> None:
    """The manager's page renders the two sections, and the frozen references stay in the audit trail."""
    with TestClient(create_app(settings)) as client:
        database = client.app.state.database
        _seed_the_day(database, FixedClock(GENERATED_AT), pepper=client.app.state.secret_key)
        assert client.app.state.daily_card.regenerate(CARD_DAY) is not None
        signed_in = login(client, mobile=STAFF_MOBILES["manager-1"], device_id="device-manager-1", pin=PIN)
        assert signed_in.status_code == 200, signed_in.text

        page = client.get("/manager/card")

        # R-02/ADR-011: the references really are in the trail, and the page never repeats either figure.
        with database.read() as connection:
            reference_row = connection.exec_driver_sql(
                "SELECT detail FROM audit_log WHERE action = 'handover.reference.ho-reception'"
            ).scalar_one()

    assert page.status_code == 200
    text = page.text
    assert "رویدادهای شیفت" in text
    assert "رویدادهای باز" in text
    # The rows: the kind's Persian label, its code, its actor, its instant and its own fields.
    assert "کارت‌به‌کارت" in text
    assert "در انتظار تأیید" in text
    assert "تأییدشده" in text
    assert "خرابی تجهیزات" in text
    assert "تعمیرشده" in text
    assert "دستگاه فشار" in text
    assert "پاراستامول ۵۰۰ میلی‌گرم" in text
    assert "قفسهٔ پذیرش" in text
    assert "پاراستامول ۵۰۰ میلی‌گرم، دیکلوفناک ۷۵" in text
    assert f"{90_000:,} تومان" in text
    assert "ساعت باز" in text
    assert str(CASH_REFERENCE) in str(reference_row)
    for figure in (CASH_REFERENCE, ITEM_REFERENCE):
        assert str(figure) not in text
        assert f"{figure:,}" not in text


def test_regenerating_a_day_keeps_one_row_and_one_document(
    service: DailyCardService, database: Database, synthetic_day: None
) -> None:
    """The log is append-only, but the card is derived: a second run replaces the day, never doubles it."""
    first = service.regenerate(CARD_DAY)
    first_document = _stored_payload(database, CARD_DAY)
    second = service.regenerate(CARD_DAY)

    assert second == first
    assert _stored_payload(database, CARD_DAY) == first_document
    with database.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM daily_cards").scalar_one() == 1
        assert card_repo.newest_date(connection) == CARD_DAY
    assert [card.card_date for card in service.history()] == [CARD_DAY]
    # Each run is its own audited act, so the trail shows two generations while the card stays one.
    assert _actions(database).count("daily_card.generated") == 2
    assert _stored_audit_detail(database, CARD_DAY) == {
        "card_date": "2026-10-27",
        "covered_from": "2026-10-26",
        "covered_to": "2026-10-26",
        "days": 1,
    }


def test_generate_due_stores_the_two_day_saturday_card_and_nothing_on_friday(
    service: DailyCardService, database: Database, clock: FixedClock
) -> None:
    """S01 §6, ADR-018: Friday has no card; Saturday's card carries Thursday and Friday."""
    clock.moment = datetime(2026, 10, 29, 9, 0, tzinfo=TEHRAN)
    assert service.regenerate(THURSDAY) is not None

    assert service.generate_due(datetime(2026, 10, 30, 7, 31, tzinfo=TEHRAN)) == ()
    assert service.get(FRIDAY) is None
    assert service.build(FRIDAY) is None

    saturday = service.generate_due(datetime(2026, 10, 31, 7, 31, tzinfo=TEHRAN))

    assert [card.card_date for card in saturday] == [SATURDAY]
    assert (saturday[0].covered_from, saturday[0].covered_to, saturday[0].day_count) == (THURSDAY, FRIDAY, 2)
    assert service.get(SATURDAY) == saturday[0]
