"""The settings API is manager-only and never changes the shift that is already running (R-09)."""

from datetime import date

import pytest
from clocks import FixedClock, inside_shift
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from shift.adapters.db.core import Database
from shift.api.routes_settings import build_settings_router
from shift.domain.policy import ShiftKey
from shift.services.settings import SettingsService

DAY = date(2026, 10, 28)
MORNING = ShiftKey(DAY, 1)
RUNNING = ShiftKey(DAY, 2)
NEXT = ShiftKey(DAY, 3)
ROUTES = [
    ("get", "/api/v1/settings", None),
    ("put", "/api/v1/settings/card_time", {"value": "08:00"}),
    ("get", "/api/v1/settings/card_time/history", None),
]


def api(
    database: Database,
    *,
    shift: ShiftKey | None = RUNNING,
    manager: str | None = "staff-manager",
    clock: FixedClock | None = None,
) -> TestClient:
    service = SettingsService(database, clock=clock)
    app = FastAPI()

    def guard() -> str:
        if manager is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "manager_only")
        return manager

    app.include_router(build_settings_router(guard=guard, settings=lambda: service, running_shift=lambda: shift))
    return TestClient(app)


def test_listing_returns_the_seeded_defaults(database: Database) -> None:
    response = api(database).get("/api/v1/settings")

    assert response.status_code == 200
    body = response.json()
    assert [item["key"] for item in body] == [
        "card_time",
        "takeover_window_minutes",
        "withdrawal_cap_toman",
        "session_ttl_hours",
    ]
    assert [item["value"] for item in body] == ["07:30", "30", "300000", "12"]
    assert {item["effective_from_shift"] for item in body} == {"0001-01-01#1"}
    assert {item["changed_by"] for item in body} == {"migration:m0004"}


def test_a_put_changes_the_next_shift_not_the_running_one(database: Database) -> None:
    client = api(database)

    changed = client.put("/api/v1/settings/takeover_window_minutes", json={"value": "45"})

    assert changed.status_code == 200
    assert changed.json()["value"] == "45"
    assert changed.json()["version"] == 2
    assert changed.json()["effective_from_shift"] == NEXT.text
    assert changed.json()["changed_by"] == "staff-manager"
    running_values = {item["key"]: item["value"] for item in client.get("/api/v1/settings").json()}
    assert running_values["takeover_window_minutes"] == "30"


def test_a_persian_digit_value_is_stored_canonically(database: Database) -> None:
    response = api(database).put("/api/v1/settings/withdrawal_cap_toman", json={"value": "۳۵۰۰۰۰"})

    assert response.status_code == 200
    assert response.json()["value"] == "350000"


def test_a_value_outside_policy_is_a_bad_request(database: Database) -> None:
    response = api(database).put("/api/v1/settings/takeover_window_minutes", json={"value": "999"})

    assert response.status_code == 400
    assert "takeover_window_minutes" in response.json()["detail"]


def test_an_unknown_setting_is_not_found(database: Database) -> None:
    response = api(database).put("/api/v1/settings/owner", json={"value": "x"})

    assert response.status_code == 404


def test_a_missing_value_fails_validation(database: Database) -> None:
    response = api(database).put("/api/v1/settings/card_time", json={})

    assert response.status_code == 422


def test_history_lists_every_version_newest_first(database: Database) -> None:
    client = api(database)
    client.put("/api/v1/settings/card_time", json={"value": "06:00"})

    history = client.get("/api/v1/settings/card_time/history")

    assert history.status_code == 200
    assert [(item["version"], item["value"]) for item in history.json()] == [(2, "06:00"), (1, "07:30")]


def test_without_a_running_shift_a_change_applies_from_the_next_clock_shift(database: Database) -> None:
    client = api(database, shift=None, clock=FixedClock(inside_shift(DAY, 1)))

    changed = client.put("/api/v1/settings/session_ttl_hours", json={"value": "8"})

    assert changed.status_code == 200
    assert changed.json()["effective_from_shift"] == RUNNING.text
    snapshot = {item["key"]: item["value"] for item in client.get("/api/v1/settings").json()}
    assert snapshot["session_ttl_hours"] == "12"


def test_a_change_that_would_rewrite_a_newer_shift_is_a_bad_request(database: Database) -> None:
    api(database, shift=NEXT).put("/api/v1/settings/session_ttl_hours", json={"value": "6"})

    response = api(database, shift=MORNING).put("/api/v1/settings/session_ttl_hours", json={"value": "8"})

    assert response.status_code == 400
    assert "newest version" in response.json()["detail"]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_needs_a_manager(database: Database, method: str, path: str, body: dict[str, str] | None) -> None:
    client = api(database, manager=None)

    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)

    assert response.status_code == 403


def test_a_broken_install_is_reported_as_unavailable(database: Database) -> None:
    with database.write() as connection:
        connection.exec_driver_sql("DELETE FROM setting_versions WHERE setting_key = 'card_time'")

    response = api(database).get("/api/v1/settings")

    assert response.status_code == 503
    assert "card_time" in response.json()["detail"]


def test_the_router_cannot_be_built_without_a_guard(database: Database) -> None:
    service = SettingsService(database)

    with pytest.raises(TypeError):
        build_settings_router(settings=lambda: service, running_shift=lambda: None)  # type: ignore[call-arg]
