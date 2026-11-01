"""C1: the manager's live decisions page — early leave, unanswered discrepancies, faults to assign."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import events_repo
from shift.app import create_app
from shift.common.clock import to_tehran
from shift.common.ids import new_ulid
from shift.config.settings import Settings
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.shift_clock import covering
from shift.services.auth import SESSION_COOKIE
from shift.services.event_store import NewEvent

PAGE = "/manager/decisions"
API = "/api/v1/manager/decisions"
OUTGOING = ROLES["multi_role"].staff_id


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / "app"
    settings = Settings(
        environment="development",
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
        accounting_db_path=empty_accounting(tmp_path / "accounting.db"),
    )
    with TestClient(create_app(settings)) as running:
        seed_roster(running.app.state.database, pepper=running.app.state.secret_key, on_shift=False)
        seed_catalog(running.app.state.database)
        running.app.state.handover.record_baseline(
            flow=HandoverFlow.RECEPTION,
            custodian_id=OUTGOING,
            manager_id=ROLES["manager"].staff_id,
            opening_cash=1_000_000,
            items=[
                {"item_id": "para_500_amp", "location_id": "shelf_reception", "quantity": 0},
                {"item_id": "diclofenac_75_amp", "location_id": "shelf_reception", "quantity": 0},
            ],
        )
        yield running


def _as(client: TestClient, role: str) -> dict[str, str]:
    person = ROLES[role]
    client.cookies.clear()
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    client.cookies.set(SESSION_COOKIE, response.cookies.get(SESSION_COOKIE))
    return {"X-CSRFToken": response.json()["csrf_token"]}


def _record(
    client: TestClient, event_type: str, payload: dict, *, ref: str | None = None, handover: str | None = None
) -> str:
    moment = datetime.now(UTC)
    shift = covering(to_tehran(moment))
    result = client.app.state.events.append(
        NewEvent(
            client_event_id=new_ulid(moment),
            type=event_type,
            payload=payload,
            actor_id=ROLES["reception"].staff_id,
            device_id=ROLES["reception"].device_id,
            work_date=shift.work_date,
            client_at=moment,
            shift_id=shift.text,
            handover_id=handover,
            ref_event_id=ref,
            idempotency_key=new_ulid(moment),
        )
    )
    return result.event.event_id


def test_the_manager_confirms_an_early_leave_from_the_page_once(client: TestClient) -> None:
    headers = _as(client, "reception")
    started = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers).json()
    handover_id = started["handover_id"]
    assert client.post(f"/api/v1/handover/{handover_id}/early-leave", headers=headers).status_code == 200

    manager = _as(client, "manager")
    page = client.get(PAGE)
    assert page.status_code == 200 and f'data-early-leave="{handover_id}"' in page.text

    assert client.post(f"{API}/early-leave/{handover_id}", headers=manager).status_code == 200
    state = client.app.state.handover.state(handover_id)
    assert state.emergency_by == ROLES["manager"].staff_id, "the confirmation names the manager who gave it"
    assert f'data-early-leave="{handover_id}"' not in client.get(PAGE).text
    again = client.post(f"{API}/early-leave/{handover_id}", headers=manager)
    assert again.status_code == 409


def test_a_discrepancy_gets_one_answer_from_a_manager_only(client: TestClient) -> None:
    gap = _record(
        client,
        "discrepancy.recorded",
        {
            "scope": "item",
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "difference": 2,
            "culprit_id": OUTGOING,
        },
    )
    reception = _as(client, "reception")
    assert (
        client.post(f"{API}/review", json={"event_id": gap, "decision": "approved"}, headers=reception).status_code
        == 403
    )

    manager = _as(client, "manager")
    page = client.get(PAGE).text
    assert f'data-event-id="{gap}"' in page and "کسری" in page

    answered = client.post(
        f"{API}/review", json={"event_id": gap, "decision": "rejected", "note": "با انباردار چک شود"}, headers=manager
    )
    assert answered.status_code == 200
    with client.app.state.database.read() as connection:
        review = events_repo.find_by_event_id(connection, answered.json()["event_id"])
    assert review is not None and review.type == "manager.reviewed" and review.ref_event_id == gap
    assert review.payload == {"decision": "rejected", "note": "با انباردار چک شود"}
    assert f'data-event-id="{gap}"' not in client.get(PAGE).text
    assert (
        client.post(f"{API}/review", json={"event_id": gap, "decision": "approved"}, headers=manager).status_code == 409
    )


def test_a_fault_waits_on_the_page_until_it_is_assigned(client: TestClient) -> None:
    fault = _record(client, "equipment.fault_reported", {"device": "ساکشن", "description": "روشن نمی‌شود"})
    _as(client, "manager")
    assert f'data-event-id="{fault}"' in client.get(PAGE).text

    _record(
        client,
        "equipment.fault_assigned",
        {"assignee_id": ROLES["nurse"].staff_id, "due_on": datetime.now(UTC).date().isoformat()},
        ref=fault,
    )
    assert f'data-event-id="{fault}"' not in client.get(PAGE).text
