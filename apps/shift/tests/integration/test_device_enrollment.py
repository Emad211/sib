"""Device enrollment: one-time QR token, 10-minute expiry, binding and revocation (step 8)."""

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from auth_fixtures import PIN, PUBKEY, ROLES, SPARE, FixedClock, create_staff, login, seed_roster
from fastapi.testclient import TestClient

from shift.adapters.db import devices_repo, staff_repo
from shift.adapters.db.core import Database
from shift.common.hashing import sha256_hex
from shift.config.settings import Settings
from shift.services.auth import SESSION_COOKIE
from shift.services.devices import ENROLLMENT_TTL, DeviceService, EnrollmentRejected


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
    return client


def _manager(client: TestClient) -> str:
    response = login(client, mobile=ROLES["manager"].mobile, device_id=ROLES["manager"].device_id, pin=PIN)
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _mint(client: TestClient, csrf: str, staff_id: str):
    return client.post(
        "/api/v1/devices/enrollment-tokens",
        json={"staff_id": staff_id},
        headers={"X-CSRF-Token": csrf},
    )


def _enroll(client: TestClient, token: str, label: str = "گوشی انبار", pubkey: str = PUBKEY):
    return client.post(
        "/api/v1/devices/enroll",
        json={"token": token, "device_label": label, "device_pubkey": pubkey},
    )


def test_the_manager_mints_a_qr_whose_token_lives_in_the_url_fragment(seeded: TestClient) -> None:
    csrf = _manager(seeded)

    response = _mint(seeded, csrf, ROLES["reception"].staff_id)

    assert response.status_code == 201
    offer = response.json()
    assert offer["enrollment_url"].startswith("http://testserver/enroll#t=")
    assert "?" not in offer["enrollment_url"].split("#")[0], "the token must never be a query string"
    assert offer["qr_svg"].startswith("data:image/svg+xml")
    assert offer["staff_id"] == ROLES["reception"].staff_id
    assert ENROLLMENT_TTL == timedelta(minutes=10)


def test_enrollment_is_single_use_and_records_the_device(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, ROLES["nurse"].staff_id).json()
    token = offer["enrollment_url"].split("#t=", 1)[1]

    first = _enroll(seeded, token)
    replay = _enroll(seeded, token)

    assert first.status_code == 201
    assert first.json()["device_id"] == offer["device_id"]
    assert first.json()["staff_id"] == ROLES["nurse"].staff_id
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "enrollment_token_used"


def test_an_expired_token_is_refused_and_leaves_no_device(database: Database) -> None:
    create_staff(database, staff_id="staff-late", full_name="دیرآمده", mobile="09122222222", roles=("nurse",))
    clock = FixedClock()
    service = DeviceService(database, clock=clock)
    offer = service.mint(staff_id="staff-late", created_by="staff-manager", origin="https://shift.test")
    token = offer.url.split("#t=", 1)[1]

    clock.advance(timedelta(minutes=11))

    with pytest.raises(EnrollmentRejected) as caught:
        service.enroll(token=token, label="گوشی", pubkey=PUBKEY)
    assert caught.value.reason == "expired"
    with database.read() as connection:
        assert devices_repo.find_device(connection, offer.device_id) is None


def test_an_unknown_token_is_a_404_and_a_failed_enrollment_keeps_the_token_usable(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, SPARE.staff_id).json()
    token = offer["enrollment_url"].split("#t=", 1)[1]

    unknown = _enroll(seeded, "invalid-token-that-was-never-minted")
    bad_label = _enroll(seeded, token, label="   ")
    good = _enroll(seeded, token)

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "enrollment_token_unknown"
    assert bad_label.status_code == 400
    assert good.status_code == 201, "a rejected payload must not consume the token"


def test_each_token_mints_its_own_device_id(seeded: TestClient) -> None:
    csrf = _manager(seeded)

    first = _mint(seeded, csrf, ROLES["nurse"].staff_id).json()
    second = _mint(seeded, csrf, ROLES["nurse"].staff_id).json()

    assert first["device_id"] != second["device_id"]


def test_a_paired_phone_can_log_in_and_revocation_kills_that_session(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, ROLES["storekeeper"].staff_id).json()
    token = offer["enrollment_url"].split("#t=", 1)[1]
    enrolled = _enroll(seeded, token, label="گوشی انبار")

    opened = login(seeded, mobile=ROLES["storekeeper"].mobile, device_id=enrolled.json()["device_id"])
    assert opened.status_code == 200
    session_token = seeded.cookies.get(SESSION_COOKIE)

    manager_csrf = _manager(seeded)
    revoked = seeded.post(
        f"/api/v1/devices/{enrolled.json()['device_id']}/revoke", headers={"X-CSRF-Token": manager_csrf}
    )

    assert revoked.status_code == 204
    seeded.cookies.set(SESSION_COOKIE, session_token)
    assert seeded.get("/api/v1/auth/me").status_code == 401, "revocation must kill the live session"
    again = login(seeded, mobile=ROLES["storekeeper"].mobile, device_id=enrolled.json()["device_id"])
    assert again.status_code == 403
    assert again.json()["error"]["code"] == "device_revoked"


def test_revocation_is_idempotent_and_records_the_reason(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    device_id = ROLES["accountant"].device_id

    first = seeded.post(f"/api/v1/devices/{device_id}/revoke", headers={"X-CSRF-Token": csrf})
    second = seeded.post(f"/api/v1/devices/{device_id}/revoke", headers={"X-CSRF-Token": csrf})

    assert first.status_code == 204
    assert second.status_code == 204
    with seeded.app.state.database.read() as connection:
        device = devices_repo.find_device(connection, device_id)
    assert device.revoked_reason == "revoked_by_manager"


def test_revoking_an_unknown_device_is_a_404(seeded: TestClient) -> None:
    csrf = _manager(seeded)

    response = seeded.post("/api/v1/devices/device-that-never-existed/revoke", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "device_not_found"


def test_an_expired_token_answers_410_over_http(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, SPARE.staff_id).json()
    token = offer["enrollment_url"].split("#t=", 1)[1]
    with seeded.app.state.database.write() as connection:
        connection.exec_driver_sql(
            "UPDATE enrollment_tokens SET expires_at = '2020-01-01T00:00:00.000000Z' WHERE device_id = ?",
            (offer["device_id"],),
        )

    response = _enroll(seeded, token)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "enrollment_token_expired"


def test_a_token_for_a_deactivated_staff_member_is_refused(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, SPARE.staff_id).json()
    with seeded.app.state.database.write() as connection:
        staff_repo.set_active(connection, staff_id=SPARE.staff_id, active=False)

    response = _enroll(seeded, offer["enrollment_url"].split("#t=", 1)[1])

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "staff_inactive"


def test_non_managers_cannot_mint_or_revoke(seeded: TestClient) -> None:
    issued = login(seeded, mobile=ROLES["reception"].mobile, device_id=ROLES["reception"].device_id, pin=PIN).json()
    headers = {"X-CSRF-Token": issued["csrf_token"]}

    minted = seeded.post(
        "/api/v1/devices/enrollment-tokens",
        json={"staff_id": ROLES["reception"].staff_id},
        headers=headers,
    )
    revoked = seeded.post(f"/api/v1/devices/{ROLES['nurse'].device_id}/revoke", headers=headers)
    listed = seeded.get("/api/v1/devices", headers=headers)

    assert (minted.status_code, revoked.status_code, listed.status_code) == (403, 403, 403)


def test_tokens_never_reach_the_logs_or_the_device_list(seeded: TestClient, settings: Settings) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, ROLES["nurse"].staff_id).json()
    token = offer["enrollment_url"].split("#t=", 1)[1]

    devices = seeded.get("/api/v1/devices", headers={"X-CSRF-Token": csrf})

    assert token not in devices.text
    for log_name in ("audit.log", "shift.log"):
        assert token not in (settings.log_dir / log_name).read_text(encoding="utf-8")


def test_minting_for_an_unknown_or_inactive_staff_is_refused(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    unknown = _mint(seeded, csrf, "staff-does-not-exist")

    with seeded.app.state.database.write() as connection:
        staff_repo.set_active(connection, staff_id=SPARE.staff_id, active=False)
    inactive = _mint(seeded, csrf, SPARE.staff_id)

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "staff_not_found"
    assert inactive.status_code == 404
    assert inactive.json()["error"]["code"] == "staff_not_found"


def test_the_enrollment_payload_is_validated_and_a_refusal_keeps_the_token(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    token = _mint(seeded, csrf, SPARE.staff_id).json()["enrollment_url"].split("#t=", 1)[1]

    too_short = _enroll(seeded, token, pubkey="too-short")
    not_base64 = _enroll(seeded, token, pubkey="!" * 24)
    accepted = _enroll(seeded, token)

    assert too_short.status_code == 422
    assert not_base64.status_code == 400
    assert not_base64.json()["error"]["code"] == "device_invalid"
    assert accepted.status_code == 201


def test_the_enroll_page_is_public_and_carries_no_token(seeded: TestClient) -> None:
    response = seeded.get("/enroll")

    assert response.status_code == 200
    assert "ثبت این گوشی" in response.text
    assert "#t=" not in response.text


def test_print_enroll_qr_writes_a_png_and_prints_the_url(tmp_path: Path) -> None:
    database_path = tmp_path / "shift.db"
    database = Database(database_path)
    database.migrate()
    try:
        create_staff(
            database, staff_id=SPARE.staff_id, full_name=SPARE.full_name, mobile=SPARE.mobile, roles=SPARE.roles
        )
    finally:
        database.close()

    image = tmp_path / "qr.png"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/print_enroll_qr.py",
            "--db",
            str(database_path),
            "--staff-id",
            SPARE.staff_id,
            "--origin",
            "https://shift.sibclinic.ir",
            "--out",
            str(image),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "https://shift.sibclinic.ir/enroll#t=" in completed.stdout
    assert image.read_bytes().startswith(b"\x89PNG")


def test_print_enroll_qr_refuses_an_unknown_staff(tmp_path: Path) -> None:
    database_path = tmp_path / "shift.db"
    Database(database_path).migrate()

    completed = subprocess.run(
        [
            sys.executable,
            "tools/print_enroll_qr.py",
            "--db",
            str(database_path),
            "--staff-id",
            "staff-missing",
            "--origin",
            "https://shift.sibclinic.ir",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "cannot mint" in completed.stdout


def test_stored_tokens_are_digests_only(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    token = _mint(seeded, csrf, SPARE.staff_id).json()["enrollment_url"].split("#t=", 1)[1]

    with seeded.app.state.database.read() as connection:
        row = devices_repo.find_token(connection, sha256_hex(token))

    assert row is not None
    assert row.token_hash != token


def test_the_audit_trail_records_mint_enroll_and_revoke(seeded: TestClient, settings: Settings) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, SPARE.staff_id).json()
    enrolled = _enroll(seeded, offer["enrollment_url"].split("#t=", 1)[1])
    manager_csrf = _manager(seeded)
    seeded.post(f"/api/v1/devices/{enrolled.json()['device_id']}/revoke", headers={"X-CSRF-Token": manager_csrf})

    audit = (settings.log_dir / "audit.log").read_text(encoding="utf-8")

    for marker in ("device.token_minted", "device.enrolled", "device.revoked"):
        assert marker in audit, marker


def test_an_enrollment_row_carries_the_tehran_expiry(seeded: TestClient) -> None:
    csrf = _manager(seeded)
    offer = _mint(seeded, csrf, SPARE.staff_id).json()

    with seeded.app.state.database.read() as connection:
        row = devices_repo.find_token(connection, sha256_hex(offer["enrollment_url"].split("#t=", 1)[1]))

    assert offer["expires_at_fa"].count(":") == 1
    assert row.expires_at == offer["expires_at"]
