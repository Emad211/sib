"""Integration tests for the authenticated idempotent events endpoint (review B1)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, create_device, create_staff, seed_roster
from auth_fixtures import login as login_request
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from seed_reference_data import seed as seed_catalog

from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.api.deps import running_shift
from shift.api.errors import install_error_handlers
from shift.api.routes_events import router as events_router
from shift.common.clock import parse_iso, to_tehran
from shift.common.ids import new_ulid
from shift.domain.shift_clock import covering
from shift.services.outbox import business_date_for

# One fresh instant for the whole module: F5 holds a record older than 72 hours for the manager, so a
# pinned instant would turn this suite red three days after it was written. The shift is the one that
# instant falls in, because the log takes it from the record's own occurrence (E-14), not from a header.
CLIENT_AT = datetime.now(UTC) - timedelta(minutes=5)
RUNNING = covering(to_tehran(CLIENT_AT))
MATRIX_STAFF = {"staff_id": "staff-no-roles", "mobile": "09120000010", "device_id": "device-no-roles"}


def _event_payload(**overrides: Any) -> dict[str, Any]:
    """Build a valid current-shift stock event, applying caller overrides."""
    event_id = overrides.pop("client_event_id", None) or new_ulid()
    payload: dict[str, Any] = {
        "client_event_id": event_id,
        "idempotency_key": event_id,
        "type": "stock.waste_reported",
        "payload": {
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 5,
            "waste_kind": "broken",
            "culprit_id": "staff-reception",
            "confirmer_id": "staff-spare",
        },
        # Fresh, not pinned: the F5 hold keeps a record older than 72 hours for the manager, so a fixed
        # instant would turn this suite red three days after it was written.
        "client_at": CLIENT_AT.isoformat(),
    }
    payload.update(overrides)
    return payload


def _login(client: TestClient, role: str = "nurse") -> dict[str, Any]:
    """Log one seeded role in and return the session payload with its CSRF token."""
    person = ROLES[role]
    response = login_request(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


def _headers(session: dict[str, Any]) -> dict[str, str]:
    """The session-bound CSRF header every unsafe route demands."""
    return {"X-CSRFToken": session["csrf_token"]}


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    """The real app with one staff member and one phone per role, on a fixed shift."""
    seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
    seed_catalog(client.app.state.database)
    client.app.dependency_overrides[running_shift] = lambda: RUNNING
    return client


def test_an_app_without_auth_wiring_still_rejects_event_ingestion() -> None:
    """An app that never created the auth service must not accept anonymous writes."""
    application = FastAPI()
    install_error_handlers(application)
    application.include_router(events_router)
    with TestClient(application) as client:
        client.cookies.set("shift_csrf", "t" * 43)
        response = client.post("/api/v1/events", json=_event_payload(), headers={"X-CSRFToken": "t" * 43})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["code"] == "not_authenticated"


def test_anonymous_request_is_rejected_and_records_nothing(seeded: TestClient) -> None:
    """No cookie means 401, never an event attributed to nobody."""
    response = seeded.post("/api/v1/events", json=_event_payload())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    database: Database = seeded.app.state.database
    with database.read() as connection:
        assert events_repo.count_events(connection) == 0


def test_the_session_identity_is_what_gets_recorded(seeded: TestClient) -> None:
    """actor/device/shift in the JSON body are ignored; the session wins."""
    session = _login(seeded, "nurse")
    body = _event_payload(actor_id="staff_forged", device_id="device_forged", shift_id="shift_forged")

    response = seeded.post("/api/v1/events", json=body, headers=_headers(session))

    assert response.status_code == status.HTTP_200_OK
    database: Database = seeded.app.state.database
    with database.read() as connection:
        event = events_repo.find_by_event_id(connection, response.json()["event_id"])
    assert event is not None
    assert event.actor_id == ROLES["nurse"].staff_id
    assert event.device_id == ROLES["nurse"].device_id
    assert event.shift_id == RUNNING.text
    assert event.work_date == RUNNING.work_date.isoformat()


def test_the_double_submit_cookie_is_accepted_for_offline_replays(seeded: TestClient, csrf_headers: Any) -> None:
    """The PWA shell has only the double-submit token when it replays its outbox."""
    _login(seeded, "reception")

    response = seeded.post("/api/v1/events", json=_event_payload(), headers=csrf_headers(seeded))

    assert response.status_code == status.HTTP_200_OK


def test_a_missing_or_mismatched_csrf_token_is_rejected(seeded: TestClient) -> None:
    """A session cookie alone is not proof the request came from the shell."""
    _login(seeded)
    body = _event_payload()

    missing = seeded.post("/api/v1/events", json=body)
    mismatched = seeded.post("/api/v1/events", json=body, headers={"X-CSRFToken": "x" * 43})

    assert missing.status_code == status.HTTP_403_FORBIDDEN
    assert mismatched.status_code == status.HTTP_403_FORBIDDEN
    assert missing.json()["error"]["code"] == "csrf_failed"
    database: Database = seeded.app.state.database
    with database.read() as connection:
        assert events_repo.count_events(connection) == 0


def test_a_staff_member_without_the_events_permission_gets_403(seeded: TestClient) -> None:
    """The guard is the permission matrix, not merely a live session."""
    database: Database = seeded.app.state.database
    create_staff(
        database,
        staff_id=MATRIX_STAFF["staff_id"],
        full_name="بدون نقش",
        mobile=MATRIX_STAFF["mobile"],
        roles=(),
        pepper=seeded.app.state.secret_key,
    )
    create_device(database, device_id=MATRIX_STAFF["device_id"], staff_id=MATRIX_STAFF["staff_id"])
    response = login_request(seeded, mobile=MATRIX_STAFF["mobile"], device_id=MATRIX_STAFF["device_id"], pin=PIN)
    assert response.status_code == status.HTTP_200_OK

    denied = seeded.post(
        "/api/v1/events", json=_event_payload(), headers={"X-CSRFToken": response.json()["csrf_token"]}
    )

    assert denied.status_code == status.HTTP_403_FORBIDDEN
    with database.read() as connection:
        assert events_repo.count_events(connection) == 0


@pytest.mark.parametrize("role", ["reception", "nurse", "storekeeper", "shift_doctor", "manager", "multi_role"])
def test_a_role_with_the_kind_permission_may_append_its_event(seeded: TestClient, role: str) -> None:
    """Event ingestion is shift work, but each kind also names the roles that may record it (R-07)."""
    session = _login(seeded, role)

    response = seeded.post("/api/v1/events", json=_event_payload(), headers=_headers(session))

    assert response.status_code == status.HTTP_200_OK, response.text


@pytest.mark.parametrize("role", ["accountant", "purchaser"])
def test_a_role_without_the_kind_permission_is_refused(seeded: TestClient, role: str) -> None:
    """Holding `events.append` is not enough: the waste kind belongs to the shelf's roles."""
    session = _login(seeded, role)

    response = seeded.post("/api/v1/events", json=_event_payload(), headers=_headers(session))

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "اجازهٔ ثبت" in response.json()["error"]["message"]


def test_idempotency_key_wins_over_a_new_client_event_id(seeded: TestClient) -> None:
    """Replays dedupe on idempotency_key even when the client mints a new ULID."""
    session = _login(seeded)
    key = new_ulid()
    first_id = new_ulid()
    second_id = new_ulid()

    first = seeded.post(
        "/api/v1/events", json=_event_payload(client_event_id=first_id, idempotency_key=key), headers=_headers(session)
    )
    second = seeded.post(
        "/api/v1/events", json=_event_payload(client_event_id=second_id, idempotency_key=key), headers=_headers(session)
    )

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["event_id"] == first_id
    assert second.json()["seq"] == first.json()["seq"]
    database: Database = seeded.app.state.database
    with database.read() as connection:
        assert events_repo.count_events(connection) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "handover.cash_counted", "payload": {"counted": -1}},
        {"type": "invalid.event_type"},
        {"payload": {"item_id": "i", "location_id": "l", "quantity": 1, "extra": 2}},
        {"client_at": "2026-10-28T12:00:00"},
        {"client_event_id": "not-a-ulid"},
        {"payload": {"item_id": "i" * 129, "location_id": "l", "quantity": 1}},
        {"work_date": "not-a-date"},
        {"unexpected_field": True},
    ],
)
def test_invalid_submissions_are_rejected(seeded: TestClient, overrides: dict[str, Any]) -> None:
    """Each invalid shape is a 422, not a 500 or a silently coerced row."""
    session = _login(seeded)

    response = seeded.post("/api/v1/events", json=_event_payload(**overrides), headers=_headers(session))

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    database: Database = seeded.app.state.database
    with database.read() as connection:
        assert events_repo.count_events(connection) == 0


def test_a_wrong_work_date_hint_never_loses_the_record(seeded: TestClient) -> None:
    """F5: the phone's date is a hint; the instant it happened decides which shift the record joins."""
    session = _login(seeded)
    body = _event_payload(work_date="2020-01-01")

    response = seeded.post("/api/v1/events", json=body, headers=_headers(session))

    assert response.status_code == status.HTTP_200_OK
    with seeded.app.state.database.read() as connection:
        stored = events_repo.find_by_event_id(connection, response.json()["event_id"])
    assert stored is not None
    assert stored.work_date == business_date_for(parse_iso(body["client_at"])).isoformat()


def test_the_authenticated_shift_date_is_accepted(seeded: TestClient) -> None:
    """The PWA's Tehran business date equals the running shift's work date."""
    session = _login(seeded)

    response = seeded.post(
        "/api/v1/events",
        json=_event_payload(work_date=RUNNING.work_date.isoformat()),
        headers=_headers(session),
    )

    assert response.status_code == status.HTTP_200_OK


def test_ingest_updates_projections_and_replay_does_not_double_apply(seeded: TestClient) -> None:
    """The only production write path keeps proj_stock in step with the event log."""
    session = _login(seeded)
    body = _event_payload(
        payload={
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 5,
            "waste_kind": "broken",
            "culprit_id": "staff-reception",
            "confirmer_id": "staff-spare",
        }
    )

    first = seeded.post("/api/v1/events", json=body, headers=_headers(session))
    replay = seeded.post("/api/v1/events", json=body, headers=_headers(session))

    assert first.status_code == status.HTTP_200_OK
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["created"] is False
    database: Database = seeded.app.state.database
    with database.read() as connection:
        rows = projections_repo.stock_rows(connection)
    assert rows == [{"item_id": "para_500_amp", "location_id": "shelf_reception", "qty": -5}]


def test_oversized_body_is_rejected_before_parsing(seeded: TestClient) -> None:
    """The ingest endpoint caps request bodies at 64 KiB."""
    session = _login(seeded)
    headers = {**_headers(session), "Content-Type": "application/json"}

    response = seeded.post("/api/v1/events", content=b"x" * (70 * 1024), headers=headers)

    assert response.status_code == status.HTTP_413_CONTENT_TOO_LARGE
