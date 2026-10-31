"""The manager's setup screens (SHF-7.1): the initial count and the items with their accounting codes.

The pages are the phone-side of `HandoverService.record_baseline` and of the versioned catalog, with
the clinic's real install list behind them (69 counted items that have no code until the owner types
one, Q-17). These tests sign in the real way and post the same JSON the pages' own scripts post.
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from seed_reference_data import seed as seed_clinic

from shift.adapters.db import projections_repo
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.config.settings import Settings
from shift.domain.shift_clock import covering
from shift.services.catalog import CatalogService, ItemKind

BASELINE_URL = "/api/v1/manager/baseline"


@pytest.fixture()
def clinic_client(settings: Settings) -> Iterator[TestClient]:
    """An app on the clinic's real install list (names only; every value is synthetic data)."""
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_clinic(client.app.state.database)
        yield client


def _sign_in(client: TestClient, role: str) -> dict[str, str]:
    person = ROLES[role]
    client.cookies.clear()
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return {"X-CSRFToken": response.json()["csrf_token"]}


def _counted(client: TestClient, location_id: str) -> list[Any]:
    shift = covering(datetime.now(TEHRAN))
    return [item for item in CatalogService(client.app.state.database).items(shift, location_id=location_id)]


def _reception_items(client: TestClient) -> list[dict[str, Any]]:
    return [
        {"item_id": item.item_id, "location_id": item.location_id, "quantity": 5}
        for item in _counted(client, "shelf_reception")
        if item.kind is ItemKind.COUNTED
    ]


def test_the_baseline_page_offers_the_box_and_the_flow_shelves(clinic_client: TestClient) -> None:
    _sign_in(clinic_client, "manager")
    page = clinic_client.get("/manager/baseline")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "شمارش اولیه" in page.text and "پذیرش" in page.text and "پرستاری" in page.text
    assert "موجودی صندوق" in page.text, "reception owns the cash box (H-12)"
    assert clinic_client.app.state.database is not None

    _sign_in(clinic_client, "reception")
    assert clinic_client.get("/manager/baseline").status_code == 403


def test_the_manager_records_the_opening_count_once(clinic_client: TestClient) -> None:
    """The API writes one baseline through the service; the second one is refused and changes nothing."""
    items = _reception_items(clinic_client)
    assert len(items) == 49, "the reception shelf holds the 49 counted items of the clinic list"
    body = {
        "flow": "reception",
        "custodian_id": ROLES["reception"].staff_id,
        "opening_cash": 750_000,
        "items": items,
    }

    headers = _sign_in(clinic_client, "manager")
    created = clinic_client.post(BASELINE_URL, json=body, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["shelves"] == 49
    assert created.json()["opening_cash"] == 750_000
    with clinic_client.app.state.database.read() as connection:
        cash = projections_repo.cash_rows(connection)
    assert cash == [{"cash_box_id": "reception", "expected": 750_000, "counted": 750_000, "variance": 0}]

    headers = _sign_in(clinic_client, "manager")
    second = clinic_client.post(BASELINE_URL, json=body, headers=headers)
    assert second.status_code == 400
    assert "قبلاً ثبت شده" in second.text

    page = clinic_client.get("/manager/baseline")
    assert page.text.count("شمارش اولیهٔ این جریان ثبت شده است") == 1


def test_the_opening_count_refuses_a_foreign_item_and_an_empty_box(clinic_client: TestClient) -> None:
    headers = _sign_in(clinic_client, "manager")
    cpr_item = next(item for item in _counted(clinic_client, "cpr_room") if item.kind is ItemKind.COUNTED)
    foreign = {
        "flow": "reception",
        "custodian_id": ROLES["reception"].staff_id,
        "opening_cash": 1_000,
        "items": [{"item_id": cpr_item.item_id, "location_id": cpr_item.location_id, "quantity": 1}],
    }
    assert clinic_client.post(BASELINE_URL, json=foreign, headers=headers).status_code == 400

    headers = _sign_in(clinic_client, "manager")
    no_cash = {**foreign, "items": _reception_items(clinic_client)[:1], "opening_cash": None}
    assert clinic_client.post(BASELINE_URL, json=no_cash, headers=headers).status_code == 400


def test_the_items_page_lists_the_missing_codes_first(clinic_client: TestClient) -> None:
    """The clinic list starts with 69 counted items and no code; the page says so and asks for them."""
    _sign_in(clinic_client, "manager")
    page = clinic_client.get("/manager/items")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "69 قلم شمارشی" in page.text
    assert "69 قلم بی‌کد مانده" in page.text, "every counted item waits for the owner's code (Q-17)"
    assert "کد کالا" in page.text

    _sign_in(clinic_client, "reception")
    assert clinic_client.get("/manager/items").status_code == 403
