"""Owner decision 9: each person records their own flow's first count (ADR-022 item 5).

The receptionist counts the reception flow (box included) and the nurse the nursing flow, on their own
phone, while off shift and before they hold any custody; whoever counts becomes that flow's custodian
under their own name. The manager's `/manager/baseline` tool stays the fallback, so these tests also
prove the two paths write one and only one first count.

Money and stock are checked through the real handover routes: the next reception flow compares its
blind counts against what the person counted, and a rebuild reproduces the live projection.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed as seed_catalog
from turns import send_receipt_api

from shift.adapters.db import handover_repo
from shift.app import create_app
from shift.config.settings import Settings
from shift.domain.handover_fsm import HandoverFlow
from shift.services.auth import SESSION_COOKIE
from shift.services.handover import OWN_FLOW_ONLY

CASH_OPENING = 1_000_000
RECEPTION = ROLES["reception"].staff_id
SHELF = "shelf_reception"
PARA = "para_500_amp"
DICLO = "diclofenac_75_amp"
CPR = "cpr_room"
TROLLEY = "trolley"
ROUTE = "/api/v1/handover/baseline"


def _settings(root: Path) -> Settings:
    return Settings(
        environment="development",  # plain-HTTP test client: no Secure cookies
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
        accounting_db_path=root / "accounting.db",
    )


@contextmanager
def _running(root: Path) -> Iterator[TestClient]:
    """A running app with the seeded roster **off shift** and the synthetic reference catalog.

    Off shift is the point: owner decision 9 has the first count happen before anyone holds custody
    (ADR-021), so the permission has to work while the shift is not open for that person yet.
    """
    root.mkdir(parents=True, exist_ok=True)
    settings = _settings(root)
    empty_accounting(settings.accounting_db_path)
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key, on_shift=False)
        seed_catalog(client.app.state.database)
        yield client


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    with _running(tmp_path / "app") as running:
        yield running


@dataclass(frozen=True, slots=True)
class Phone:
    """One person signed in on their own paired phone, with the CSRF proof for unsafe verbs."""

    staff_id: str
    headers: dict[str, str]
    token: str


def _phone(client: TestClient, role: str) -> Phone:
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    token = response.cookies.get(SESSION_COOKIE)
    assert token, "the login handshake issued no session"
    return Phone(person.staff_id, {"X-CSRFToken": response.json()["csrf_token"]}, token)


def _as(client: TestClient, phone: Phone) -> dict[str, str]:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, phone.token)
    return phone.headers


def _reception_body(opening_cash: int = CASH_OPENING, para: int = 12, diclo: int = 0) -> dict[str, Any]:
    return {
        "flow": "reception",
        "opening_cash": opening_cash,
        "items": [
            {"item_id": PARA, "location_id": SHELF, "quantity": para},
            {"item_id": DICLO, "location_id": SHELF, "quantity": diclo},
        ],
    }


def _nursing_body() -> dict[str, Any]:
    return {
        "flow": "nursing",
        "opening_cash": None,
        "items": [
            {"item_id": "saline_500_bag", "location_id": CPR, "quantity": 4},
            {"item_id": "oxygen_mask", "location_id": CPR, "quantity": 2},
            {"item_id": "glove_latex_m", "location_id": TROLLEY, "quantity": 500},
            {"item_id": "alcohol_pad", "location_id": TROLLEY, "quantity": 150},
        ],
    }


def _count(
    client: TestClient, phone: Phone, handover_id: str, *, scope: str, counted: int, item_id=None, location_id=None
):
    body: dict[str, Any] = {"scope": scope, "counted": counted}
    if item_id is not None:
        body["item_id"] = item_id
        body["location_id"] = location_id
    return client.post(f"/api/v1/handover/{handover_id}/counts", json=body, headers=_as(client, phone))


def test_the_receptionist_counts_the_box_and_shelf_before_holding_custody(client: TestClient) -> None:
    """Owner decision 9: the person who counts is the custodian, and money and stock start from it."""
    counter = _phone(client, "reception")
    created = client.post(ROUTE, json=_reception_body(), headers=_as(client, counter))
    assert created.status_code == 201, created.text
    assert created.json()["custodian_id"] == counter.staff_id
    assert created.json()["shelves"] == 2
    with client.app.state.database.read() as connection:
        assert handover_repo.has_baseline(connection, HandoverFlow.RECEPTION)
        assert handover_repo.custodian(connection, HandoverFlow.RECEPTION) == counter.staff_id

    # A flow that already has its first count refuses a second one, so nothing money-wise is rewritten.
    again = client.post(ROUTE, json=_reception_body(opening_cash=1, para=1), headers=_as(client, counter))
    assert again.status_code == 409, again.text

    # The next reception flow compares against exactly what this person counted, through the real routes.
    incoming = _phone(client, "multi_role")
    started = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, incoming))
    assert started.status_code == 201, started.text
    handover_id = started.json()["handover_id"]
    assert started.json()["flow"] == "reception"
    assert send_receipt_api(client, _as(client, counter), handover_id).status_code == 200  # the outgoing person
    # The box is counted together: one joint figure from either of the two people.
    cash = client.post(
        f"/api/v1/handover/{handover_id}/joint",
        json={"scope": "cash", "counted": CASH_OPENING},
        headers=_as(client, incoming),
    )
    assert cash.json()["status"] == "matched", cash.text
    advanced = client.post(f"/api/v1/handover/{handover_id}/advance", headers=_as(client, incoming))
    assert advanced.json()["step"] == "shelf"
    counted = _count(client, incoming, handover_id, scope="item", counted=12, item_id=PARA, location_id=SHELF)
    assert counted.json()["status"] == "matched", counted.text
    empty = _count(client, incoming, handover_id, scope="item", counted=0, item_id=DICLO, location_id=SHELF)
    assert empty.json()["status"] == "matched", empty.text
    assert client.app.state.projections.rebuild().drift == ()


def test_the_nurse_counts_the_nursing_flow_and_nobody_counts_the_other_flow(client: TestClient) -> None:
    """Only the role that owns the flow records its first count (owner decision 9)."""
    nurse = _phone(client, "nurse")
    reception = _phone(client, "reception")
    wrong_flow = client.post(ROUTE, json=_nursing_body(), headers=_as(client, reception))
    assert wrong_flow.status_code == 403
    assert wrong_flow.json()["error"]["message"] == OWN_FLOW_ONLY
    assert client.post(ROUTE, json=_reception_body(), headers=_as(client, nurse)).status_code == 403

    created = client.post(ROUTE, json=_nursing_body(), headers=_as(client, nurse))
    assert created.status_code == 201, created.text
    assert created.json()["flow"] == "nursing"

    # The manager keeps the fallback tool; the receptionist does not get it by holding the new permission.
    _as(client, reception)
    page = client.get("/manager/baseline", headers={"Accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 403


def test_the_first_count_covers_every_counted_item_of_the_flow(client: TestClient) -> None:
    """A blind count compares against the baseline, so an item the baseline skips has no reference."""
    counter = _phone(client, "reception")
    body = _reception_body()
    body["items"] = body["items"][:1]
    response = client.post(ROUTE, json=body, headers=_as(client, counter))
    assert response.status_code == 400
    assert "همهٔ اقلام" in response.json()["error"]["message"]


def test_the_home_screen_offers_the_job_only_while_the_flow_has_no_baseline(client: TestClient) -> None:
    """The handover area offers the first count until it exists, and never afterwards."""
    counter = _phone(client, "reception")
    _as(client, counter)
    before = client.get("/", headers={"Accept": "text/html"})
    assert "/handover/baseline" in before.text, "an off-shift receptionist is offered their own first count"
    assert client.get("/handover/baseline", headers={"Accept": "text/html"}).status_code == 200

    assert client.post(ROUTE, json=_reception_body(), headers=counter.headers).status_code == 201
    after = client.get("/", headers={"Accept": "text/html"})
    assert "/handover/baseline" not in after.text, "the tile disappears once the flow has its baseline"
    page = client.get("/handover/baseline", headers={"Accept": "text/html"})
    assert page.status_code == 200
    assert "شمارش اولیه‌ای نمانده است" in page.text
