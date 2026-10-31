"""Route × role matrix: every route is guarded, listed, and answers its recorded status.

`tests/data/route_matrix.json` is the spec, written by hand: the guard each route must
declare and the status each role gets. The meta-test fails when a route is added without
a row, when a row outlives its route, when the declared guard drifts from the row, or
when a guarded route lets a role without the permission through.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, SPARE, seed_roster
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from seed_reference_data import seed as seed_catalog

from shift.api.deps import declared_guards
from shift.api.errors import describe_refusal
from shift.app import create_app
from shift.config.settings import Settings
from shift.domain.permissions import Permission, permissions_for
from shift.services.auth import SESSION_COOKIE

MATRIX_PATH = Path(__file__).resolve().parents[1] / "data" / "route_matrix.json"
PUBLIC = "public"
AUTHENTICATED = "authenticated"
PERMISSION = "permission:"
ROLE_NAMES = (
    "anon",
    "reception",
    "nurse",
    "storekeeper",
    "purchaser",
    "accountant",
    "shift_doctor",
    "manager",
    "multi_role",
)
PATH_VALUES = {
    "staff_id": SPARE.staff_id,
    "device_id": SPARE.device_id,
    "item_id": "para_500_amp",
    "location_id": "shelf_reception",
    "setting": "session_ttl_hours",
    "role": "nurse",
    "campaign_id": "cmp-matrix",
    # A day the matrix app never generated a card for, so the manager gets the 404 page.
    "card_date": "2026-10-29",
    # An id no flow ever used: every guarded handover route answers 404 for a permitted role.
    "handover_id": "01J00000000000000000000000",
    # An id no record was ever held under: the two held-record routes answer 404 for the manager.
    "held_event_id": "01J00000000000000000000000",
}
_ITEM_BODY = {
    "name": "پاراستامول ۵۰۰ میلی‌گرم",
    "kind": "counted",
    "location_id": "shelf_reception",
    "unit": "آمپول",
    "minimum": 10,
    "acc_code": "10001",
    "stock_factor": 1,
    "retired": False,
}
BODIES = {
    "POST /api/v1/auth/login": {},
    "POST /api/v1/devices/enroll": {},
    "POST /api/v1/devices/enrollment-tokens": {"staff_id": SPARE.staff_id},
    "POST /api/v1/staff/{staff_id}/pin-reset": {"pin": "739251"},
    "POST /api/v1/events": {
        "client_event_id": "01J00000000000000000000000",
        "idempotency_key": "01J00000000000000000000000",
        "type": "stock.waste_reported",
        "payload": {
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 5,
            "waste_kind": "broken",
            "culprit_id": "staff-reception",
            "confirmer_id": "staff-spare",
        },
        "client_at": "2026-10-28T10:00:00Z",
    },
    "PUT /api/v1/catalog/items/{item_id}": _ITEM_BODY,
    "PUT /api/v1/catalog/locations/{location_id}": {"name": "قفسهٔ پذیرش", "retired": False},
    "PUT /api/v1/catalog/staff/{staff_id}/roles/{role}": {"granted": True},
    "PUT /api/v1/settings/{setting}": {"value": "8"},
    "PUT /api/v1/campaigns/{campaign_id}": {
        "name": "کمپین آزمایشی",
        "service": "پذیرش",
        "amount_toman": 10_000,
        "starts_on": "2026-10-01",
        "ends_on": "2026-12-31",
    },
    # A body the flow vocabulary cannot accept, so probing the start cell never opens a real flow
    # (a second reception flow of the same shift would answer 409 instead of the recorded status).
    "POST /api/v1/handover/start": {"flow": "not-a-flow"},
    "POST /api/v1/handover/{handover_id}/checklists": {
        "item_id": "store_lock_ok",
        "ok": True,
        "reading": 6.5,
    },
    "POST /api/v1/handover/{handover_id}/counts": {
        "scope": "item",
        "item_id": "para_500_amp",
        "location_id": "shelf_reception",
        "counted": 1,
    },
    "POST /api/v1/handover/{handover_id}/joint": {
        "scope": "item",
        "item_id": "para_500_amp",
        "location_id": "shelf_reception",
        "counted": 1,
    },
    "POST /api/v1/handover/{handover_id}/parcels": {"parcel_id": "parcel-01", "packages": 1},
    "POST /api/v1/handover/{handover_id}/emergency-start": {"manager_id": SPARE.staff_id},
    # No such action: every permitted role gets the same deterministic refusal, and nothing is written.
    "POST /api/v1/purchase/action": {"action": "unknown.action", "data": {}},
}
PAGES = {"GET /login", "GET /enroll", "GET /admin/devices", "GET /manager/campaigns", "GET /events"}


def _rows() -> dict[str, dict[str, object]]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _api_routes(app) -> Iterator[tuple[str, str, frozenset[str], object]]:
    """Every API route as (path, name, methods, dependant), whatever FastAPI wraps it in."""
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for context in contexts():
                yield context.path, context.name, frozenset(context.methods), context.dependant
        elif isinstance(route, APIRoute):
            yield route.path, route.name, frozenset(route.methods), route.dependant


def _cells(app) -> list[tuple[str, str, str]]:
    """(matrix key, method, concrete url) for every method of every route."""
    cells = []
    for path, name, methods, _ in _api_routes(app):
        url = path
        for placeholder, value in PATH_VALUES.items():
            url = url.replace(f"{{{placeholder}}}", value)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            cells.append((f"{method} {path} [{name}]", method, url))
    return sorted(cells)


def _status_for(client: TestClient, role: str, key: str, method: str, url: str) -> int:
    """One cell: act as `role` (a fresh session) and return the raw status."""
    client.cookies.clear()
    headers: dict[str, str] = {}
    if role != "anon":
        person = ROLES[role]
        issued = client.app.state.auth.login(mobile=person.mobile, pin=PIN, device_id=person.device_id)
        client.cookies.set(SESSION_COOKIE, issued.token)
        headers["X-CSRFToken"] = issued.csrf_token
    if method == "POST" and key.startswith("POST /api/v1/auth/login"):
        client.get("/login")
        headers["X-CSRFToken"] = client.cookies.get("shift_csrf", "")
    extra = {"Accept": "text/html"} if key.split(" [")[0] in PAGES else {}
    response = client.request(
        method,
        url,
        json=BODIES.get(key.split(" [")[0]),
        headers={**headers, **extra},
        follow_redirects=False,
    )
    return response.status_code


@pytest.fixture(scope="module")
def matrix_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """One app for the whole matrix: building and probing it per test would be wasteful."""
    root = tmp_path_factory.mktemp("matrix")
    settings = Settings(
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
    )
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_catalog(client.app.state.database)
        yield client


@pytest.fixture(scope="module")
def observed(matrix_client: TestClient) -> dict[str, dict[str, int]]:
    """Every route × role cell, recorded once from the running app."""
    return {
        key: {role: _status_for(matrix_client, role, key, method, url) for role in ROLE_NAMES}
        for key, method, url in _cells(matrix_client.app)
    }


def test_every_route_is_listed_with_the_guard_it_declares(matrix_client: TestClient) -> None:
    rows = _rows()
    listed = {key for key in rows}
    live = {key for key, _, _ in _cells(matrix_client.app)}
    declared = {
        f"{method} {path} [{name}]": sorted(declared_guards(dependant))
        for path, name, methods, dependant in _api_routes(matrix_client.app)
        for method in sorted(methods - {"HEAD", "OPTIONS"})
    }

    assert not live - listed, f"routes missing from route_matrix.json: {sorted(live - listed)}"
    assert not listed - live, f"route_matrix.json rows with no route: {sorted(listed - live)}"
    for key, guards in declared.items():
        expected = rows[key]["guard"]
        actual = guards[0] if guards else PUBLIC
        assert len(guards) <= 1, f"{key} declares more than one guard: {guards}"
        assert actual == expected, f"{key}: guard drift ({actual!r} vs {expected!r})"


def test_role_outcomes_match_the_matrix(observed) -> None:
    rows = _rows()

    assert {key: set(cells) for key, cells in observed.items()} == {key: set(rows[key]["roles"]) for key in rows}
    diffs = [
        f"{key} [{role}]: expected {rows[key]['roles'][role]}, got {status}"
        for key, cells in observed.items()
        for role, status in cells.items()
        if rows[key]["roles"][role] != status
    ]
    assert not diffs, "access control changed:\n" + "\n".join(diffs)


def test_a_permission_guard_matches_the_domain_matrix(observed) -> None:
    """A role that lacks the permission must never get a success status."""
    for key, cells in observed.items():
        guard = _rows()[key]["guard"]
        if not guard.startswith(PERMISSION):
            continue
        permission = Permission(guard.removeprefix(PERMISSION))
        for role, status in cells.items():
            if role == "anon":
                continue
            if permission not in permissions_for(ROLES[role].roles):
                assert status in {401, 403}, f"{key}: {role} lacks {permission.value} but got {status}"


def test_only_these_routes_are_public() -> None:
    public = {key for key, row in _rows().items() if row["guard"] == PUBLIC}

    assert public == {
        "GET /healthz [healthz]",
        "GET /login [login_page]",
        "GET /enroll [enroll_page]",
        "GET / [get_home]",
        "GET /offline.html [get_offline]",
        "GET /manifest.webmanifest [get_manifest]",
        "GET /sw.js [get_service_worker]",
        "POST /api/v1/auth/login [login]",
        "POST /api/v1/devices/enroll [enroll]",
    }, "a new public route is an access-control decision and must be reviewed here"


def test_the_matrix_covers_every_role_and_names_them_once() -> None:
    rows = _rows()

    assert len(ROLE_NAMES) == len(set(ROLE_NAMES))
    for key, row in rows.items():
        assert set(row["roles"]) == set(ROLE_NAMES), key
        assert row["guard"] == PUBLIC or row["guard"] == AUTHENTICATED or row["guard"].startswith(PERMISSION)


def test_unknown_paths_use_the_same_error_envelope(matrix_client: TestClient) -> None:
    api = matrix_client.get("/api/v1/does-not-exist")
    page = matrix_client.get("/does-not-exist", headers={"Accept": "text/html"})

    assert api.status_code == 404
    assert api.json()["error"]["code"] == "not_found"
    assert page.status_code == 404
    assert "text/html" in page.headers["content-type"]


def test_an_unmapped_refusal_fails_closed() -> None:
    status, code, _message, details = describe_refusal(RuntimeError("boom"))

    assert (status, code, details) == (500, "internal_error", None)
