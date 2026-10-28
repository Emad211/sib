"""Integration tests for the authenticated idempotent events endpoint (review B1)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.api.principal import Principal, require_principal
from shift.app import create_app
from shift.common.clock import utc_iso
from shift.common.ids import new_ulid
from shift.config.settings import Settings
from shift.services.outbox import business_date_for


def _event_payload(**overrides: Any) -> dict[str, Any]:
    """Build a valid current-shift stock event, applying caller overrides."""
    event_id = overrides.pop("client_event_id", None) or new_ulid()
    payload: dict[str, Any] = {
        "client_event_id": event_id,
        "idempotency_key": event_id,
        "type": "stock.waste_reported",
        "payload": {"item_id": "bandage_roll", "location_id": "emergency_cabinet", "quantity": 5},
        "client_at": utc_iso(datetime.now(UTC)),
    }
    payload.update(overrides)
    return payload


def test_events_endpoint_fails_closed_without_a_guard(client: TestClient) -> None:
    """An unwired auth dependency must never accept anonymous writes."""
    response = client.post("/api/v1/events", json=_event_payload())

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    db: Database = client.app.state.database
    with db.read() as connection:
        assert events_repo.count_events(connection) == 0


def test_anonymous_request_is_rejected_by_the_configured_guard(client_factory: Any) -> None:
    """Once wired, the real guard's 401 propagates instead of becoming a no-op."""

    def deny(_request: object) -> None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ورود لازم است.")

    with client_factory(guard=deny) as client:
        response = client.post("/api/v1/events", json=_event_payload())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "ورود" in response.json()["detail"]


def test_guard_returning_none_is_treated_as_anonymous(client_factory: Any) -> None:
    """A guard that returns no session gets 401, not a 500 or an anonymous write."""
    with client_factory(guard=lambda _request: None) as client:
        response = client.post("/api/v1/events", json=_event_payload())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_guard_must_resolve_a_principal(client_factory: Any) -> None:
    """A guard that returns an unrelated object fails closed with 500."""
    with client_factory(guard=lambda _request: object()) as client:
        response = client.post("/api/v1/events", json=_event_payload())

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "principal" in response.json()["detail"]


def test_session_object_is_coerced_into_a_principal(client_factory: Any, csrf_headers: Any) -> None:
    """The auth lane may return a session dataclass; staff_id becomes actor_id."""

    class Session:
        staff_id = "staff_session_1"
        device_id = "device_session_1"
        shift_id = "shift_session_1"
        work_date = None

    with client_factory(guard=lambda _request: Session()) as client:
        response = client.post("/api/v1/events", json=_event_payload(), headers=csrf_headers(client))
        assert response.status_code == status.HTTP_200_OK
        db: Database = client.app.state.database
        with db.read() as connection:
            event = events_repo.find_by_idempotency_key(connection, response.json()["event_id"])
            assert event is not None
            assert event.actor_id == Session.staff_id
            assert event.device_id == Session.device_id
            assert event.shift_id == Session.shift_id


def test_async_guard_is_awaited(client_factory: Any, csrf_headers: Any) -> None:
    """The auth lane's require() may be async; the dependency must await it."""

    async def guard(_request: object) -> Principal:
        return Principal(actor_id="staff_async", device_id="device_async")

    with client_factory(guard=guard) as client:
        response = client.post("/api/v1/events", json=_event_payload(), headers=csrf_headers(client))
        assert response.status_code == status.HTTP_200_OK
        db: Database = client.app.state.database
        with db.read() as connection:
            event = events_repo.find_by_idempotency_key(connection, response.json()["event_id"])
            assert event is not None
            assert event.actor_id == "staff_async"


def test_dependency_override_with_a_session_object_is_coerced(settings: Settings, csrf_headers: Any) -> None:
    """The coordinator can wire require() as a one-line dependency override."""

    class Session:
        staff_id = "staff_override"
        device_id = "device_override"
        shift_id = None
        work_date = None

    application = create_app(settings)
    application.dependency_overrides[require_principal] = lambda: Session()
    with TestClient(application) as client:
        response = client.post("/api/v1/events", json=_event_payload(), headers=csrf_headers(client))
        assert response.status_code == status.HTTP_200_OK
        db: Database = client.app.state.database
        with db.read() as connection:
            event = events_repo.find_by_idempotency_key(connection, response.json()["event_id"])
            assert event is not None
            assert event.actor_id == Session.staff_id
            assert event.device_id == Session.device_id


def test_body_asserted_identity_is_ignored(client_factory: Any, csrf_headers: Any) -> None:
    """actor_id/device_id/shift_id in the JSON body never reach the event log."""
    principal = Principal(actor_id="staff_real", device_id="device_real", shift_id="shift_real")
    body = _event_payload(
        actor_id="staff_forged",
        device_id="device_forged",
        shift_id="shift_forged",
    )

    with client_factory(principal=principal) as client:
        response = client.post("/api/v1/events", json=body, headers=csrf_headers(client))
        assert response.status_code == status.HTTP_200_OK
        db: Database = client.app.state.database
        with db.read() as connection:
            event = events_repo.find_by_idempotency_key(connection, response.json()["event_id"])
            assert event is not None
            assert event.actor_id == "staff_real"
            assert event.device_id == "device_real"
            assert event.shift_id == "shift_real"


def test_csrf_token_is_required_for_the_cookie_authenticated_post(client_factory: Any, csrf_headers: Any) -> None:
    """A valid principal without a matching double-submit token is rejected."""
    principal = Principal(actor_id="staff_real", device_id="device_real")
    body = _event_payload()

    with client_factory(principal=principal) as client:
        missing = client.post("/api/v1/events", json=body)
        assert missing.status_code == status.HTTP_403_FORBIDDEN

        headers = csrf_headers(client)
        mismatched = client.post("/api/v1/events", json=body, headers={"X-CSRFToken": "x" * 43})
        assert mismatched.status_code == status.HTTP_403_FORBIDDEN

        accepted = client.post("/api/v1/events", json=body, headers=headers)
        assert accepted.status_code == status.HTTP_200_OK
        db: Database = client.app.state.database
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1


def test_idempotency_key_wins_over_a_new_client_event_id(client_factory: Any, csrf_headers: Any) -> None:
    """Replays dedupe on idempotency_key even when the client mints a new ULID."""
    principal = Principal(actor_id="staff_real", device_id="device_real")
    key = new_ulid()
    first_id = new_ulid()
    second_id = new_ulid()

    with client_factory(principal=principal) as client:
        headers = csrf_headers(client)
        first = client.post(
            "/api/v1/events", json=_event_payload(client_event_id=first_id, idempotency_key=key), headers=headers
        )
        second = client.post(
            "/api/v1/events", json=_event_payload(client_event_id=second_id, idempotency_key=key), headers=headers
        )
        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert first.json()["created"] is True
        assert second.json()["created"] is False
        assert second.json()["event_id"] == first_id
        assert second.json()["seq"] == first.json()["seq"]

        db: Database = client.app.state.database
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1


@pytest.mark.parametrize(
    ("overrides", "shift_id"),
    [
        ({"type": "handover.cash_counted", "payload": {"counted": -1}}, "shift_guard_1"),
        ({"type": "invalid.event_type"}, "shift_guard_1"),
        ({"type": "handover.cash_counted", "payload": {"counted": 1000}}, None),
        ({"payload": {"item_id": "i", "location_id": "l", "quantity": 1, "extra": 2}}, "shift_guard_1"),
        ({"client_at": "2026-10-28T12:00:00"}, "shift_guard_1"),
        ({"client_event_id": "not-a-ulid"}, "shift_guard_1"),
        ({"payload": {"item_id": "i" * 129, "location_id": "l", "quantity": 1}}, "shift_guard_1"),
        ({"work_date": "not-a-date"}, "shift_guard_1"),
        ({"unexpected_field": True}, "shift_guard_1"),
    ],
)
def test_invalid_submissions_are_rejected(
    client_factory: Any, csrf_headers: Any, overrides: dict[str, Any], shift_id: str | None
) -> None:
    """Each invalid shape is a 422, not a 500 or a silently coerced row."""
    principal = Principal(actor_id="staff_real", device_id="device_real", shift_id=shift_id)
    with client_factory(principal=principal) as client:
        response = client.post("/api/v1/events", json=_event_payload(**overrides), headers=csrf_headers(client))
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        db: Database = client.app.state.database
        with db.read() as connection:
            assert events_repo.count_events(connection) == 0


def test_night_shift_work_date_is_derived_from_tehran_time(client_factory: Any, csrf_headers: Any) -> None:
    """01:00 Tehran belongs to the previous business date, not the UTC date."""
    assert business_date_for(datetime.fromisoformat("2026-10-29T01:00:00+03:30")) == date(2026, 10, 28)
    principal = Principal(actor_id="staff_real", device_id="device_real")

    with client_factory(principal=principal) as client:
        headers = csrf_headers(client)
        response = client.post(
            "/api/v1/events",
            json=_event_payload(client_at="2026-10-29T01:00:00+03:30"),
            headers=headers,
        )
        assert response.status_code == status.HTTP_200_OK
        db: Database = client.app.state.database
        with db.read() as connection:
            event = events_repo.find_by_idempotency_key(connection, response.json()["event_id"])
            assert event is not None
            assert event.work_date == "2026-10-28"


def test_backdated_work_date_is_rejected(client_factory: Any, csrf_headers: Any) -> None:
    """A client-supplied work_date must agree with the shift business date."""
    principal = Principal(actor_id="staff_real", device_id="device_real")
    with client_factory(principal=principal) as client:
        response = client.post(
            "/api/v1/events",
            json=_event_payload(client_at="2026-10-28T12:00:00+03:30", work_date="2020-01-01"),
            headers=csrf_headers(client),
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "work_date" in response.json()["detail"]


def test_principal_work_date_is_authoritative(client_factory: Any, csrf_headers: Any) -> None:
    """When the session carries the shift date it wins and the body hint must match."""
    principal = Principal(actor_id="staff_real", device_id="device_real", work_date=date(2026, 10, 28))
    with client_factory(principal=principal) as client:
        headers = csrf_headers(client)
        rejected = client.post(
            "/api/v1/events",
            json=_event_payload(client_at="2026-10-28T12:00:00+03:30", work_date="2026-10-01"),
            headers=headers,
        )
        accepted = client.post(
            "/api/v1/events",
            json=_event_payload(client_at="2026-10-28T12:00:00+03:30", work_date="2026-10-28"),
            headers=headers,
        )
        assert rejected.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert accepted.status_code == status.HTTP_200_OK
        db: Database = client.app.state.database
        with db.read() as connection:
            event = events_repo.find_by_idempotency_key(connection, accepted.json()["event_id"])
            assert event is not None
            assert event.work_date == "2026-10-28"


def test_ingest_updates_projections_and_replay_does_not_double_apply(client_factory: Any, csrf_headers: Any) -> None:
    """The only production write path keeps proj_stock in step with the event log."""
    principal = Principal(actor_id="staff_real", device_id="device_real")
    body = _event_payload(payload={"item_id": "bandage_roll", "location_id": "emergency_cabinet", "quantity": 5})

    with client_factory(principal=principal) as client:
        headers = csrf_headers(client)
        first = client.post("/api/v1/events", json=body, headers=headers)
        assert first.status_code == status.HTTP_200_OK
        replay = client.post("/api/v1/events", json=body, headers=headers)
        assert replay.status_code == status.HTTP_200_OK
        assert replay.json()["created"] is False

        db: Database = client.app.state.database
        with db.read() as connection:
            rows = projections_repo.stock_rows(connection)
            assert rows == [{"item_id": "bandage_roll", "location_id": "emergency_cabinet", "qty": -5}]


def test_oversized_body_is_rejected_before_parsing(client_factory: Any, csrf_headers: Any) -> None:
    """The ingest endpoint caps request bodies at 64 KiB."""
    principal = Principal(actor_id="staff_real", device_id="device_real")
    with client_factory(principal=principal) as client:
        headers = {**csrf_headers(client), "Content-Type": "application/json"}
        response = client.post("/api/v1/events", content=b"x" * (70 * 1024), headers=headers)

    assert response.status_code == status.HTTP_413_CONTENT_TOO_LARGE
