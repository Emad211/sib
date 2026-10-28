"""Integration tests for the idempotent events API endpoint and PWA headers."""

from collections.abc import Generator
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.api.routes_events import events_guard
from shift.app import create_app
from shift.common.clock import utc_iso
from shift.common.ids import new_ulid
from shift.config.settings import Settings


@pytest.fixture
def api_client(settings: Settings) -> Generator[TestClient]:
    """Provide a TestClient running with an isolated test database."""
    application = create_app(settings)
    with TestClient(application) as client:
        yield client


def test_idempotent_event_replay_twice_creates_single_record(api_client: TestClient) -> None:
    """Posting the same event twice returns existing seq with HTTP 200 and records once."""
    event_id = new_ulid()
    payload = {
        "event_id": event_id,
        "type": "handover.cash_counted",
        "payload": {"counted": 500_000},
        "actor_id": "staff_test_1",
        "device_id": "dev_test_1",
        "work_date": date.today().isoformat(),
        "client_at": utc_iso(datetime.now(UTC)),
        "shift_id": "shift_test_1",
        "idempotency_key": event_id,
    }

    # First submission
    resp1 = api_client.post("/api/v1/events", json=payload)
    assert resp1.status_code == status.HTTP_200_OK
    data1 = resp1.json()
    assert data1["created"] is True
    assert data1["status"] == "recorded"
    assert data1["event_id"] == event_id
    seq1 = data1["seq"]

    # Replay of the exact same event
    resp2 = api_client.post("/api/v1/events", json=payload)
    assert resp2.status_code == status.HTTP_200_OK
    data2 = resp2.json()
    assert data2["created"] is False
    assert data2["status"] == "replayed"
    assert data2["event_id"] == event_id
    assert data2["seq"] == seq1

    # Check database event count
    db: Database = api_client.app.state.database
    with db.read() as connection:
        assert events_repo.count_events(connection) == 1


def test_event_submission_with_bad_payload_returns_422(api_client: TestClient) -> None:
    """Invalid payload schema raises HTTP 422 Unprocessable Entity."""
    event_id = new_ulid()

    # Bad payload: counted must be an integer, not negative or string
    payload = {
        "event_id": event_id,
        "type": "handover.cash_counted",
        "payload": {"counted": -100},  # Negative money is invalid
        "actor_id": "staff_test_1",
        "device_id": "dev_test_1",
        "work_date": date.today().isoformat(),
        "client_at": utc_iso(datetime.now(UTC)),
        "shift_id": "shift_test_1",
    }
    resp = api_client.post("/api/v1/events", json=payload)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Unknown event type
    payload["type"] = "invalid.event_type"
    resp_unknown = api_client.post("/api/v1/events", json=payload)
    assert resp_unknown.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Missing shift_id when event requires shift_id
    valid_cash = {
        "event_id": new_ulid(),
        "type": "handover.cash_counted",
        "payload": {"counted": 1000},
        "actor_id": "staff_test_1",
        "device_id": "dev_test_1",
        "work_date": date.today().isoformat(),
        "client_at": utc_iso(datetime.now(UTC)),
        "shift_id": None,  # handover.cash_counted requires shift_id
    }
    resp_missing_shift = api_client.post("/api/v1/events", json=valid_cash)
    assert resp_missing_shift.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_auth_guard_blocks_unauthorized_requests(api_client: TestClient) -> None:
    """Overriding the auth guard dependency enforces authorization (401/403)."""

    def failing_guard() -> None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ورود لازم است.",
        )

    api_client.app.dependency_overrides[events_guard] = failing_guard
    try:
        payload = {
            "event_id": new_ulid(),
            "type": "stock.waste_reported",
            "payload": {"item_id": "item_1", "location_id": "loc_1", "quantity": 2},
            "actor_id": "staff_test_1",
            "device_id": "dev_test_1",
            "work_date": date.today().isoformat(),
            "client_at": utc_iso(datetime.now(UTC)),
        }
        resp = api_client.post("/api/v1/events", json=payload)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        assert "ورود" in resp.json()["detail"]
    finally:
        api_client.app.dependency_overrides.pop(events_guard, None)


def test_pwa_webmanifest_and_sw_headers(api_client: TestClient) -> None:
    """Assert PWA manifest, service worker headers, and shell responses."""
    # Manifest
    manifest_resp = api_client.get("/manifest.webmanifest")
    assert manifest_resp.status_code == status.HTTP_200_OK
    assert "application/manifest+json" in manifest_resp.headers["content-type"]
    manifest_data = manifest_resp.json()
    assert manifest_data["name"] == "سیب — شیفت"
    assert manifest_data["short_name"] == "شیفت"
    assert manifest_data["display"] == "standalone"
    assert manifest_data["dir"] == "rtl"

    # Service Worker
    sw_resp = api_client.get("/sw.js")
    assert sw_resp.status_code == status.HTTP_200_OK
    assert "application/javascript" in sw_resp.headers["content-type"]
    assert sw_resp.headers["service-worker-allowed"] == "/"
    assert "no-cache" in sw_resp.headers["cache-control"]

    # Home Shell
    home_resp = api_client.get("/")
    assert home_resp.status_code == status.HTTP_200_OK
    assert "text/html" in home_resp.headers["content-type"]
    assert 'dir="rtl"' in home_resp.text
    assert "manifest.webmanifest" in home_resp.text

    # Offline Fallback Page
    offline_resp = api_client.get("/offline.html")
    assert offline_resp.status_code == status.HTTP_200_OK
    assert "text/html" in offline_resp.headers["content-type"]
    assert "سیب" in offline_resp.text
