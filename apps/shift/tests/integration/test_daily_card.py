"""The manager's daily card end to end: every section from one synthetic clinic day (S01 §6, SHF-6.1).

The card is the page the manager opens at 07:30, so this module builds one real day of synthetic work
and reads the stored document back: a closed reception flow, an emergency nursing flow, a named and an
unnamed discrepancy, cash exceptions, and a flow nobody closed. It also pins the two rules that cost
the clinic money or break R-02: regenerating a day replaces its one document instead of stacking a
duplicate, and no frozen reference number ever reaches the card — the audit trail keeps those alone.

Every value here is invented; nothing reads a production database or real patient data (ADR-006).
"""

import json
from datetime import date, datetime

import pytest
from clocks import FixedClock

from event_fixtures import build_event
from shift.adapters.db import card_repo, handover_repo, staff_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN, tehran_iso
from shift.domain.daily_card import CARD_SCHEMA_VERSION
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.services.audit import AuditEntry, AuditService
from shift.services.daily_card import DailyCardService, Person
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


def _monday(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 26, hour, minute, tzinfo=TEHRAN)


@pytest.fixture()
def clock() -> FixedClock:
    """The instant the Tuesday card is built; every append below moves it, the fixture resets it."""
    return FixedClock(GENERATED_AT)


@pytest.fixture()
def audit(database: Database, clock: FixedClock) -> AuditService:
    return AuditService(database, clock=clock)


@pytest.fixture()
def store(database: Database, clock: FixedClock) -> EventStore:
    return EventStore(database, clock=clock)


@pytest.fixture()
def service(database: Database, clock: FixedClock, audit: AuditService) -> DailyCardService:
    return DailyCardService(database, clock=clock, audit=audit)


def _staff(database: Database) -> None:
    """Synthetic roster; `night-in` is deliberately absent, as a person who left the clinic would be."""
    with database.write() as connection:
        for index, (staff_id, full_name) in enumerate(STAFF.items()):
            staff_repo.insert(
                connection,
                staff_id=staff_id,
                full_name=full_name,
                mobile=f"091200000{index:02d}",
                pin_hash="argon2id$synthetic$not-a-login",
                created_at="2026-10-01T08:00:00.000000Z",
            )


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
    handover_id: str,
    shift_id: str,
    actor_id: str,
    payload: dict[str, object],
) -> None:
    clock.moment = at
    store.append(
        build_event(
            event_type,
            payload=payload,
            handover_id=handover_id,
            shift_id=shift_id,
            actor_id=actor_id,
            client_at=at,
            work_date=ShiftKey.parse(shift_id).work_date,
        )
    )


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


@pytest.fixture()
def synthetic_day(database: Database, clock: FixedClock, store: EventStore, audit: AuditService) -> None:
    """One Monday of synthetic work: two reception reports, two emergency starts, one forgotten flow."""
    _staff(database)
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


def test_a_regenerated_card_reports_the_whole_covered_day(
    service: DailyCardService, database: Database, synthetic_day: None
) -> None:
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
    }
    assert document["card_date"] == "2026-10-27"
    assert document["generated_at"] == "2026-10-27T04:01:00+00:00"
    assert document["sections"]["discrepancies"][0]["culprit"] is None
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
