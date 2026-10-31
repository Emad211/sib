"""The two shift events that move a shelf: restock (و) and emergency withdrawal (اضطراری).

S01 §3 E-06 records a mid-shift entry — from the store, or from a vendor with an invoice reference —
and adds to `proj_stock` (H-01 `R`). E-08 is the double-signed opening of the sealed store key: the
shelf drops at once because the goods already left, and the storekeeper confirms the record
afterwards, so the confirmation changes a status and never the projection twice (H-01 `U`).

Stock is where a quiet bug costs the clinic, so both kinds run the real `ShiftEventService`, the real
`EventStore` and the real `ProjectionEngine`, and the rebuild has to land on the same figures as the
live projection. Every value here is the synthetic reference catalog — no production database and no
patient data is read (ADR-006).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, SPARE, Seeded, create_staff
from synthetic_catalog import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.events import EventType
from shift.domain.permissions import permissions_for
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, SubmissionResult
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventRuleError, ShiftEventService

SHIFT = ShiftKey(date(2026, 10, 28), 1)
CLIENT_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
# The synthetic catalog: paracetamol on the reception shelf, saline in the CPR room (S03 §1).
ITEM = "para_500_amp"
LOCATION = "shelf_reception"
ELSEWHERE = "cpr_room"
SHELF_BEFORE = 10
RESTOCKED = EventType.STOCK_SHELF_RESTOCKED.value
WITHDRAWN = EventType.STOCK_EMERGENCY_WITHDRAWAL.value
APPROVED = EventType.EVENT_APPROVED.value


@dataclass(frozen=True, slots=True)
class Desk:
    """The real service on one synthetic shelf, with the people the two rules name."""

    database: Database
    projections: ProjectionEngine
    service: ShiftEventService
    shift: ShiftKey
    nurse: Principal
    storekeeper: Principal
    accountant: Principal


def _principal(person: Seeded) -> Principal:
    """The session every route would hand the service for that person."""
    return Principal(
        staff_id=person.staff_id,
        full_name=person.full_name,
        roles=person.roles,
        permissions=permissions_for(person.roles),
        device_id=person.device_id,
        device_label="گوشی آزمایشی",
        session_hash=f"session-{person.staff_id}",
        csrf_token=f"csrf-{person.staff_id}",
        expires_at=datetime(2026, 10, 28, 23, 0, tzinfo=UTC),
    )


@pytest.fixture()
def desk(database: Database, store: EventStore, projections: ProjectionEngine) -> Iterator[Desk]:
    """One shift with a settled shelf: a weekly count seeds the figure the moves start from."""
    seed_catalog(database)
    for person in (ROLES["nurse"], ROLES["storekeeper"], ROLES["accountant"], SPARE):
        create_staff(
            database,
            staff_id=person.staff_id,
            full_name=person.full_name,
            mobile=person.mobile,
            roles=person.roles,
        )
    store.append(
        build_event(
            EventType.STOCK_WEEKLY_COUNT.value,
            payload={"item_id": ITEM, "location_id": LOCATION, "counted": SHELF_BEFORE},
            shift_id=SHIFT.text,
        )
    )
    projections.sync()
    yield Desk(
        database=database,
        projections=projections,
        service=ShiftEventService(
            database,
            store,
            projections,
            CatalogService(database),
            SettingsService(database),
        ),
        shift=SHIFT,
        nurse=_principal(ROLES["nurse"]),
        storekeeper=_principal(ROLES["storekeeper"]),
        accountant=_principal(ROLES["accountant"]),
    )


def _record(
    desk: Desk,
    principal: Principal,
    event_type: str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    ref_event_id: str | None = None,
) -> SubmissionResult:
    """Record one phone event the way the route does: identity from the session, never the body.

    `event_id` is the id the phone minted into its outbox; a retry re-sends the same one, which is
    the only way the server can tell a replay from a second event (ADR-016).
    """
    minted = event_id or new_ulid(CLIENT_AT)
    submission = EventSubmission(
        type=event_type,
        payload=payload,
        actor_id=principal.staff_id,
        device_id=principal.device_id,
        client_at=CLIENT_AT,
        client_event_id=minted,
        idempotency_key=minted,
        shift_id=desk.shift.text,
        shift_work_date=desk.shift.work_date,
        ref_event_id=ref_event_id,
    )
    return desk.service.record(submission, principal=principal, shift=desk.shift)


def _restock(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": ITEM,
        "location_id": LOCATION,
        "quantity": 5,
        "source": "store",
        "reference": None,
    }
    payload.update(overrides)
    return payload


def _withdrawal(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": ITEM,
        "location_id": LOCATION,
        "quantity": 2,
        "reason": "قفسه خالی شد",
        "second_person_id": SPARE.staff_id,
    }
    payload.update(overrides)
    return payload


def _shelf(desk: Desk, item_id: str = ITEM, location_id: str = LOCATION) -> int | None:
    """The projected quantity of one shelf, or None when no event ever moved it."""
    with desk.database.read() as connection:
        rows = projections_repo.stock_rows(connection)
    return next(
        (row["qty"] for row in rows if (row["item_id"], row["location_id"]) == (item_id, location_id)),
        None,
    )


def _written(desk: Desk) -> int:
    with desk.database.read() as connection:
        return events_repo.count_events(connection)


# --- ورودی وسط شیفت، E-06 ----------------------------------------------------------------


def test_a_store_restock_adds_the_entry_to_the_reception_shelf(desk: Desk) -> None:
    result = _record(desk, desk.nurse, RESTOCKED, _restock())

    assert result.created
    assert _shelf(desk) == SHELF_BEFORE + 5


def test_a_vendor_restock_without_its_reference_is_refused(desk: Desk) -> None:
    """E-06: a vendor box nobody can match against an invoice is not a record of an entry."""
    with pytest.raises(ShiftEventRuleError):
        _record(desk, desk.nurse, RESTOCKED, _restock(source="vendor"))

    assert _shelf(desk) == SHELF_BEFORE
    assert _written(desk) == 1, "a refused event writes nothing"


def test_a_vendor_restock_with_its_invoice_reference_is_accepted(desk: Desk) -> None:
    _record(desk, desk.nurse, RESTOCKED, _restock(source="vendor", reference="فاکتور ۱۲۳۴"))

    assert _shelf(desk) == SHELF_BEFORE + 5


def test_a_restock_of_an_item_that_lives_in_another_location_is_refused(desk: Desk) -> None:
    """One item sits in one place (S01 §4), so a stock move can never name a shelf that lacks it."""
    with pytest.raises(ShiftEventRuleError):
        _record(desk, desk.nurse, RESTOCKED, _restock(location_id=ELSEWHERE))

    assert _shelf(desk) == SHELF_BEFORE
    assert _shelf(desk, location_id=ELSEWHERE) is None


def test_a_replayed_restock_writes_and_moves_the_shelf_once(desk: Desk) -> None:
    """The outbox retries after a lost answer (ADR-016); the shelf must not grow twice."""
    first = _record(desk, desk.nurse, RESTOCKED, _restock())
    replayed = _record(desk, desk.nurse, RESTOCKED, _restock(), event_id=first.event_id)

    assert replayed.created is False
    assert replayed.event_id == first.event_id
    assert _shelf(desk) == SHELF_BEFORE + 5
    assert _written(desk) == 2, "the weekly count and the one restock"


def test_a_stock_move_of_nothing_is_refused(desk: Desk) -> None:
    """Both kinds move a shelf by a quantity, so a zero is a record of no move at all (E-06, E-08)."""
    with pytest.raises(ShiftEventRuleError):
        _record(desk, desk.nurse, RESTOCKED, _restock(quantity=0))
    with pytest.raises(ShiftEventRuleError):
        _record(desk, desk.nurse, WITHDRAWN, _withdrawal(quantity=0))

    assert _shelf(desk) == SHELF_BEFORE
    assert _written(desk) == 1


# --- برداشت اضطراری انبار، E-08 ----------------------------------------------------------


def test_an_emergency_withdrawal_takes_from_the_shelf_at_once(desk: Desk) -> None:
    _record(desk, desk.nurse, WITHDRAWN, _withdrawal())

    assert _shelf(desk) == SHELF_BEFORE - 2


def test_a_second_person_who_is_the_recorder_is_refused(desk: Desk) -> None:
    """E-08: two people open the sealed envelope, so the second name cannot be the first."""
    with pytest.raises(ShiftEventRuleError):
        _record(desk, desk.nurse, WITHDRAWN, _withdrawal(second_person_id=desk.nurse.staff_id))

    assert _shelf(desk) == SHELF_BEFORE
    assert _written(desk) == 1


def test_a_withdrawal_without_a_reason_is_refused(desk: Desk) -> None:
    with pytest.raises(ShiftEventRuleError):
        _record(desk, desk.nurse, WITHDRAWN, _withdrawal(reason="  "))

    assert _shelf(desk) == SHELF_BEFORE
    assert _written(desk) == 1


def test_the_storekeeper_confirms_an_emergency_withdrawal_afterwards(desk: Desk) -> None:
    """The shelf already dropped; the confirmation is the storekeeper's signature on it (H-01 `U`)."""
    recorded = _record(desk, desk.nurse, WITHDRAWN, _withdrawal())

    pending = desk.service.pending_for(desk.storekeeper)
    assert [entry.event_id for entry in pending] == [recorded.event_id]
    entry = pending[0]
    assert (entry.type, entry.code) == (WITHDRAWN, "اض")
    assert entry.actor["id"] == desk.nurse.staff_id
    assert entry.summary["quantity"] == 2

    decision = _record(desk, desk.storekeeper, APPROVED, {}, ref_event_id=recorded.event_id)

    assert decision.created
    assert desk.service.pending_for(desk.storekeeper) == []
    assert _shelf(desk) == SHELF_BEFORE - 2, "the confirmation never moves the shelf a second time"


def test_an_accountant_may_not_decide_an_emergency_withdrawal(desk: Desk) -> None:
    """E-08: only the storekeeper closes the envelope, so a refusal leaves the record waiting."""
    recorded = _record(desk, desk.nurse, WITHDRAWN, _withdrawal())

    with pytest.raises(ShiftEventRuleError):
        _record(desk, desk.accountant, APPROVED, {}, ref_event_id=recorded.event_id)

    assert [entry.event_id for entry in desk.service.pending_for(desk.storekeeper)] == [recorded.event_id]


def test_a_replayed_withdrawal_writes_and_moves_the_shelf_once(desk: Desk) -> None:
    first = _record(desk, desk.nurse, WITHDRAWN, _withdrawal())
    replayed = _record(desk, desk.nurse, WITHDRAWN, _withdrawal(), event_id=first.event_id)

    assert replayed.created is False
    assert replayed.event_id == first.event_id
    assert _shelf(desk) == SHELF_BEFORE - 2
    assert _written(desk) == 2


# --- the log is the truth ----------------------------------------------------------------


def test_a_rebuild_lands_on_the_same_shelves_as_the_live_projection(desk: Desk) -> None:
    """ADR-010: a stock event is only as good as its replay, so the two runs must agree."""
    _record(desk, desk.nurse, RESTOCKED, _restock(quantity=6))
    _record(desk, desk.nurse, WITHDRAWN, _withdrawal(quantity=3))
    live = _shelf(desk)

    report = desk.projections.rebuild()

    assert report.applied
    assert report.drift == ()
    assert _shelf(desk) == live == SHELF_BEFORE + 6 - 3
