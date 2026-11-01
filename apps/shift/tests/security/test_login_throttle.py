"""S-2, S-18: a flood of login attempts is refused before it costs a hash, per mobile and for a minute only."""

from datetime import timedelta

from auth_fixtures import FIXED_NOW, FixedClock
from fastapi.testclient import TestClient

from shift.services.login_throttle import LoginThrottle


def test_the_window_lets_a_person_in_again_after_a_minute() -> None:
    clock = FixedClock(FIXED_NOW)
    throttle = LoginThrottle(clock=clock, attempts=3, window=timedelta(seconds=60))

    assert [throttle.hit("0912") for _ in range(4)] == [True, True, True, False]
    assert throttle.hit("0935") is True, "another mobile has its own counter"
    clock.advance(timedelta(seconds=61))
    assert throttle.hit("0912") is True


def test_a_flood_of_invented_mobiles_hits_the_clinic_wide_ceiling(client: TestClient) -> None:
    client.app.state.login_ceiling = LoginThrottle(attempts=5)
    client.get("/login")
    token = client.cookies.get("shift_csrf", "")

    codes = [
        client.post(
            "/api/v1/auth/login",
            json={"mobile": f"0912000{n:04d}", "pin": "000000", "device_id": "x"},
            headers={"X-CSRFToken": token},
        ).status_code
        for n in range(8)
    ]

    assert codes[5:] == [429, 429, 429]


def test_a_burst_of_logins_for_one_mobile_gets_429_before_any_hashing(client: TestClient) -> None:
    client.get("/login")
    token = client.cookies.get("shift_csrf", "")
    body = {"mobile": "09120000099", "pin": "000000", "device_id": "no-such-device"}
    codes = [
        client.post("/api/v1/auth/login", json=body, headers={"X-CSRFToken": token}).status_code for _ in range(15)
    ]

    assert codes.count(429) >= 1
    assert 429 not in codes[:10]
    refused = client.post("/api/v1/auth/login", json=body, headers={"X-CSRFToken": token})
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "too_many_attempts"
    # Persian digits are the same person
    persian = {**body, "mobile": "۰۹۱۲۰۰۰۰۰۹۹"}
    assert client.post("/api/v1/auth/login", json=persian, headers={"X-CSRFToken": token}).status_code == 429
