"""Security properties of the event log: no mutation SQL, payloads stay data, no offline handover."""

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, seed_roster
from auth_fixtures import login as login_request
from fastapi import status
from fastapi.testclient import TestClient

from event_fixtures import build_event, default_payload
from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.api.deps import running_shift
from shift.common.ids import new_ulid
from shift.domain.policy import ShiftKey
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, OutboxService, OutboxValidationError
from shift.services.projections import ProjectionEngine

RUNNING = ShiftKey(date(2026, 10, 28), 2)


def test_event_repo_contains_no_update_or_delete_statements() -> None:
    source = Path(events_repo.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]

    assert not any("DELETE FROM EVENTS" in value.upper() for value in literals)
    assert not any("UPDATE EVENTS" in value.upper() for value in literals)


def test_payload_is_parameterised_data_not_sql(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    malicious = "x'); DROP TABLE events; --"
    store.append(
        build_event(
            "handover.item_counted",
            payload={"item_id": malicious, "location_id": "loc-1", "counted": 3},
        )
    )

    projections.sync()

    with database.read() as connection:
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar_one()) == 1
        row = connection.exec_driver_sql("SELECT item_id FROM proj_stock").one()
    assert row[0] == malicious


@pytest.mark.parametrize("event_type", ["handover.cash_counted", "handover.count_settled", "discrepancy.recorded"])
def test_the_outbox_refuses_every_handover_event(event_type: str, store: EventStore, database: Database) -> None:
    """ADR-016: the handover runs live; the offline queue can never write its events."""
    submission = EventSubmission(
        type=event_type,
        payload=default_payload(event_type),
        actor_id="staff-1",
        device_id="device-1",
        client_at=datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(OutboxValidationError, match="ADR-016"):
        OutboxService(store).record_event(submission)

    with database.read() as connection:
        assert events_repo.count_events(connection) == 0


def test_the_events_route_refuses_a_handover_event(client: TestClient) -> None:
    """The generic route inherits the refusal: no handover event enters the log from a phone."""
    seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
    client.app.dependency_overrides[running_shift] = lambda: RUNNING
    person = ROLES["nurse"]
    session = login_request(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert session.status_code == status.HTTP_200_OK
    event_id = new_ulid()
    body = {
        "client_event_id": event_id,
        "idempotency_key": event_id,
        "type": "handover.cash_counted",
        "payload": {"counted": 1_000_000},
        "client_at": datetime(2026, 10, 28, 10, 0, tzinfo=UTC).isoformat(),
    }

    response = client.post("/api/v1/events", json=body, headers={"X-CSRFToken": session.json()["csrf_token"]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "ADR-016" in response.json()["error"]["message"]
    database: Database = client.app.state.database
    with database.read() as connection:
        assert events_repo.count_events(connection) == 0
