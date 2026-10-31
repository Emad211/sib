"""Moving SHF-3 events reach the handover references and survive a rebuild (H-01, H-02)."""

from datetime import UTC, date, datetime

import pytest
from auth_fixtures import ROLES, SPARE, seed_roster
from seed_reference_data import seed as seed_catalog

from event_fixtures import build_event
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
from shift.services.shift_events import ShiftEventService

WORK_DATE = date(2026, 10, 28)
CLIENT_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
OPENING = 1_000_000
CAP = 300_000


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


def _submission(
    event_type: str,
    payload: dict,
    principal: Principal,
    shift: ShiftKey,
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
        idempotency_key=event_id,
        shift_work_date=shift.work_date,
    )


@pytest.mark.parametrize(
    ("withdrawal", "wasted", "restocked", "emergency"),
    [(0, 0, 0, 0), (CAP, 20, 20, 20), (150_000, 2, 5, 1), (1, 1, 0, 0)],
)
def test_moving_events_change_the_next_reference_and_rebuild_equal_live(
    withdrawal: int,
    wasted: int,
    restocked: int,
    emergency: int,
    database: Database,
) -> None:
    seed_roster(database, pepper=b"synthetic-pepper")
    seed_catalog(database)
    shift = ShiftKey(WORK_DATE, 2)
    store = EventStore(database)
    projections = ProjectionEngine(database)
    service = ShiftEventService(
        database,
        store,
        projections,
        CatalogService(database),
        SettingsService(database),
    )
    reception = _principal("reception")
    nurse = _principal("nurse")
    store_keeper = _principal("storekeeper")

    store.append(
        build_event(
            "handover.started",
            payload={"opening_cash": OPENING},
            shift_id=shift.text,
            client_at=CLIENT_AT,
        )
    )
    projections.sync()
    if withdrawal:
        service.record(
            _submission(
                "cash.withdrawn",
                {
                    "amount": withdrawal,
                    "recipient_id": nurse.staff_id,
                    "reason": "خرید فوری",
                    "manager_id": None,
                },
                reception,
                shift,
            ),
            principal=reception,
            shift=shift,
        )
    if wasted:
        service.record(
            _submission(
                "stock.waste_reported",
                {
                    "item_id": "para_500_amp",
                    "location_id": "shelf_reception",
                    "quantity": wasted,
                    "waste_kind": "broken",
                    "culprit_id": SPARE.staff_id,
                    "confirmer_id": nurse.staff_id,
                },
                reception,
                shift,
            ),
            principal=reception,
            shift=shift,
        )
    if restocked:
        service.record(
            _submission(
                "stock.shelf_restocked",
                {
                    "item_id": "para_500_amp",
                    "location_id": "shelf_reception",
                    "quantity": restocked,
                    "source": "store",
                    "reference": None,
                },
                nurse,
                shift,
            ),
            principal=nurse,
            shift=shift,
        )
    if emergency:
        service.record(
            _submission(
                "stock.emergency_withdrawal",
                {
                    "item_id": "para_500_amp",
                    "location_id": "shelf_reception",
                    "quantity": emergency,
                    "reason": "قفسه خالی",
                    "second_person_id": nurse.staff_id,
                },
                store_keeper,
                shift,
            ),
            principal=store_keeper,
            shift=shift,
        )

    with database.read() as connection:
        cash = projections_repo.cash_rows(connection)[0]
        stock = {(row["item_id"], row["location_id"]): row["qty"] for row in projections_repo.stock_rows(connection)}
    assert cash["expected"] == OPENING - withdrawal
    assert stock.get(("para_500_amp", "shelf_reception"), 0) == restocked - wasted - emergency

    report = projections.rebuild(dry_run=True)
    assert report.drift == ()
