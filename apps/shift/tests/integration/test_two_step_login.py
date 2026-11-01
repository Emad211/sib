"""ADR-027: the two-step login of the head and the manager, through the real routes."""

import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, seed_roster
from fastapi.testclient import TestClient

from shift.app import create_app
from shift.config.settings import Settings
from shift.domain import totp

MANAGER = ROLES["manager"]
API = "/api/v1/manager/two-step"


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / "app"
    settings = Settings(
        environment="development",
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
    )
    with TestClient(create_app(settings)) as running:
        seed_roster(running.app.state.database, pepper=running.app.state.secret_key)
        yield running


def _login(client: TestClient, *, otp: str | None = None, host: str | None = None):
    client.cookies.clear()
    client.get("/login")
    headers = {"X-CSRFToken": client.cookies.get("shift_csrf", "")}
    if host:
        headers["Host"] = host
    body = {"mobile": MANAGER.mobile, "pin": PIN, "device_id": MANAGER.device_id}
    if otp is not None:
        body["otp"] = otp
    return client.post("/api/v1/auth/login", json=body, headers=headers)


def _current_code(secret: str) -> str:
    return totp.code_for(secret, totp.step_at(time.time()))


def _previous_code(secret: str) -> str:
    """The step before now: still inside the drift, so the setup leaves the current step for the login."""
    return totp.code_for(secret, totp.step_at(time.time()) - 1)


def _turn_on(client: TestClient) -> str:
    signed = _login(client)
    assert signed.status_code == 200, signed.text
    csrf = {"X-CSRFToken": signed.json()["csrf_token"]}
    setup = client.post(f"{API}/begin", headers=csrf).json()
    assert "<svg" in setup["qr_svg"] and setup["uri"].startswith("otpauth://totp/")
    assert client.post(f"{API}/confirm", json={"code": "000000"}, headers=csrf).status_code == 400
    assert (
        client.post(f"{API}/confirm", json={"code": _previous_code(setup["secret"])}, headers=csrf).status_code == 200
    )
    return setup["secret"]


def test_once_on_the_login_asks_for_the_code_and_a_code_works_once(client: TestClient) -> None:
    secret = _turn_on(client)

    asked = _login(client)
    assert asked.status_code == 401 and asked.json()["error"]["code"] == "otp_required"
    wrong = _login(client, otp="123456" if _current_code(secret) != "123456" else "654321")
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "invalid_credentials"

    fresh = _login(client, otp=_current_code(secret))
    assert fresh.status_code == 200, fresh.text
    replay = _login(client, otp=_current_code(secret))
    assert replay.status_code == 401, "the same code cannot open a second session"


def test_the_secret_is_sealed_in_the_database(client: TestClient) -> None:
    secret = _turn_on(client)
    with client.app.state.database.read() as connection:
        stored = connection.exec_driver_sql("SELECT secret FROM staff_totp").scalar_one()
    assert secret not in stored


def test_a_manager_from_outside_needs_the_two_step_login(client: TestClient) -> None:
    client.app.state.settings = replace(client.app.state.settings, public_origin="https://shift.example.ir")

    outside = _login(client, host="shift.example.ir")
    assert outside.status_code == 403 and outside.json()["error"]["code"] == "two_step_required"
    assert _login(client).status_code == 200, "inside the clinic it stays the manager's own choice"

    secret = _turn_on(client)
    answer = _login(client, host="shift.example.ir", otp=_current_code(secret))
    assert answer.status_code == 200, answer.text
