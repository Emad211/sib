"""The handover HTTP skin: one test per behaviour that can cost the clinic money or break access.

Every test drives the real app (lifespan, auth, CSRF, the service and its event log) on a synthetic
database, the synthetic reference catalog and a synthetic accounting copy. The manager's baseline
record is the only seed: a flow never starts without a custodian, and the baseline is what gives the
box its opening figure and the shelf its first counted items (H-01, H-02, H-09).

S03 §3 has each person sign on their own device, so the tests that walk a flow to its close hold two
sessions at once: the incoming person's phone opens the flow and the outgoing person's phone joins it.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, login, put_on_shift, seed_roster
from fastapi.testclient import TestClient
from make_synthetic_acc_db import build
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import handover_repo
from shift.app import create_app
from shift.config.settings import Settings
from shift.domain.handover_counts import CountStatus, counter_side
from shift.domain.handover_fsm import HandoverFlow
from shift.services.auth import SESSION_COOKIE
from shift.services.handover import NO_ACCOUNTING

# The custodian the manager records hands the flow over and signs on their own phone (S03 §3).
OUTGOING = ROLES["multi_role"].staff_id
OUTGOING_ROLE = "multi_role"
MANAGER = ROLES["manager"].staff_id
RECEPTION = ROLES["reception"].staff_id
CASH_OPENING = 1_000_000
SHELF = "shelf_reception"
PARA = "para_500_amp"
DICLO = "diclofenac_75_amp"
NO_CSRF = {"X-CSRFToken": "not-the-token"}


def _settings(root: Path, accounting: Path | None) -> Settings:
    return Settings(
        environment="development",  # plain-HTTP test client: no Secure cookies
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
        accounting_db_path=accounting,
    )


@contextmanager
def _client(root: Path, *, accounting: Path | None) -> Iterator[TestClient]:
    """A running app on a fresh database, the seeded roster and the reference catalog.

    The roster starts off shift: these tests record the manager's baseline, which is what puts the
    flow's custodian on shift through the real path (ADR-021).
    """
    with TestClient(create_app(_settings(root, accounting))) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key, on_shift=False)
        seed_catalog(client.app.state.database)
        yield client


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """The everyday app: a readable accounting copy with no invoices in it yet."""
    with _client(tmp_path / "app", accounting=empty_accounting(tmp_path / "accounting.db")) as running:
        yield running


@dataclass(frozen=True, slots=True)
class Phone:
    """One person signed in on their own paired phone, with the CSRF proof for unsafe verbs."""

    staff_id: str
    headers: dict[str, str]
    token: str


def _phone(client: TestClient, role: str = "reception") -> Phone:
    """Log `role` in on their paired phone; the session stays valid next to other people's."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    token = response.cookies.get(SESSION_COOKIE)
    assert token, "the login handshake issued no session"
    return Phone(person.staff_id, {"X-CSRFToken": response.json()["csrf_token"]}, token)


def _as(client: TestClient, phone: Phone) -> dict[str, str]:
    """Act as one signed-in person; both phones of a handover keep their sessions side by side."""
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, phone.token)
    return phone.headers


def _baseline(
    client: TestClient,
    *,
    flow: HandoverFlow = HandoverFlow.RECEPTION,
    custodian: str = OUTGOING,
    opening_cash: int | None = CASH_OPENING,
    items: list[dict[str, Any]] | None = None,
) -> None:
    """The manager's baseline record: the custodian, the box's opening figure and the shelf's count."""
    default_items: list[dict[str, Any]] = (
        [
            {"item_id": PARA, "location_id": SHELF, "quantity": 0},
            {"item_id": DICLO, "location_id": SHELF, "quantity": 0},
        ]
        if flow is HandoverFlow.RECEPTION
        else []
    )
    client.app.state.handover.record_baseline(
        flow=flow,
        custodian_id=custodian,
        manager_id=MANAGER,
        opening_cash=opening_cash,
        items=default_items if items is None else items,
    )


def _start(client: TestClient, phone: Phone, flow: str = "reception") -> dict[str, Any]:
    response = client.post("/api/v1/handover/start", json={"flow": flow}, headers=_as(client, phone))
    assert response.status_code == 201, response.text
    return response.json()


def _whose_turn(client: TestClient, phone: Phone, handover_id: str, scope: str, item_id: str | None) -> Phone:
    """A4: every count is taken on its own side's phone, so a test counts as whoever the flow asks for."""
    answered = client.get(f"/api/v1/handover/{handover_id}", headers=_as(client, phone))
    if answered.status_code != 200:  # a colleague of another flow reads nothing, and counts nothing either
        return phone
    state = answered.json()
    status = state["cash_status"] if scope == "cash" else state["counts"].get(item_id)
    if status is None:
        return phone
    side = counter_side(scope, CountStatus(status), alone=state["emergency"] is not None)
    if state["my_side"] and side != state["my_side"]:
        return _phone(client, "reception" if phone.staff_id == OUTGOING else OUTGOING_ROLE)
    return phone


def _count(
    client: TestClient,
    phone: Phone,
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
    phone = _whose_turn(client, phone, handover_id, scope, item_id)
    return client.post(f"/api/v1/handover/{handover_id}/counts", json=body, headers=_as(client, phone))


def _item(client: TestClient, phone: Phone, handover_id: str, item_id: str, counted: int, location: str = SHELF):
    return _count(client, phone, handover_id, scope="item", counted=counted, item_id=item_id, location_id=location)


def _advance(client: TestClient, phone: Phone, handover_id: str):
    return client.post(f"/api/v1/handover/{handover_id}/advance", headers=_as(client, phone))


def _sign(client: TestClient, phone: Phone, handover_id: str):
    """Sign with the caller's own side; the body names no signer (H-04, R-04)."""
    return client.post(f"/api/v1/handover/{handover_id}/sign", headers=_as(client, phone))


def _checklist(
    client: TestClient,
    phone: Phone,
    handover_id: str,
    item_id: str,
    *,
    ok: bool,
    reading: float | None = None,
):
    body: dict[str, Any] = {"item_id": item_id, "ok": ok}
    if reading is not None:
        body["reading"] = reading
    return client.post(f"/api/v1/handover/{handover_id}/checklists", json=body, headers=_as(client, phone))


def _parcel(
    client: TestClient,
    phone: Phone,
    handover_id: str,
    parcel_id: str = "parcel-01",
    packages: int = 2,
):
    body = {"parcel_id": parcel_id, "packages": packages}
    return client.post(f"/api/v1/handover/{handover_id}/parcels", json=body, headers=_as(client, phone))


def _current(client: TestClient, phone: Phone):
    """The open flows of this shift, as the second phone finds them to join."""
    return client.get("/api/v1/handover/current", headers=_as(client, phone))


def _settle_cash(client: TestClient, phone: Phone, handover_id: str) -> None:
    """Two blind counts on one figure settle the box without a joint count (H-13)."""
    assert _count(client, phone, handover_id, scope="cash", counted=CASH_OPENING).json()["status"] == "incoming"
    assert _count(client, phone, handover_id, scope="cash", counted=CASH_OPENING).json()["status"] == "matched"


def _to_checklists(client: TestClient, phone: Phone, handover_id: str) -> None:
    """Walk the reception flow to its checklists step; every item is settled at 0."""
    _settle_cash(client, phone, handover_id)
    assert _advance(client, phone, handover_id).json()["step"] == "shelf"
    assert _item(client, phone, handover_id, PARA, 0).json()["status"] == "matched"
    assert _item(client, phone, handover_id, DICLO, 0).json()["status"] == "matched"
    assert _advance(client, phone, handover_id).json()["step"] == "checklists"


def _to_dual_sign(client: TestClient, phone: Phone, handover_id: str) -> None:
    """Walk the whole reception flow up to the signature step without recording middle steps."""
    _to_checklists(client, phone, handover_id)
    assert _advance(client, phone, handover_id).json()["step"] == "parcels"
    assert _advance(client, phone, handover_id).json()["step"] == "dual_sign"


def _events(client: TestClient, event_type: str) -> list[tuple[str, dict[str, Any]]]:
    """One flow's events of a type as (actor, payload), in the order the log recorded them."""
    with client.app.state.database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT actor_id, payload FROM events WHERE type = ? ORDER BY seq", (event_type,)
        ).mappings()
        return [(str(row["actor_id"]), json.loads(str(row["payload"]))) for row in rows]


def test_start_is_refused_before_the_manager_records_the_baseline(client: TestClient) -> None:
    """A flow has nothing to compare against until the manager's opening count exists (SHF-7.1)."""
    phone = _phone(client)

    refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, phone))

    assert refused.status_code == 409
    assert "شمارش اولیه" in refused.json()["error"]["message"]
    with client.app.state.database.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM handover").scalar_one() == 0


def test_start_is_refused_without_a_readable_accounting_copy(tmp_path: Path) -> None:
    """H-03: no copy, or a copy that cannot be read, means no reference and no open-invoice check."""
    with _client(tmp_path / "no-copy", accounting=None) as client:
        phone = _phone(client)
        _baseline(client)
        refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, phone))
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == NO_ACCOUNTING
    assert not any(character.isdigit() for character in refused.json()["error"]["message"])

    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database, it is a text file")
    with _client(tmp_path / "broken", accounting=broken) as client:
        phone = _phone(client)
        _baseline(client)
        unreachable = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, phone))
    assert unreachable.status_code == 409
    assert unreachable.json()["error"]["message"] == NO_ACCOUNTING


def test_start_is_refused_while_an_invoice_is_still_open(tmp_path: Path) -> None:
    """H-03: the tool's scenario leaves one invoice open, and a handover never starts over it."""
    with _client(tmp_path / "app", accounting=build(tmp_path / "accounting.db").path) as client:
        phone = _phone(client)
        _baseline(client)
        refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, phone))
    assert refused.status_code == 409
    message = refused.json()["error"]["message"]
    assert "صورت‌حساب باز" in message
    assert not any(character.isdigit() for character in message)


def test_a_second_reception_flow_of_the_same_shift_is_refused(client: TestClient) -> None:
    """H-12/H-10: one reception flow per shift, and the second person only records events."""
    phone = _phone(client)
    _baseline(client)
    first = _start(client, phone)

    second = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, phone))

    assert second.status_code == 409
    assert "باز شده" in second.json()["error"]["message"]
    assert client.get(f"/api/v1/handover/{first['handover_id']}", headers=_as(client, phone)).status_code == 200


def test_a_person_cannot_hand_over_to_themselves(client: TestClient) -> None:
    """H-09: the custodian of the baseline cannot be the incoming person of that same flow."""
    phone = _phone(client)
    _baseline(client, custodian=RECEPTION)

    refused = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, phone))

    assert refused.status_code == 409
    assert "به خود" in refused.json()["error"]["message"]


def test_a_role_without_the_permission_is_refused(client: TestClient) -> None:
    phone = _phone(client)
    _baseline(client)
    handover_id = _start(client, phone)["handover_id"]

    accountant = _phone(client, "accountant")
    start = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=_as(client, accountant))
    managers = client.get("/api/v1/handover/managers", headers=_as(client, accountant))

    assert start.status_code == 403
    assert managers.status_code == 403
    assert start.json()["error"]["code"] == "forbidden"

    nurse = _phone(client, "nurse")
    cash = _count(client, nurse, handover_id, scope="cash", counted=CASH_OPENING)

    assert cash.status_code == 403, "a nurse may count items but never the cash box (H-10)"
    assert cash.json()["error"]["code"] == "forbidden"


def test_no_session_is_refused(client: TestClient) -> None:
    state = client.get("/api/v1/handover/01J00000000000000000000000")
    start = client.post("/api/v1/handover/start", json={"flow": "reception"})
    current = client.get("/api/v1/handover/current")

    assert state.status_code == 401
    assert state.json()["error"]["code"] == "not_authenticated"
    assert start.status_code == 401
    assert current.status_code == 401


def test_an_unsafe_request_without_the_csrf_proof_is_refused(client: TestClient) -> None:
    _phone(client)
    _baseline(client)

    missing = client.post("/api/v1/handover/start", json={"flow": "reception"})
    wrong = client.post("/api/v1/handover/start", json={"flow": "reception"}, headers=NO_CSRF)

    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"
    assert wrong.status_code == 403


def test_a_count_answers_a_status_and_nothing_else(client: TestClient) -> None:
    phone = _phone(client)
    _baseline(client)
    handover_id = _start(client, phone)["handover_id"]

    cash = _count(client, phone, handover_id, scope="cash", counted=CASH_OPENING)
    cash_done = _count(client, phone, handover_id, scope="cash", counted=CASH_OPENING)
    _advance(client, phone, handover_id)
    item = _item(client, phone, handover_id, PARA, 0)

    assert cash.json() == {"status": "incoming"}
    assert cash_done.json() == {"status": "matched"}
    assert item.json() == {"status": "matched"}
    for response in (cash, cash_done, item):
        assert not any(character.isdigit() for character in response.text)


def test_the_steps_are_enforced_in_order(client: TestClient) -> None:
    """H-04: the cash box settles before the shelf, and every item of a step before the next one."""
    phone = _phone(client)
    _baseline(client)
    handover_id = _start(client, phone)["handover_id"]

    early = _advance(client, phone, handover_id)
    wrong_step = _item(client, phone, handover_id, PARA, 0)

    assert (early.status_code, wrong_step.status_code) == (409, 409)
    _settle_cash(client, phone, handover_id)
    assert _advance(client, phone, handover_id).json()["step"] == "shelf"
    assert _item(client, phone, handover_id, PARA, 0).json()["status"] == "matched"
    still_open = _advance(client, phone, handover_id)
    assert still_open.status_code == 409
    assert _item(client, phone, handover_id, DICLO, 0).json()["status"] == "matched"
    assert _advance(client, phone, handover_id).json()["step"] == "checklists"


def test_a_joint_count_is_only_taken_when_the_chain_asks_for_it(client: TestClient) -> None:
    """H-05/H-13: a joint figure belongs to a chain that already has two differing blind counts."""
    phone = _phone(client)
    _baseline(client)
    handover_id = _start(client, phone)["handover_id"]

    no_joint_yet = client.post(
        f"/api/v1/handover/{handover_id}/joint",
        json={"scope": "cash", "counted": CASH_OPENING},
        headers=_as(client, phone),
    )
    assert no_joint_yet.status_code == 409

    assert _count(client, phone, handover_id, scope="cash", counted=900_000).json()["status"] == "incoming"
    assert _count(client, phone, handover_id, scope="cash", counted=910_000).json()["status"] == "joint"
    joint = client.post(
        f"/api/v1/handover/{handover_id}/joint",
        json={"scope": "cash", "counted": 905_000},
        headers=_as(client, phone),
    )

    assert joint.status_code == 200
    assert joint.json() == {"status": "settled"}


def test_the_signer_is_the_session_and_a_third_person_is_refused(client: TestClient) -> None:
    """H-04/R-04: each side signs from its own phone, and no body can name a signer."""
    incoming = _phone(client)
    outgoing = _phone(client, OUTGOING_ROLE)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]

    early = _sign(client, incoming, handover_id)
    assert early.status_code == 409, "signatures belong to the dual-sign step"

    _to_dual_sign(client, incoming, handover_id)

    stranger = _phone(client, "nurse")
    refused = _sign(client, stranger, handover_id)
    assert refused.status_code == 403, "a nurse holds handover.sign but is neither side of this flow"
    assert "مال شما نیست" in refused.json()["error"]["message"]
    assert not any(character.isdigit() for character in refused.json()["error"]["message"])

    first = _sign(client, incoming, handover_id)
    twice = _sign(client, incoming, handover_id)
    closed = _sign(client, outgoing, handover_id)

    assert first.status_code == 200
    assert first.json()["closed"] is False
    assert twice.status_code == 409
    assert closed.json()["closed"] is True
    assert closed.json()["signed"] == ["incoming", "outgoing"]
    assert closed.json()["step"] is None

    signatures = [(actor, payload["signer"]) for actor, payload in _events(client, "handover.signed")]
    assert signatures == [(incoming.staff_id, "incoming"), (outgoing.staff_id, "outgoing")], (
        "R-04: the outgoing signature names the outgoing person"
    )


def test_the_checklists_and_parcels_are_recorded_on_their_own_steps(client: TestClient) -> None:
    """H-04's two middle steps now record what they see, and only on their own step."""
    incoming = _phone(client)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]
    _to_checklists(client, incoming, handover_id)

    answer = _checklist(client, incoming, handover_id, "store_lock_ok", ok=True)
    reading = _checklist(client, incoming, handover_id, "fridge_temperature", ok=True, reading=7.5)
    early_parcel = _parcel(client, incoming, handover_id)

    assert answer.status_code == 200
    assert answer.json()["step"] == "checklists"
    assert reading.status_code == 200
    assert early_parcel.status_code == 409, "a parcel belongs to the parcels step"

    assert _advance(client, incoming, handover_id).json()["step"] == "parcels"
    parcel = _parcel(client, incoming, handover_id, "parcel-01", 2)
    late_answer = _checklist(client, incoming, handover_id, "store_lock_ok", ok=False)

    assert parcel.status_code == 200
    assert parcel.json()["step"] == "parcels"
    assert late_answer.status_code == 409, "a checklist answer belongs to the checklists step"

    answers = _events(client, "handover.checklist_answered")
    assert [(actor, payload["item_id"], payload["ok"]) for actor, payload in answers] == [
        (incoming.staff_id, "store_lock_ok", True),
        (incoming.staff_id, "fridge_temperature", True),
    ]
    assert answers[1][1]["reading"] == 7.5
    parcels = _events(client, "handover.parcel_handed")
    assert parcels == [(incoming.staff_id, {"parcel_id": "parcel-01", "packages": 2})]

    assert _advance(client, incoming, handover_id).json()["step"] == "dual_sign"


def test_current_lists_the_open_flows_of_the_running_shift_only(client: TestClient) -> None:
    """S03 §3: the second phone finds its shift's open flows, and never another shift's."""
    incoming = _phone(client)
    outgoing = _phone(client, OUTGOING_ROLE)
    nurse = _phone(client, "nurse")
    _baseline(client)
    _baseline(
        client,
        flow=HandoverFlow.NURSING,
        opening_cash=None,
        items=[{"item_id": "saline_500_bag", "location_id": "cpr_room", "quantity": 0}],
    )
    reception_id = _start(client, incoming)["handover_id"]
    nursing_id = _start(client, nurse, flow="nursing")["handover_id"]

    with client.app.state.database.write() as connection:
        handover_repo.insert(
            connection,
            handover_id="01J00000000000000000000001",
            shift_id="2020-01-01#1",
            flow=HandoverFlow.RECEPTION,
            opened_at=datetime(2020, 1, 1, 6, 0, tzinfo=UTC),
            work_date=date(2020, 1, 1),
            outgoing_id="staff-past",
            incoming_id="staff-present",
            created_at=datetime(2020, 1, 1, 6, 0, tzinfo=UTC),
        )

    listed = _current(client, nurse)

    assert listed.status_code == 200
    assert sorted(listed.json(), key=lambda row: row["flow"]) == [
        {
            "handover_id": nursing_id,
            "flow": "nursing",
            "step": "cpr_room",
            "signed": [],
            "early_left": False,
        },
        {
            "handover_id": reception_id,
            "flow": "reception",
            "step": "cash",
            "signed": [],
            "early_left": False,
        },
    ], "a flow of another shift is never listed"

    _to_dual_sign(client, incoming, reception_id)
    assert _sign(client, incoming, reception_id).json()["closed"] is False
    assert _sign(client, outgoing, reception_id).json()["closed"] is True
    assert [row["flow"] for row in _current(client, incoming).json()] == ["nursing"], "a closed flow is gone"


def test_early_leave_blocks_the_flow_until_the_manager_confirms_and_records_who(client: TestClient) -> None:
    """H-08: the outgoing person left; the phone confirmation names the manager and its recorder."""
    phone = _phone(client)
    _baseline(client)
    handover_id = _start(client, phone)["handover_id"]

    left = client.post(f"/api/v1/handover/{handover_id}/early-leave", headers=_as(client, phone))
    blocked = _advance(client, phone, handover_id)
    confirmed = client.post(
        f"/api/v1/handover/{handover_id}/emergency-start",
        json={"manager_id": MANAGER},
        headers=_as(client, phone),
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

    _count(client, phone, handover_id, scope="cash", counted=CASH_OPENING)
    assert _advance(client, phone, handover_id).json()["step"] == "shelf"


def test_a_closed_flow_rejects_further_work(client: TestClient) -> None:
    incoming = _phone(client)
    outgoing = _phone(client, OUTGOING_ROLE)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]
    _to_dual_sign(client, incoming, handover_id)
    _sign(client, incoming, handover_id)
    assert _sign(client, outgoing, handover_id).json()["closed"] is True

    late_count = _count(client, incoming, handover_id, scope="cash", counted=CASH_OPENING)
    late_advance = _advance(client, incoming, handover_id)
    late_sign = _sign(client, incoming, handover_id)
    late_parcel = _parcel(client, incoming, handover_id)

    assert (late_count.status_code, late_advance.status_code, late_sign.status_code) == (409, 409, 409)
    assert late_parcel.status_code == 409
    assert "بسته شده" in late_count.json()["error"]["message"]


def test_the_targets_list_the_items_and_the_checklists_without_quantities(client: TestClient) -> None:
    """H-01: an item with no projection row is still listed; no quantity is ever sent to the phone."""
    phone = _phone(client)
    _baseline(client)
    handover_id = _start(client, phone)["handover_id"]

    response = client.get(f"/api/v1/handover/{handover_id}/targets", headers=_as(client, phone))

    assert response.status_code == 200
    body = response.json()
    assert {item["item_id"] for item in body["items"]} == {PARA, DICLO}
    assert all(set(item) == {"item_id", "name", "unit", "location_id", "icon"} for item in body["items"])
    assert {entry["item_id"] for entry in body["checklists"]} == {"store_lock_ok", "fridge_temperature"}
    assert all(entry["icon"] for entry in body["checklists"]), "G2: every checklist line carries a picture"
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
    phone = _phone(client)

    response = client.get("/api/v1/handover/managers", headers=_as(client, phone))

    assert response.status_code == 200
    assert response.json() == [{"id": MANAGER, "name": ROLES["manager"].full_name}]


def test_the_offline_queue_may_not_write_handover_events(client: TestClient) -> None:
    # ADR-021: the person is on shift, so the refusal under test is the flow rule, not access.
    put_on_shift(client.app.state.database)
    """ADR-016: a blind count runs against the live server, so the outbox refuses its vocabulary."""
    phone = _phone(client)
    submission = {
        "client_event_id": "01J00000000000000000000000",
        "type": "handover.cash_counted",
        "payload": {"counted": 1},
        "client_at": "2026-10-28T10:00:00Z",
    }

    handover_event = client.post("/api/v1/events", json=submission, headers=_as(client, phone))
    discrepancy = client.post(
        "/api/v1/events",
        json={**submission, "client_event_id": "01J00000000000000000000001", "type": "discrepancy.recorded"},
        headers=_as(client, phone),
    )

    assert handover_event.status_code == 422
    assert discrepancy.status_code == 422
    assert "سرور" in handover_event.json()["error"]["message"], "the refusal is for a person, in Persian"


def test_the_nursing_flow_asks_only_its_own_checklist(client: TestClient) -> None:
    """A3: the trolley belongs to nursing; reception's store lines never appear on its step."""
    phone = _phone(client, "nurse")
    _baseline(
        client,
        flow=HandoverFlow.NURSING,
        custodian=OUTGOING,
        opening_cash=None,
        items=[{"item_id": "saline_500_bag", "location_id": "cpr_room", "quantity": 0}],
    )
    handover_id = _start(client, phone, "nursing")["handover_id"]

    body = client.get(f"/api/v1/handover/{handover_id}/targets", headers=_as(client, phone)).json()

    assert {entry["item_id"] for entry in body["checklists"]} == {"trolley_seal_ok", "oxygen_pressure_trolley"}
    assert all(entry["icon"] for entry in body["checklists"])


def test_only_the_two_people_of_a_flow_may_count_advance_or_answer(client: TestClient) -> None:
    """A5 (H-10): a second receptionist on the same shift cannot count or move somebody else's flow.

    The person is on shift and holds the count permission, so nothing here is an access refusal: the
    flow itself belongs to its outgoing and incoming person, and a third colleague is answered 403 in
    Persian for every step of it.
    """
    _baseline(client)
    incoming = _phone(client, "reception")
    handover_id = _start(client, incoming)["handover_id"]

    # A colleague of the same shift with the same count permission, who is not one of the flow's two.
    second = _phone(client, "storekeeper")
    assert client.get("/events", headers=_as(client, second)).status_code == 200, "she may append"

    refusals = {
        "counts": _item(client, second, handover_id, PARA, 0),
        "joint": client.post(
            f"/api/v1/handover/{handover_id}/joint",
            json={"scope": "item", "item_id": PARA, "location_id": SHELF, "counted": 0},
            headers=_as(client, second),
        ),
        "advance": _advance(client, second, handover_id),
        "checklists": _checklist(client, second, handover_id, "store_lock_ok", ok=True),
        "parcels": _parcel(client, second, handover_id),
        "early-leave": client.post(f"/api/v1/handover/{handover_id}/early-leave", headers=_as(client, second)),
    }

    for step, response in refusals.items():
        assert response.status_code == 403, f"{step}: {response.text}"
        assert "دو نفرِ همین تحویل" in response.json()["error"]["message"], f"{step}: {response.text}"


# --- whose count it is (A4) ----------------------------------------------------------------------


def _raw_count(client: TestClient, phone: Phone, handover_id: str, counted: int, **target: Any):
    body = {"counted": counted, **target}
    return client.post(f"/api/v1/handover/{handover_id}/counts", json=body, headers=_as(client, phone))


def test_every_count_is_taken_by_the_side_it_belongs_to(client: TestClient) -> None:
    """A4: the box is counted by the outgoing person first, a shelf item by the incoming person first."""
    incoming = _phone(client)
    outgoing = _phone(client, OUTGOING_ROLE)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]
    mine = client.get(f"/api/v1/handover/{handover_id}", headers=_as(client, incoming)).json()
    theirs = client.get(f"/api/v1/handover/{handover_id}", headers=_as(client, outgoing)).json()
    assert (mine["my_side"], theirs["my_side"]) == ("incoming", "outgoing")

    cash = {"scope": "cash"}
    assert _raw_count(client, incoming, handover_id, CASH_OPENING, **cash).status_code == 403
    assert _raw_count(client, outgoing, handover_id, CASH_OPENING, **cash).json() == {"status": "incoming"}
    assert _raw_count(client, outgoing, handover_id, CASH_OPENING, **cash).status_code == 403
    assert _raw_count(client, incoming, handover_id, CASH_OPENING, **cash).json() == {"status": "matched"}
    _advance(client, incoming, handover_id)

    item = {"scope": "item", "item_id": PARA, "location_id": SHELF}
    assert _raw_count(client, outgoing, handover_id, 5, **item).status_code == 403
    assert _raw_count(client, incoming, handover_id, 5, **item).json() == {"status": "recount"}
    assert _raw_count(client, incoming, handover_id, 6, **item).json() == {"status": "outgoing"}
    assert _raw_count(client, incoming, handover_id, 7, **item).status_code == 403
    assert _raw_count(client, outgoing, handover_id, 7, **item).json() == {"status": "joint"}


# --- the audit's confirmed gaps (A6) -------------------------------------------------------------


def test_an_outsider_reads_nothing_of_a_flow_and_cannot_start_the_other_flow(client: TestClient) -> None:
    """R-07, H-10: a colleague of another flow cannot read its state, and a role starts only its own flow."""
    incoming = _phone(client)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]
    outsider = _phone(client, "nurse")

    assert client.get(f"/api/v1/handover/{handover_id}", headers=_as(client, outsider)).status_code == 403
    assert client.get(f"/api/v1/handover/{handover_id}/targets", headers=_as(client, outsider)).status_code == 403
    assert client.get(f"/api/v1/handover/{handover_id}", headers=_as(client, incoming)).status_code == 200

    wrong = client.post("/api/v1/handover/start", json={"flow": "nursing"}, headers=_as(client, _phone(client)))
    assert wrong.status_code == 403
    assert "جریان خودش" in wrong.json()["error"]["message"]


def test_the_phone_confirmation_names_a_real_active_manager(client: TestClient) -> None:
    """H-08, M-03: the manager the card shows is a manager, not whatever name the caller typed."""
    incoming = _phone(client)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]
    client.post(f"/api/v1/handover/{handover_id}/early-leave", headers=_as(client, incoming))

    for who in ("somebody-made-up", RECEPTION):
        refused = client.post(
            f"/api/v1/handover/{handover_id}/emergency-start", json={"manager_id": who}, headers=_as(client, incoming)
        )
        assert refused.status_code == 403, who
    accepted = client.post(
        f"/api/v1/handover/{handover_id}/emergency-start", json={"manager_id": MANAGER}, headers=_as(client, incoming)
    )
    assert accepted.status_code == 200


def test_the_checklists_are_filled_by_the_incoming_person(client: TestClient) -> None:
    incoming = _phone(client)
    outgoing = _phone(client, OUTGOING_ROLE)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]
    _to_checklists(client, incoming, handover_id)

    refused = _checklist(client, outgoing, handover_id, "store_lock_ok", ok=True)

    assert refused.status_code == 403


def _session_ends(client: TestClient, staff_id: str) -> list[str]:
    with client.app.state.database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT expires_at FROM sessions WHERE staff_id = ? AND revoked_at IS NULL", (staff_id,)
        ).all()
    return [str(row[0]) for row in rows]


def test_closing_the_flow_ends_the_outgoing_persons_session_after_a_grace_hour(client: TestClient) -> None:
    """S02:85: the shift's close signs its person out, but only an hour later, so last jobs still fit."""
    incoming = _phone(client)
    outgoing = _phone(client, OUTGOING_ROLE)
    _baseline(client)
    handover_id = _start(client, incoming)["handover_id"]
    _to_dual_sign(client, incoming, handover_id)
    before = _session_ends(client, OUTGOING)
    assert _sign(client, incoming, handover_id).status_code == 200
    assert _session_ends(client, OUTGOING) == before, "a flow that is not closed yet ends nobody's session"

    assert _sign(client, outgoing, handover_id).json()["closed"] is True

    now = client.app.state.clock.now_utc()
    for ends in _session_ends(client, OUTGOING):
        left = datetime.fromisoformat(ends) - now
        assert timedelta(minutes=55) < left <= timedelta(hours=1, minutes=1)
    assert client.get("/api/v1/auth/me", headers=_as(client, outgoing)).status_code == 200, "the grace hour"
    assert all(datetime.fromisoformat(ends) - now > timedelta(hours=2) for ends in _session_ends(client, RECEPTION)), (
        "the incoming person keeps the full session"
    )
