"""Taking over is the key to access (ADR-021), proven through the real routes.

Every app here starts from the same synthetic roster; the bound roles are put on shift or left off
it explicitly, never by weakening the rule.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, login, put_on_shift, seed_roster
from fastapi.testclient import TestClient

from shift.app import create_app
from shift.config.settings import Settings

OFF_SHIFT_MESSAGE = "شیفت شما باز نیست"


def _client(root: Path, *, on_shift: bool) -> Iterator[TestClient]:
    settings = Settings(
        environment="development",  # plain-HTTP test client: no Secure cookies
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
    )
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key, on_shift=on_shift)
        yield client


@pytest.fixture
def off_shift_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(tmp_path / "off", on_shift=False)


@pytest.fixture
def on_shift_client(tmp_path: Path) -> Iterator[TestClient]:
    yield from _client(tmp_path / "on", on_shift=True)


def _session(client: TestClient, role: str) -> tuple[dict[str, str], dict[str, object]]:
    """Log `role` in and return its CSRF header plus the `/auth/me` body."""
    person = ROLES[role]
    issued = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert issued.status_code == 200, issued.text
    token = issued.json()["csrf_token"]
    me = client.get("/api/v1/auth/me").json()
    return {"X-CSRFToken": token}, me


def test_an_off_shift_nurse_keeps_the_handover_and_loses_the_shift(
    off_shift_client: TestClient,
) -> None:
    headers, me = _session(off_shift_client, "nurse")
    assert headers

    assert me["on_shift"] == []
    assert me["shift_notice"] == "شیفت شما باز نیست. برای شروع، شیفت را تحویل بگیرید."
    assert "events.append" not in me["permissions"]
    assert "handover.count.submit" in me["permissions"]
    assert "reward.view" in me["permissions"]
    # The screen she can still use, and the one she cannot, through the real routes.
    assert off_shift_client.get("/handover").status_code == 200
    assert off_shift_client.get("/events").status_code == 403
    assert off_shift_client.get("/rewards").status_code == 200


def test_the_refusal_is_shared_and_names_nobody_on_shift(off_shift_client: TestClient) -> None:
    headers, _me = _session(off_shift_client, "nurse")

    refusal = off_shift_client.get(
        "/api/v1/shift-events/pending",
        headers=headers,
    )

    assert refusal.status_code == 403
    body = refusal.text
    assert "اجازهٔ این کار را ندارد" in body
    for person in ROLES.values():
        assert person.full_name not in body, "a refusal never says who is on shift"
        assert person.staff_id not in body


def test_the_home_screen_says_the_shift_is_not_open(off_shift_client: TestClient) -> None:
    _session(off_shift_client, "nurse")
    home = off_shift_client.get("/")

    assert home.status_code == 200
    assert OFF_SHIFT_MESSAGE in home.text
    assert "/handover" in home.text, "the handover tile stays, so she can take over"


def test_taking_over_opens_the_shift(on_shift_client: TestClient) -> None:
    headers, me = _session(on_shift_client, "nurse")

    assert me["on_shift"] == ["nurse"]
    assert me["shift_notice"] is None
    assert on_shift_client.get("/events").status_code == 200
    assert on_shift_client.get("/").text.find(OFF_SHIFT_MESSAGE) == -1


def test_the_custody_row_is_what_hands_the_shift_on(tmp_path: Path) -> None:
    """A close hands custody to the incoming person; nothing else has to be written for access."""
    root = tmp_path / "takeover"
    with TestClient(
        create_app(
            Settings(
                environment="development",
                database_path=root / "shift.db",
                log_dir=root / "logs",
                audit_log_path=root / "logs" / "audit.log",
                secret_file=root / ".secret",
            )
        )
    ) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key, on_shift=False)
        _session(client, "nurse")
        assert client.get("/events").status_code == 403

        put_on_shift(client.app.state.database)

        assert client.get("/events").status_code == 200
        assert client.get("/api/v1/auth/me").json()["on_shift"] == ["nurse"]


def test_a_second_receptionist_declares_presence(off_shift_client: TestClient) -> None:
    person = ROLES["multi_role"]
    issued = login(off_shift_client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    headers = {"X-CSRFToken": issued.json()["csrf_token"]}

    before = off_shift_client.get("/api/v1/auth/me").json()
    assert before["on_shift"] == []
    assert "parcel.handle" not in before["permissions"], "the counter's work waits for a shift"
    assert "accountant.queue.view" in before["permissions"], "the free role never waits"

    declared = off_shift_client.post("/api/v1/shift/presence", json={"role": "reception"}, headers=headers)

    assert declared.status_code == 201, declared.text
    after = off_shift_client.get("/api/v1/auth/me").json()
    assert after["on_shift"] == ["reception"]
    assert "parcel.handle" in after["permissions"]


def test_the_presence_button_is_on_the_home_screen_until_it_is_pressed(
    off_shift_client: TestClient,
) -> None:
    """ADR-021 in the UI: the second receptionist presses one button and her shift opens."""
    headers, me = _session(off_shift_client, "multi_role")
    assert me["on_shift"] == []
    home = off_shift_client.get("/")
    assert "حضور در شیفت" in home.text
    assert 'data-presence-role="reception"' in home.text

    declared = off_shift_client.post("/api/v1/shift/presence", json={"role": "reception"}, headers=headers)

    assert declared.status_code == 201, declared.text
    assert declared.json()["message"] == "حضور شما در این شیفت ثبت شد."
    after = off_shift_client.get("/")
    assert 'data-presence-role="reception"' not in after.text, "the button is done once she is on shift"
    assert OFF_SHIFT_MESSAGE not in after.text


def test_a_nurse_has_no_presence_button(off_shift_client: TestClient) -> None:
    """A nurse comes on shift by taking over, so the home screen offers her no shortcut."""
    _session(off_shift_client, "nurse")

    assert "data-presence-role=" not in off_shift_client.get("/").text


def test_the_shift_doctor_declares_presence_and_nobody_else_does(off_shift_client: TestClient) -> None:
    doctor = ROLES["shift_doctor"]
    issued = login(off_shift_client, mobile=doctor.mobile, device_id=doctor.device_id, pin=PIN)
    headers = {"X-CSRFToken": issued.json()["csrf_token"]}
    assert off_shift_client.get("/events").status_code == 403

    declared = off_shift_client.post("/api/v1/shift/presence", json={"role": "shift_doctor"}, headers=headers)

    assert declared.status_code == 201, declared.text
    assert off_shift_client.get("/api/v1/auth/me").json()["on_shift"] == ["shift_doctor"]
    assert off_shift_client.get("/events").status_code == 200

    # A role the person does not hold cannot be claimed, and an unguarded role cannot declare at all.
    assert (
        off_shift_client.post("/api/v1/shift/presence", json={"role": "reception"}, headers=headers).status_code == 403
    )
    nurse = ROLES["nurse"]
    nurse_session = login(off_shift_client, mobile=nurse.mobile, device_id=nurse.device_id, pin=PIN)
    refused = off_shift_client.post(
        "/api/v1/shift/presence",
        json={"role": "reception"},
        headers={"X-CSRFToken": nurse_session.json()["csrf_token"]},
    )
    assert refused.status_code == 403, "a nurse comes on shift by taking over, not by a button"
