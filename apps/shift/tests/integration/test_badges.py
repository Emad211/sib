"""The number on a home tile: how many open jobs wait for this very person (the badges on «کارهای من»)."""

from collections.abc import Iterator

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from synthetic_catalog import seed as seed_catalog

from shift.app import create_app
from shift.config.settings import Settings

BADGES = "/api/v1/badges"
ACTION = "/api/v1/purchase/action"
ITEM = "para_500_amp"
LOCATION = "shelf_reception"


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as app:
        seed_roster(app.app.state.database, pepper=app.app.state.secret_key)
        seed_catalog(app.app.state.database)
        yield app


def _sign_in(client: TestClient, role: str) -> str:
    client.cookies.clear()
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])


def _act(client: TestClient, csrf: str, action: str, data: dict) -> dict:
    return client.post(ACTION, json={"action": action, "data": data}, headers={"X-CSRFToken": csrf}).json()


def test_nobody_signed_in_gets_no_badges(client: TestClient) -> None:
    assert client.get(BADGES).status_code == 401


def test_a_quiet_clinic_shows_no_number(client: TestClient) -> None:
    _sign_in(client, "manager")
    answer = client.get(BADGES)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"badges": {}}
    assert answer.headers["cache-control"] == "no-store"


def test_an_order_waiting_for_the_manager_lights_the_purchase_tile_and_a_decision_clears_it(client: TestClient) -> None:
    store_csrf = _sign_in(client, "storekeeper")
    drafted = _act(
        client,
        store_csrf,
        "order.draft",
        {"lines": [{"item_id": ITEM, "location_id": LOCATION, "quantity": 4}], "note": "قفسه خالی شد"},
    )
    assert drafted["status"] == "recorded", drafted
    assert client.get(BADGES).json()["badges"].get("/purchase") is None, "the author has nothing to decide"

    manager_csrf = _sign_in(client, "manager")
    assert client.get(BADGES).json()["badges"]["/purchase"] == 1

    decided = _act(
        client, manager_csrf, "order.decide", {"order_id": drafted["order_id"], "decision": "approved", "note": None}
    )
    assert decided["status"] == "recorded", decided
    assert "/purchase" not in client.get(BADGES).json()["badges"]


def test_a_person_gets_a_number_only_for_tiles_they_hold(client: TestClient) -> None:
    store_csrf = _sign_in(client, "storekeeper")
    _act(
        client,
        store_csrf,
        "order.draft",
        {"lines": [{"item_id": ITEM, "location_id": LOCATION, "quantity": 4}], "note": None},
    )
    _sign_in(client, "nurse")
    assert client.get(BADGES).json()["badges"] == {}, "the nurse has no purchase decision and no manager page"
