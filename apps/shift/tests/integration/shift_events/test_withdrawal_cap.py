"""The withdrawal cap is the shift's running total, and the manager decides an above-cap one (ADR-019).

The owner's decision has two halves. The cap counts every withdrawal of the shift together, by the
shift each one happened in, so small records cannot empty the box without a manager. And an above-cap
withdrawal still reduces expected cash the moment it is recorded — the cash really left — while the
manager approves or rejects it afterwards from their own session; a rejection does not put the money
back, it only records that the manager did not stand behind it.

The real `ShiftEventService` on a migrated synthetic database; every value below is invented.
"""

from datetime import UTC, date, datetime

import pytest
from auth_fixtures import ROLES, Seeded, seed_roster
from clocks import FixedClock, inside_shift
from synthetic_catalog import seed as seed_catalog

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
SHIFT = ShiftKey(WORK_DATE, 2)
NEXT_SHIFT = SHIFT.following()
CLIENT_AT = inside_shift(WORK_DATE, SHIFT.rank)
CAP = 300_000
OPENING_CASH = 1_000_000
RECIPIENT = ROLES["nurse"].staff_id


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
        expires_at=datetime(2026, 10, 30, 23, 0, tzinfo=UTC),
    )


@pytest.fixture()
def seeded(database: Database) -> Database:
    seed_roster(database, pepper=b"synthetic-pepper")
    seed_catalog(database)
    return database


@pytest.fixture()
def service(seeded: Database) -> ShiftEventService:
    """The real service, wired as the app wires it, with a fixed clock inside the morning shift."""
    return ShiftEventService(
        seeded,
        EventStore(seeded),
        ProjectionEngine(seeded),
        CatalogService(seeded),
        SettingsService(seeded),
        clock=FixedClock(CLIENT_AT),
    )


def _withdraw(service: ShiftEventService, amount: int, *, manager_id: str | None = None, shift: ShiftKey = SHIFT):
    """One withdrawal from the reception box, stamped inside `shift` (whatever is running when it lands)."""
    reception = _principal(ROLES["reception"])
    event_id = new_ulid(CLIENT_AT)
    return service.record(
        EventSubmission(
            type="cash.withdrawn",
            payload={
                "amount": amount,
                "recipient_id": RECIPIENT,
                "reason": "خرید فوری",
                "manager_id": manager_id,
            },
            actor_id=reception.staff_id,
            device_id=reception.device_id,
            client_at=inside_shift(shift.work_date, shift.rank),
            client_event_id=event_id,
            shift_id=shift.text,
            idempotency_key=event_id,
            shift_work_date=shift.work_date,
        ),
        principal=reception,
        shift=shift,
    )


def _decide(service: ShiftEventService, manager: Principal, event_id: str, decision: str, shift: ShiftKey = SHIFT):
    """The manager's own decision on one open withdrawal."""
    moment = inside_shift(shift.work_date, shift.rank)
    token = new_ulid(moment)
    return service.record(
        EventSubmission(
            type=decision,
            payload={"note": "بررسی شد"},
            actor_id=manager.staff_id,
            device_id=manager.device_id,
            client_at=moment,
            client_event_id=token,
            shift_id=shift.text,
            ref_event_id=event_id,
            idempotency_key=token,
            shift_work_date=shift.work_date,
        ),
        principal=manager,
        shift=shift,
    )


def _box(database: Database) -> int:
    """The reception box's expected figure; a box no event has touched yet holds zero."""
    with database.read() as connection:
        rows = projections_repo.cash_rows(connection)
    return next((int(row["expected"]) for row in rows if row["cash_box_id"] == "reception"), 0)


def test_the_cap_is_the_running_total_of_the_shift(service: ShiftEventService, database: Database) -> None:
    """Two records under the cap are fine; the third takes the total over and needs a manager."""
    before = _box(database)
    _withdraw(service, 150_000)
    _withdraw(service, 150_000)

    with pytest.raises(ShiftEventRuleError):
        _withdraw(service, 50_000)

    assert _box(database) == before - 300_000


def test_a_withdrawal_that_happened_offline_counts_against_its_own_shift(
    service: ShiftEventService, database: Database
) -> None:
    """A queue that arrives after the shift changed still counts against the shift it happened in."""
    before = _box(database)
    _withdraw(service, 250_000)

    with pytest.raises(ShiftEventRuleError):
        _withdraw(service, 100_000, shift=SHIFT)

    # The same amount in the next shift is that shift's first withdrawal, so it is under the cap there.
    assert _withdraw(service, 100_000, shift=NEXT_SHIFT).created
    assert _box(database) == before - 350_000


def test_an_above_cap_withdrawal_waits_for_the_manager_and_approval_keeps_the_cash_out(
    service: ShiftEventService, database: Database
) -> None:
    """The money left the box when it was recorded; the approval only says the manager stands behind it."""
    manager = _principal(ROLES["manager"])
    before = _box(database)
    above = _withdraw(service, 400_000, manager_id=manager.staff_id)
    assert _box(database) == before - 400_000

    pending = [entry.event_id for entry in service.pending_for(manager)]
    assert above.event_id in pending

    _decide(service, manager, above.event_id, "event.approved")

    assert _box(database) == before - 400_000
    assert [entry.event_id for entry in service.pending_for(manager)] == []


def test_a_rejected_above_cap_withdrawal_keeps_the_cash_out(service: ShiftEventService, database: Database) -> None:
    """A rejection never puts money back: the box keeps what really left and the row stays decided."""
    manager = _principal(ROLES["manager"])
    before = _box(database)
    above = _withdraw(service, 400_000, manager_id=manager.staff_id)

    _decide(service, manager, above.event_id, "event.rejected")

    assert _box(database) == before - 400_000
    assert [entry.event_id for entry in service.pending_for(manager)] == []


def test_below_the_cap_the_recipient_decides_and_above_it_the_manager(service: ShiftEventService) -> None:
    """The owner asks the recipient to confirm a withdrawal; past the cap the manager decides instead."""
    recipient = _principal(ROLES["nurse"])
    manager = _principal(ROLES["manager"])

    below = _withdraw(service, 50_000)
    assert below.event_id in [entry.event_id for entry in service.pending_for(recipient)]

    above = _withdraw(service, 400_000, manager_id=manager.staff_id)

    assert above.event_id not in [entry.event_id for entry in service.pending_for(recipient)]
    assert above.event_id not in [entry.event_id for entry in service.pending_for(recipient)]
    assert above.event_id in [entry.event_id for entry in service.pending_for(manager)]
