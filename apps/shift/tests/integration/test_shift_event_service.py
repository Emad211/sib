"""The shift-event service: permissions, idempotency, projections and approvals (SHF-3)."""

from datetime import UTC, date, datetime

import pytest
from auth_fixtures import ROLES, SPARE, seed_roster
from seed_reference_data import seed as seed_catalog

from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.permissions import permissions_for
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, OutboxValidationError
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventRuleError, ShiftEventService

WORK_DATE = date(2026, 10, 28)
CLIENT_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


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
    shift: ShiftKey,
    *,
    ref_event_id: str | None = None,
    key: str | None = None,
) -> EventSubmission:
    event_id = new_ulid(CLIENT_AT)
    return EventSubmission(
        type=event_type,
        payload=payload,
        actor_id=principal.staff_id,
        device_id=principal.device_id,
        client_at=CLIENT_AT,
        client_event_id=event_id,
        shift_id=shift.text,
        ref_event_id=ref_event_id,
        idempotency_key=key or event_id,
        shift_work_date=shift.work_date,
    )


@pytest.fixture
def seeded(database: Database) -> Database:
    seed_roster(database, pepper=b"synthetic-pepper")
    seed_catalog(database)
    return database


def test_a_kind_requires_its_own_permission(seeded: Database) -> None:
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    purchaser = _principal("purchaser")
    submission = _submission(
        "stock.waste_reported",
        {
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 1,
            "waste_kind": "broken",
            "culprit_id": SPARE.staff_id,
            "confirmer_id": ROLES["nurse"].staff_id,
        },
        purchaser,
        shift,
    )

    with pytest.raises(ShiftEventRuleError):
        service.record(submission, principal=purchaser, shift=shift)


def test_a_registered_kind_is_appended_and_projected(seeded: Database) -> None:
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    reception = _principal("reception")
    submission = _submission(
        "stock.waste_reported",
        {
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 2,
            "waste_kind": "broken",
            "culprit_id": SPARE.staff_id,
            "confirmer_id": ROLES["nurse"].staff_id,
        },
        reception,
        shift,
    )

    result = service.record(submission, principal=reception, shift=shift)

    assert result.created is True
    with seeded.read() as connection:
        assert events_repo.count_events(connection) == 1
        rows = {(row["item_id"], row["location_id"]): row["qty"] for row in projections_repo.stock_rows(connection)}
    assert rows[("para_500_amp", "shelf_reception")] == -2


def test_a_replay_writes_once(seeded: Database) -> None:
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    reception = _principal("reception")
    submission = _submission(
        "stock.waste_reported",
        {
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 1,
            "waste_kind": "expired",
            "culprit_id": SPARE.staff_id,
            "confirmer_id": ROLES["nurse"].staff_id,
        },
        reception,
        shift,
        key="replay-key-1",
    )

    first = service.record(submission, principal=reception, shift=shift)
    second = service.record(submission, principal=reception, shift=shift)

    assert (first.created, second.created) == (True, False)
    assert first.seq == second.seq
    with seeded.read() as connection:
        assert events_repo.count_events(connection) == 1


def test_an_approval_needs_the_target_role_once_and_not_the_author(seeded: Database) -> None:
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    reception = _principal("reception")
    accountant = _principal("accountant")
    author = service.record(
        _submission("card.transfer_reported", {"amount": 150_000, "admission_no": "A-1001"}, reception, shift),
        principal=reception,
        shift=shift,
    )
    pending = service.pending_for(accountant)
    assert [entry.event_id for entry in pending] == [author.event_id]

    decision = _submission(
        "event.approved",
        {"note": "تأیید شد"},
        accountant,
        shift,
        ref_event_id=author.event_id,
    )
    service.record(decision, principal=accountant, shift=shift)
    assert service.pending_for(accountant) == []

    # A replay of the same decision is idempotent; a second, different decision is refused.
    replay = service.record(decision, principal=accountant, shift=shift)
    assert replay.created is False
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission("event.approved", {"note": "باز هم"}, accountant, shift, ref_event_id=author.event_id),
            principal=accountant,
            shift=shift,
        )

    # The author cannot decide their own event, and reception holds no approval permission at all.
    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission("event.approved", {}, reception, shift, ref_event_id=author.event_id),
            principal=reception,
            shift=shift,
        )


def test_pending_reads_the_whole_log_and_names_each_records_own_shift(seeded: Database) -> None:
    """F2: an undecided record is the next shift's work too, and it keeps the shift that opened it."""
    shift = ShiftKey(WORK_DATE, 2)
    later = ShiftKey(WORK_DATE, 3)
    service = _service(seeded)
    reception = _principal("reception")
    accountant = _principal("accountant")
    recorded = service.record(
        _submission("card.transfer_reported", {"amount": 10_000, "admission_no": "A-2"}, reception, shift),
        principal=reception,
        shift=shift,
    )

    assert [entry.event_id for entry in service.pending_for(accountant)] == [recorded.event_id]
    assert [entry.shift_id for entry in service.pending_for(accountant)] == [shift.text]

    service.record(
        _submission("event.approved", {}, accountant, later, ref_event_id=recorded.event_id),
        principal=accountant,
        shift=later,
    )
    assert service.pending_for(accountant) == []


def test_handover_keeps_its_online_only_refusal(seeded: Database) -> None:
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    reception = _principal("reception")
    submission = _submission("handover.started", {"opening_cash": 1000}, reception, shift)

    with pytest.raises(OutboxValidationError, match="ADR-016"):
        service.record(submission, principal=reception, shift=shift)


def test_page_context_only_shows_the_kinds_the_person_may_press(seeded: Database) -> None:
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    reception = _principal("reception")

    context = service.page_context(reception, shift)
    types = {kind["type"] for kind in context["kinds"]}

    assert "stock.waste_reported" in types
    assert "vitals.recorded" not in types
    assert context["items"]
    assert context["staff"]
    assert context["pending_url"] == "/api/v1/shift-events/pending"


def test_a_decision_moves_no_projection(seeded: Database) -> None:
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    reception = _principal("reception")
    accountant = _principal("accountant")
    created = service.record(
        _submission("card.transfer_reported", {"amount": 90_000, "admission_no": "A-3"}, reception, shift),
        principal=reception,
        shift=shift,
    )
    with seeded.read() as connection:
        before = projections_repo.cash_rows(connection), projections_repo.stock_rows(connection)

    service.record(
        _submission("event.approved", {}, accountant, shift, ref_event_id=created.event_id),
        principal=accountant,
        shift=shift,
    )

    with seeded.read() as connection:
        after = projections_repo.cash_rows(connection), projections_repo.stock_rows(connection)
    assert after == before


def test_a_kind_rule_refusal_is_an_outbox_refusal(seeded: Database) -> None:
    """The route maps one error type to 422; a rule refusal must not become a 500."""
    shift = ShiftKey(WORK_DATE, 2)
    service = _service(seeded)
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError):
        service.record(
            _submission(
                "stock.waste_reported",
                {
                    "item_id": "para_500_amp",
                    "location_id": "shelf_reception",
                    "quantity": 1,
                    "waste_kind": "broken",
                    "culprit_id": SPARE.staff_id,
                    "confirmer_id": SPARE.staff_id,
                },
                reception,
                shift,
            ),
            principal=reception,
            shift=shift,
        )


def test_a_record_is_judged_by_the_shift_it_happened_in(seeded: Database) -> None:
    """F5: a queue that crossed the shift boundary is checked against the shift the person worked in.

    The follow-up names the reading of its own shift; the shift that is running when the phone finally
    syncs must not turn a legal record into a 422 the phone treats as final.
    """
    shift = ShiftKey(WORK_DATE, 2)
    next_shift = shift.following()
    service = _service(seeded)
    nurse = _principal("nurse")
    reading = service.record(
        _submission(
            "vitals.recorded",
            {"systolic": 120, "diastolic": 80, "sugar": None},
            nurse,
            shift,
        ),
        principal=nurse,
        shift=shift,
    )

    followup = service.record(
        _submission(
            "vitals.followup_opened",
            {"note": "پیگیری فشار"},
            nurse,
            next_shift,
            ref_event_id=reading.event_id,
        ),
        principal=nurse,
        shift=next_shift,
    )

    assert followup.created
    with seeded.read() as connection:
        stored = events_repo.find_by_event_id(connection, followup.event_id)
    assert stored is not None
    assert stored.shift_id == shift.text
