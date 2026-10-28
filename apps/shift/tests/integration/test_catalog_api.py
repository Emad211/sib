"""The catalog API is manager-only and versions every change by shift (R-08, R-09)."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from shift.adapters.db.core import Database
from shift.api.routes_catalog import build_catalog_router
from shift.domain.policy import ShiftKey
from shift.services.catalog import CatalogService

DAY = date(2026, 10, 28)
RUNNING = ShiftKey(DAY, 1)
NEXT = ShiftKey(DAY, 2)
LOCATION = {"name": "قفسهٔ پذیرش"}
ITEM = {
    "name": "پاراستامول ۵۰۰ میلی‌گرم",
    "kind": "counted",
    "location_id": "shelf_reception",
    "unit": "آمپول",
    "minimum": 10,
    "acc_code": "10001",
    "stock_factor": 1,
}
ROUTES = [
    ("get", "/api/v1/catalog/roles"),
    ("get", "/api/v1/catalog/locations"),
    ("put", "/api/v1/catalog/locations/shelf_reception"),
    ("get", "/api/v1/catalog/items"),
    ("put", "/api/v1/catalog/items/para_500_amp"),
    ("get", "/api/v1/catalog/staff/staff-1/roles"),
    ("put", "/api/v1/catalog/staff/staff-1/roles/nurse"),
]


def api(database: Database, *, shift: ShiftKey | None = RUNNING, manager: str | None = "staff-manager") -> TestClient:
    service = CatalogService(database)
    app = FastAPI()

    def guard() -> SimpleNamespace:
        if manager is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "manager_only")
        return SimpleNamespace(staff_id=manager)

    app.include_router(build_catalog_router(guard=guard, catalog=lambda: service, running_shift=lambda: shift))
    return TestClient(app)


def test_a_location_is_created_for_the_next_shift(database: Database) -> None:
    client = api(database)

    created = client.put("/api/v1/catalog/locations/shelf_reception", json=LOCATION)

    assert created.status_code == 200
    assert created.json() == {
        "location_id": "shelf_reception",
        "name": "قفسهٔ پذیرش",
        "retired": False,
        "version": 1,
        "effective_from_shift": NEXT.text,
    }
    assert client.get("/api/v1/catalog/locations").json() == []
    assert api(database, shift=NEXT).get("/api/v1/catalog/locations").json()[0]["location_id"] == "shelf_reception"


def test_a_counted_item_without_an_accounting_code_is_a_bad_request(database: Database) -> None:
    client = api(database)
    client.put("/api/v1/catalog/locations/shelf_reception", json=LOCATION)

    response = client.put("/api/v1/catalog/items/para_500_amp", json={**ITEM, "acc_code": None})

    assert response.status_code == 400
    assert "acc_code" in response.json()["detail"]


def test_an_unknown_item_kind_fails_validation(database: Database) -> None:
    response = api(database).put("/api/v1/catalog/items/para_500_amp", json={**ITEM, "kind": "weight"})

    assert response.status_code == 422


def test_items_can_be_listed_for_one_location(database: Database) -> None:
    writer = api(database)
    writer.put("/api/v1/catalog/locations/shelf_reception", json=LOCATION)
    writer.put("/api/v1/catalog/items/para_500_amp", json=ITEM)
    reader = api(database, shift=NEXT)

    assert [item["item_id"] for item in reader.get("/api/v1/catalog/items").json()] == ["para_500_amp"]
    assert reader.get("/api/v1/catalog/items", params={"location_id": "cpr_room"}).json() == []
    assert reader.get("/api/v1/catalog/items", params={"location_id": "bad id"}).status_code == 400


def test_the_role_vocabulary_is_exposed(database: Database) -> None:
    response = api(database).get("/api/v1/catalog/roles")

    assert response.status_code == 200
    assert response.json() == [
        "reception",
        "nurse",
        "storekeeper",
        "purchaser",
        "accountant",
        "shift_doctor",
        "manager",
    ]


def test_a_role_grant_is_listed_for_the_next_shift(database: Database) -> None:
    client = api(database)

    granted = client.put("/api/v1/catalog/staff/staff-1/roles/nurse", json={"granted": True})

    assert granted.status_code == 200
    assert granted.json() == {
        "role": "nurse",
        "granted": True,
        "version": 1,
        "effective_from_shift": NEXT.text,
    }
    assert client.get("/api/v1/catalog/staff/staff-1/roles").json() == []
    assert api(database, shift=NEXT).get("/api/v1/catalog/staff/staff-1/roles").json() == [granted.json()]


def test_a_retired_location_is_hidden_unless_asked_for(database: Database) -> None:
    writer = api(database)
    writer.put("/api/v1/catalog/locations/shelf_reception", json=LOCATION)
    retired_client = api(database, shift=NEXT)
    retired_client.put("/api/v1/catalog/locations/shelf_reception", json={**LOCATION, "retired": True})
    reader = api(database, shift=ShiftKey(DAY, 3))

    assert reader.get("/api/v1/catalog/locations").json() == []
    assert reader.get("/api/v1/catalog/locations", params={"include_retired": True}).json()[0]["retired"] is True


def test_a_location_id_that_is_not_a_slug_is_a_bad_request(database: Database) -> None:
    response = api(database).put("/api/v1/catalog/locations/Shelf Reception", json=LOCATION)

    assert response.status_code == 400
    assert "location_id" in response.json()["detail"]


def test_a_staff_id_that_is_not_usable_is_a_bad_request(database: Database) -> None:
    client = api(database)

    listed = client.get("/api/v1/catalog/staff/staff 1/roles")
    granted = client.put("/api/v1/catalog/staff/staff 1/roles/nurse", json={"granted": True})

    assert listed.status_code == 400
    assert granted.status_code == 400
    assert "staff_id" in granted.json()["detail"]


def test_an_unknown_role_in_the_path_fails_validation(database: Database) -> None:
    response = api(database).put("/api/v1/catalog/staff/staff-1/roles/owner", json={"granted": True})

    assert response.status_code == 422


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_needs_a_manager(database: Database, method: str, path: str) -> None:
    client = api(database, manager=None)

    response = (
        getattr(client, method)(path, json={"granted": True}) if method == "put" else getattr(client, method)(path)
    )

    assert response.status_code == 403


def test_the_router_cannot_be_built_without_a_guard(database: Database) -> None:
    service = CatalogService(database)

    with pytest.raises(TypeError):
        build_catalog_router(catalog=lambda: service, running_shift=lambda: None)  # type: ignore[call-arg]
