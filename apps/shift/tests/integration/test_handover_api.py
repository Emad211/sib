"""The handover HTTP skin: one test per behaviour that can cost the clinic money or break access.

Every test drives the real app (lifespan, auth, CSRF, the service and its event log) on a synthetic
database, the synthetic reference catalog and a synthetic accounting copy. The manager's baseline
record is the only seed: a flow never starts without a custodian, and the baseline is what gives the
box its opening figure and the shelf its first counted items (H-01, H-02, H-09).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from make_synthetic_acc_db import build
from seed_reference_data import seed as seed_catalog
from synthetic_accounting import empty_accounting

from shift.app import create_app
from shift.config.settings import Settings
from shift.domain.handover_fsm import HandoverFlow
from shift.services.handover import NO_ACCOUNTING

OUTGOING = "staff-outgoing"  # the custodian the manager records; never a device of their own
MANAGER = ROLES["manager"].staff_id
RECEPTION = ROLES["reception"].staff_id
CASH_OPENING = 1_000_000
SHELF = "shelf_reception"
PARA = "para_500_amp"
DICLO = "diclofenac_75_amp"
NO_CSRF = {"X-CSRFToken": "not-the-token"}


def _settings(root: Path, accounting: Path | None) -> Settings:
    return Settings(
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
        accounting_db_path=accounting,
    )


@contextmanager
def _client(root: Path, *, accounting: Path | None) -> Iterator[TestClient]:
    """A running app on a fresh database, the seeded roster and the reference catalog."""
    with TestClient(create_app(_settings(root, accounting))) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_catalog(client.app.state.database)
        yield client


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """The everyday app: a readable accounting copy with no invoices in it yet."""
    with _client(tmp_path / "app", accounting=empty_accounting(tmp_path / "accounting.db")) as running:
        yield running


def _session(client: TestClient, role: str = "reception") -> dict[str, str]:
    """Log `role` in on their paired phone and return the CSRF header for unsafe verbs."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return {"X-CSRFToken": response.json()["csrf_token"]}


def _baseline(
    client: TestClient,
    *,
    custodian: str = OUTGOING,
    opening_cash: int | None = CASH_OPENING,
    items: list[dict[str, Any]] | None = None,
) -> None:
    """The manager's baseline record: the custodian, the box's opening figure and the shelf's count."""
    client.app.state.handover.record_baseline(
        flow=HandoverFlow.RECEPTION,
        custodian_id=custodian,
        manager_id=MANAGER,
        opening_cash=opening_cash,
        items=(
            [
                {"item_id": PARA, "location_id": SHELF, "quantity": 0},
                {"item_id": DICLO, "location_id": SHELF, "quantity": 0},
            ]
            if items is None
            else items
        ),
    )


def _start(client: TestClient, headers: dict[str, str], flow: str = "reception") -> dict[str, Any]:
    response = client.post("/api/v1/handover/start", json={"flow": flow}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _count(
    client: TestClient,
    headers: dict[str, str],
    handover_id: str,
    *,
    scope: str,
    counted: int,
    item_id: str | None = None,
    location_id: str | None = None,
):
    body: dict[str, Any] = {"scope": scope, "counted": counted}
    if item_id is not None:
        body["item_id"] = item_id
        body["location_id"] = location_id
    return client.post(f"/api/v1/handover/{handover_id}/counts", json=body, headers=headers)


def _item(
    client: TestClient, headers: dict[str, str], handover_id: str, item_id: str, counted: int, location: str = SHELF
):
    return _count(client, headers, handover_id, scope="item", counted=counted, item_id=item_id, location_id=location)


def _advance(client: TestClient, headers: dict[str, str], handover_id: str):
    return client.post(f"/api/v1/handover/{handover_id}/advance", headers=headers)


def _sign(client: TestClient, headers: dict[str, str], handover_id: str, signer: str):
    return client.post(f"/api/v1/handover/{handover_id}/sign", json={"signer": signer}, headers=headers)


def _settle_cash(client: TestClient, headers: dict[str, str], handover_id: str) -> None:
    """Two blind counts on one figure settle the box without a joint count (H-13)."""
    assert _count(client, headers, handover_id, scope="cash", counted=CASH_OPENING).json()["status"] == "incoming"
    assert _count(client, headers, handover_id, scope="cash", counted=CASH_OPENING).json()["status"] == "matched"


def _to_dual_sign(client: TestClient, headers: dict[str, str], handover_id: str) -> None:
    """Walk the whole reception flow up to the signature step; every item is settled at 0."""
    _settle_cash(client, headers, handover_id)
    assert _advance(client, headers, handover_id).json()["step"] == "shelf"
    assert _item(client, headers, handover_id, PARA, 0).json()["status"] == "matched"
    assert _item(client, headers, handover_id, DICLO, 0).json()["status"] == "matched"
    assert _advance(client, headers, handover_id).json()["step"] == "checklists"
    assert _advance(client, headers, handover_id).json()["step"] == "parcels"
    assert _advance(client, headers, handover_id).json()["step"] == "dual_sign"


def test_start_is_refused_before_the_manager_records_the_baseline(client: TestClient) -> None:
    """A flow has nothing to compare against until the manager's opening count exists (SHF-7.1)."""
    headers = _session(client)

    refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers)

    assert refused.status_code == 409
    assert "شمارش اولیه" in refused.json()["error"]["message"]
    with client.app.state.database.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM handover").scalar_one() == 0


def test_start_is_refused_without_a_readable_accounting_copy(tmp_path: Path) -> None:
    """H-03: no copy, or a copy that cannot be read, means no reference and no open-invoice check."""
    with _client(tmp_path / "no-copy", accounting=None) as client:
        headers = _session(client)
        _baseline(client)
        refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == NO_ACCOUNTING
    assert not any(character.isdigit() for character in refused.json()["error"]["message"])

    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database, it is a text file")
    with _client(tmp_path / "broken", accounting=broken) as client:
        headers = _session(client)
        _baseline(client)
        unreachable = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers)
    assert unreachable.status_code == 409
    assert unreachable.json()["error"]["message"] == NO_ACCOUNTING


def test_start_is_refused_while_an_invoice_is_still_open(tmp_path: Path) -> None:
    """H-03: the tool's scenario leaves one invoice open, and a handover never starts over it."""
    with _client(tmp_path / "app", accounting=build(tmp_path / "accounting.db").path) as client:
        headers = _session(client)
        _baseline(client)
        refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers)
    assert refused.status_code == 409
    message = refused.json()["error"]["message"]
    assert "صورت‌حساب باز" in message
    assert not any(character.isdigit() for character in message)


def test_a_second_reception_flow_of_the_same_shift_is_refused(client: TestClient) -> None:
    """H-12/H-10: one reception flow per shift, and the second person only records events."""
    headers = _session(client)
    _baseline(client)
    first = _start(client, headers)

    second = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers)

    assert second.status_code == 409
    assert "باز شده" in second.json()["error"]["message"]
    assert client.get(f"/api/v1/handover/{first['handover_id']}", headers=headers).status_code == 200


def test_a_person_cannot_hand_over_to_themselves(client: TestClient) -> None:
    """H-09: the custodian of the baseline cannot be the incoming person of that same flow."""
    headers = _session(client)
    _baseline(client, custodian=RECEPTION)

    refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers)

    assert refused.status_code == 409
    assert "به خود" in refused.json()["error"]["message"]


def test_a_role_without_the_permission_is_refused(client: TestClient) -> None:
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]

    client.cookies.clear()
    accountant = _session(client, "accountant")
    start = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=accountant)
    managers = client.get("/api/v1/handover/managers", headers=accountant)

    assert start.status_code == 403
    assert managers.status_code == 403
    assert start.json()["error"]["code"] == "forbidden"

    client.cookies.clear()
    nurse = _session(client, "nurse")
    cash = _count(client, nurse, handover_id, scope="cash", counted=CASH_OPENING)

    assert cash.status_code == 403, "a nurse may count items but never the cash box (H-10)"
    assert cash.json()["error"]["code"] == "forbidden"


def test_no_session_is_refused(client: TestClient) -> None:
    state = client.get("/api/v1/handover/01J00000000000000000000000")
    start = client.post("/api/v1/handover/start", json={"flow": "reception"})

    assert state.status_code == 401
    assert state.json()["error"]["code"] == "not_authenticated"
    assert start.status_code == 401


def test_an_unsafe_request_without_the_csrf_proof_is_refused(client: TestClient) -> None:
    _session(client)
    _baseline(client)

    missing = client.post("/api/v1/handover/start", json={"flow": "reception"})
    wrong = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=NO_CSRF)

    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"
    assert wrong.status_code == 403


def test_a_count_answers_a_status_and_nothing_else(client: TestClient) -> None:
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]

    cash = _count(client, headers, handover_id, scope="cash", counted=CASH_OPENING)
    cash_done = _count(client, headers, handover_id, scope="cash", counted=CASH_OPENING)
    _advance(client, headers, handover_id)
    item = _item(client, headers, handover_id, PARA, 0)

    assert cash.json() == {"status": "incoming"}
    assert cash_done.json() == {"status": "matched"}
    assert item.json() == {"status": "matched"}
    for response in (cash, cash_done, item):
        assert not any(character.isdigit() for character in response.text)


def test_the_steps_are_enforced_in_order(client: TestClient) -> None:
    """H-04: the cash box settles before the shelf, and every item of a step before the next one."""
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]

    early = _advance(client, headers, handover_id)
    wrong_step = _item(client, headers, handover_id, PARA, 0)

    assert (early.status_code, wrong_step.status_code) == (409, 409)
    _settle_cash(client, headers, handover_id)
    assert _advance(client, headers, handover_id).json()["step"] == "shelf"
    assert _item(client, headers, handover_id, PARA, 0).json()["status"] == "matched"
    still_open = _advance(client, headers, handover_id)
    assert still_open.status_code == 409
    assert _item(client, headers, handover_id, DICLO, 0).json()["status"] == "matched"
    assert _advance(client, headers, handover_id).json()["step"] == "checklists"


def test_a_joint_count_is_only_taken_when_the_chain_asks_for_it(client: TestClient) -> None:
    """H-05/H-13: a joint figure belongs to a chain that already has two differing blind counts."""
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]

    no_joint_yet = client.post(
        f"/api/v1/handover/{handover_id}/joint", json={"scope": "cash", "counted": CASH_OPENING}, headers=headers
    )
    assert no_joint_yet.status_code == 409

    assert _count(client, headers, handover_id, scope="cash", counted=900_000).json()["status"] == "incoming"
    assert _count(client, headers, handover_id, scope="cash", counted=910_000).json()["status"] == "joint"
    joint = client.post(
        f"/api/v1/handover/{handover_id}/joint", json={"scope": "cash", "counted": 905_000}, headers=headers
    )

    assert joint.status_code == 200
    assert joint.json() == {"status": "settled"}


def test_the_dual_sign_step_closes_the_flow_and_refuses_a_double_sign(client: TestClient) -> None:
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]

    early = _sign(client, headers, handover_id, "incoming")
    assert early.status_code == 409, "signatures belong to the dual-sign step"

    _to_dual_sign(client, headers, handover_id)
    first = _sign(client, headers, handover_id, "incoming")
    twice = _sign(client, headers, handover_id, "incoming")
    closed = _sign(client, headers, handover_id, "outgoing")

    assert first.status_code == 200
    assert first.json()["closed"] is False
    assert twice.status_code == 409
    assert closed.json()["closed"] is True
    assert closed.json()["signed"] == ["incoming", "outgoing"]
    assert closed.json()["step"] is None


def test_early_leave_blocks_the_flow_until_the_manager_confirms_and_records_who(client: TestClient) -> None:
    """H-08: the outgoing person left; the phone confirmation names the manager and its recorder."""
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]

    left = client.post(f"/api/v1/handover/{handover_id}/early-leave", headers=headers)
    blocked = _advance(client, headers, handover_id)
    confirmed = client.post(
        f"/api/v1/handover/{handover_id}/emergency-start", json={"manager_id": MANAGER}, headers=headers
    )

    assert left.json()["early_left"] is True
    assert blocked.status_code == 409
    assert confirmed.status_code == 200
    emergency = confirmed.json()["emergency"]
    assert emergency["manager_id"] == MANAGER
    assert emergency["at"] is not None

    with client.app.state.database.read() as connection:
        row = (
            connection.exec_driver_sql(
                "SELECT actor_id, server_at, payload FROM events WHERE type = 'handover.emergency_start_confirmed'"
            )
            .mappings()
            .one()
        )
    assert row["actor_id"] == RECEPTION, "the incoming person records the manager's phone confirmation"
    assert row["server_at"]
    assert MANAGER in row["payload"]

    _count(client, headers, handover_id, scope="cash", counted=CASH_OPENING)
    assert _advance(client, headers, handover_id).json()["step"] == "shelf"


def test_a_closed_flow_rejects_further_work(client: TestClient) -> None:
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]
    _to_dual_sign(client, headers, handover_id)
    _sign(client, headers, handover_id, "incoming")
    assert _sign(client, headers, handover_id, "outgoing").json()["closed"] is True

    late_count = _count(client, headers, handover_id, scope="cash", counted=CASH_OPENING)
    late_advance = _advance(client, headers, handover_id)
    late_sign = _sign(client, headers, handover_id, "incoming")

    assert (late_count.status_code, late_advance.status_code, late_sign.status_code) == (409, 409, 409)
    assert "بسته شده" in late_count.json()["error"]["message"]


def test_the_targets_list_the_items_and_the_checklists_without_quantities(client: TestClient) -> None:
    """H-01: an item with no projection row is still listed; no quantity is ever sent to the phone."""
    headers = _session(client)
    _baseline(client)
    handover_id = _start(client, headers)["handover_id"]

    response = client.get(f"/api/v1/handover/{handover_id}/targets", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert {item["item_id"] for item in body["items"]} == {PARA, DICLO}
    assert all(set(item) == {"item_id", "name", "unit", "location_id"} for item in body["items"])
    assert {entry["item_id"] for entry in body["checklists"]} == {"store_lock_ok", "fridge_temperature"}
    assert _numbers(body) == []


def _numbers(value: Any) -> list[Any]:
    """Every numeric leaf of a JSON answer, so no count or delta can hide in a handover one."""
    if isinstance(value, dict):
        return [leaf for child in value.values() for leaf in _numbers(child)]
    if isinstance(value, list):
        return [leaf for child in value for leaf in _numbers(child)]
    return [value] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


def test_the_manager_list_names_the_manager_who_confirms_by_phone(client: TestClient) -> None:
    """H-08: the incoming person picks a name; the manager's own device is never needed."""
    headers = _session(client)

    response = client.get("/api/v1/handover/managers", headers=headers)

    assert response.status_code == 200
    assert response.json() == [{"id": MANAGER, "name": ROLES["manager"].full_name}]


def test_the_offline_queue_may_not_write_handover_events(client: TestClient) -> None:
    """ADR-016: a blind count runs against the live server, so the outbox refuses its vocabulary."""
    headers = _session(client)
    submission = {
        "client_event_id": "01J00000000000000000000000",
        "type": "handover.cash_counted",
        "payload": {"counted": 1},
        "client_at": "2026-10-28T10:00:00Z",
    }

    handover_event = client.post("/api/v1/events", json=submission, headers=headers)
    discrepancy = client.post(
        "/api/v1/events",
        json={**submission, "client_event_id": "01J00000000000000000000001", "type": "discrepancy.recorded"},
        headers=headers,
    )

    assert handover_event.status_code == 422
    assert discrepancy.status_code == 422
    assert "سرور" in handover_event.json()["error"]["message"], "the refusal is for a person, in Persian"
