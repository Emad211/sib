"""End-to-end through the real app: enroll, log in, append, project, bridge, rebuild.

One synthetic clinic: a manager mints a QR token, the phone enrolls with it, the person
logs in with a PIN, appends an event with CSRF, the projection catches up, the accounting
bridge polls a synthetic accounting database, and a full rebuild equals the live state.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auth_fixtures import PIN, create_device, create_staff
from auth_fixtures import login as login_request
from clocks import FixedClock
from fastapi.testclient import TestClient
from make_synthetic_acc_db import build
from photo_fixtures import RECEIPT_SHA, keep_receipt
from synthetic_catalog import seed as seed_catalog

from shift.adapters.accounting.views import parse_time
from shift.adapters.db import events_repo, handover_repo, migrations, projections_repo
from shift.adapters.db.core import Database
from shift.common.clock import to_tehran
from shift.common.ids import new_ulid
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.shift_clock import covering
from shift.services.accounting_bridge import AccountingBridge

MANAGER = {"staff_id": "staff-e2e-manager", "mobile": "09121110001", "device_id": "device-e2e-manager"}
NURSE = {"staff_id": "staff-e2e-nurse", "mobile": "09121110002"}
PUBKEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE-e2e-test-public-key"


def _bootstrap(client: TestClient) -> None:
    """Install the manager phone and the nurse account that will enroll over QR."""
    database: Database = client.app.state.database
    seed_catalog(database)
    create_staff(
        database,
        staff_id=MANAGER["staff_id"],
        full_name="مدیر شیفت",
        mobile=MANAGER["mobile"],
        roles=("manager",),
        pepper=client.app.state.secret_key,
    )
    create_device(database, device_id=MANAGER["device_id"], staff_id=MANAGER["staff_id"])
    create_staff(
        database,
        staff_id=NURSE["staff_id"],
        full_name="پرستار شیفت",
        mobile=NURSE["mobile"],
        roles=("nurse",),
        pepper=client.app.state.secret_key,
    )
    # ADR-021: the nursing flow's custodian is on shift; the clinic's own close hands this on, and
    # this journey starts from a nurse who already holds the trolley.
    with database.write() as connection:
        handover_repo.set_custodian(
            connection,
            flow=HandoverFlow.NURSING,
            custodian_id=NURSE["staff_id"],
            updated_at=datetime.now(UTC),
            updated_by=MANAGER["staff_id"],
        )


def _login(client: TestClient, *, mobile: str, device_id: str) -> dict[str, Any]:
    """Walk the login page's CSRF handshake and return the session payload."""
    response = login_request(client, mobile=mobile, device_id=device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return response.json()


def _enroll_phone(client: TestClient, manager_csrf: str) -> tuple[str, str]:
    """Mint a QR token for the nurse and redeem it as the phone would."""
    minted = client.post(
        "/api/v1/devices/enrollment-tokens",
        json={"staff_id": NURSE["staff_id"]},
        headers={"X-CSRFToken": manager_csrf},
    )
    assert minted.status_code == 201, minted.text
    offer = minted.json()
    token = offer["enrollment_url"].split("#t=", 1)[1]

    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={"token": token, "device_label": "گوشی پرستار", "device_pubkey": PUBKEY},
    )
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["device_id"] == offer["device_id"]
    assert enrolled.json()["staff_id"] == NURSE["staff_id"]
    return offer["device_id"], token


def test_the_whole_shift_path_from_qr_to_rebuilt_projections(client: TestClient, tmp_path: Path) -> None:
    """The only production write paths compose into one consistent event log and projection."""
    _bootstrap(client)
    manager_session = _login(client, mobile=MANAGER["mobile"], device_id=MANAGER["device_id"])
    enrolled_device, token = _enroll_phone(client, manager_session["csrf_token"])

    # The same QR cannot be redeemed twice: the credential is single use.
    reused = client.post(
        "/api/v1/devices/enroll",
        json={"token": token, "device_label": "گوشی دوم", "device_pubkey": PUBKEY},
    )
    assert reused.status_code == 409

    nurse_session = _login(client, mobile=NURSE["mobile"], device_id=enrolled_device)
    keep_receipt(client.app)
    shift = covering(to_tehran(client.app.state.clock.now_utc()))
    event_id = new_ulid()
    response = client.post(
        "/api/v1/events",
        json={
            "client_event_id": event_id,
            "idempotency_key": event_id,
            "type": "stock.waste_reported",
            "payload": {
                "item_id": "para_500_amp",
                "location_id": "shelf_reception",
                "quantity": 5,
                "waste_kind": "broken",
                "culprit_id": "staff-e2e-nurse",
                "confirmer_id": "staff-e2e-manager",
                "photo_sha256": RECEIPT_SHA,
            },
            "client_at": datetime.now(UTC).isoformat(),
            "actor_id": "staff-forged",
            "device_id": "device-forged",
            "shift_id": "shift-forged",
        },
        headers={"X-CSRFToken": nurse_session["csrf_token"]},
    )
    assert response.status_code == 200, response.text

    database: Database = client.app.state.database
    with database.read() as connection:
        event = events_repo.find_by_event_id(connection, event_id)
        stock = projections_repo.stock_rows(connection)
    assert event is not None
    assert event.actor_id == NURSE["staff_id"]
    assert event.device_id == enrolled_device
    assert event.shift_id == shift.text
    assert event.work_date == shift.work_date.isoformat()
    assert stock == [{"item_id": "para_500_amp", "location_id": "shelf_reception", "qty": -5}]

    scenario = build(tmp_path / "accounting.db")
    bridge = AccountingBridge(database, client.app.state.events, clock=FixedClock(parse_time(scenario.cutoff_at)))
    report = bridge.poll(scenario.path)
    assert report.degraded is False
    assert report.events == 3
    client.app.state.projections.sync()

    with database.read() as connection:
        live_cash = projections_repo.cash_rows(connection)
    assert live_cash == [{"cash_box_id": "reception", "expected": 215_000, "counted": 0, "variance": -215_000}]

    check = client.app.state.projections.rebuild(dry_run=True)
    assert check.events > 0
    assert check.drift == ()
    applied = client.app.state.projections.rebuild()
    assert applied.drift == ()
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == live_cash
        assert projections_repo.stock_rows(connection) == stock
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == migrations.LATEST
