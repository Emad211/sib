"""Session lifecycle: login, 12 h expiry, logout, lockout with manager unlock, CSRF (S4)."""

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from auth_fixtures import ALTERNATE_PIN, PIN, ROLES, SPARE, create_device, create_staff, login, seed_roster
from fastapi.testclient import TestClient

from shift.adapters.db import devices_repo, sessions_repo, staff_repo
from shift.adapters.db.core import Database
from shift.api.deps import PREAUTH_CSRF_COOKIE
from shift.app import create_app
from shift.common.clock import utc_iso
from shift.common.hashing import sha256_hex
from shift.config.settings import Settings
from shift.domain.permissions import Permission
from shift.services.auth import (
    PIN_HASHER,
    SESSION_COOKIE,
    SESSION_TTL,
    AccountLocked,
    AuthService,
    InvalidCredentials,
    SessionInvalid,
    hash_pin,
    normalise_mobile,
    verify_pin,
)

SET_COOKIE = "set-cookie"


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    """The app client with one staff member and one phone per role."""
    seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
    return client


def _login(client: TestClient, role: str = "reception", pin: str = PIN):
    person = ROLES[role]
    return login(client, mobile=person.mobile, device_id=person.device_id, pin=pin)


def _manager_session(client: TestClient) -> str:
    """Log the manager in and return their CSRF token for the admin calls below."""
    response = _login(client, "manager")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _session_row(client: TestClient, token: str):
    with client.app.state.database.read() as connection:
        return sessions_repo.find(connection, sha256_hex(token))


def test_login_returns_the_principal_and_a_hardened_cookie(seeded: TestClient) -> None:
    response = _login(seeded)

    assert response.status_code == 200
    body = response.json()
    assert body["staff"]["id"] == ROLES["reception"].staff_id
    assert body["roles"] == ["reception"]
    assert Permission.HANDOVER_CASH_SUBMIT.value in body["permissions"]
    assert body["device"]["id"] == ROLES["reception"].device_id
    assert body["csrf_token"]
    cookie = response.headers.get_list(SET_COOKIE)[0]
    assert "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower()
    assert "Path=/" in cookie
    assert "Secure" not in cookie, "development serves plain HTTP on the clinic LAN"


def test_production_cookies_are_secure(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "prod.db",
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        secret_file=tmp_path / ".secret",
        environment="production",
    )
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        response = _login(client)
        logout = client.post("/api/v1/auth/logout", headers={"X-CSRFToken": response.json()["csrf_token"]})

    assert "Secure" in response.headers.get_list(SET_COOKIE)[0]
    assert "Secure" in logout.headers.get_list(SET_COOKIE)[0], "the deletion must match the cookie it clears"


def _cookie_headers(response: httpx.Response, name: str) -> list[str]:
    """Every `Set-Cookie` header of this response that is about one cookie name."""
    return [cookie for cookie in response.headers.get_list(SET_COOKIE) if cookie.startswith(f"{name}=")]


def test_logout_clears_the_session_cookie_with_the_flags_it_was_set_with(seeded: TestClient) -> None:
    issued = _login(seeded).json()

    logout = seeded.post("/api/v1/auth/logout", headers={"X-CSRFToken": issued["csrf_token"]})
    cleared = _cookie_headers(logout, SESSION_COOKIE)

    assert len(cleared) == 1
    for attribute in ("max-age=0", "httponly", "samesite=strict", "path=/"):
        assert attribute in cleared[0].lower(), attribute


def test_login_clears_the_preauth_cookie_with_its_own_flags(seeded: TestClient) -> None:
    response = _login(seeded)

    cleared = _cookie_headers(response, PREAUTH_CSRF_COOKIE)

    assert len(cleared) == 1
    for attribute in ("max-age=0", "samesite=strict", "path=/"):
        assert attribute in cleared[0].lower(), attribute


def test_persian_digits_and_international_prefix_reach_the_same_account(seeded: TestClient) -> None:
    person = ROLES["reception"]

    persian = login(seeded, mobile="۰۹۱۲۰۰۰۰۰۰۱", device_id=person.device_id)
    international = login(seeded, mobile="+989120000001", device_id=person.device_id)

    assert persian.status_code == 200
    assert international.status_code == 200


def test_every_refusal_about_the_person_looks_identical(seeded: TestClient) -> None:
    person = ROLES["reception"]
    with seeded.app.state.database.write() as connection:
        staff_repo.set_active(connection, staff_id=SPARE.staff_id, active=False)

    refusals = {
        "unknown mobile": login(seeded, mobile="09129999999", device_id=person.device_id),
        "wrong pin": login(seeded, mobile=person.mobile, device_id=person.device_id, pin=ALTERNATE_PIN),
        "borrowed device": login(seeded, mobile=person.mobile, device_id=ROLES["nurse"].device_id),
        "inactive staff": login(seeded, mobile=SPARE.mobile, device_id=SPARE.device_id),
    }

    for label, response in refusals.items():
        assert response.status_code == 401, label
        assert response.json() == refusals["unknown mobile"].json(), f"{label} tells the phone too much"
    assert refusals["wrong pin"].json()["error"]["code"] == "invalid_credentials"


def test_pin_is_accepted_only_from_a_device_paired_with_that_person(seeded: TestClient) -> None:
    person = ROLES["reception"]

    unknown = login(seeded, mobile=person.mobile, device_id="device-not-enrolled")
    borrowed = login(seeded, mobile=person.mobile, device_id=ROLES["nurse"].device_id)

    assert unknown.status_code == 403
    assert unknown.json()["error"]["code"] == "device_not_enrolled"
    assert borrowed.status_code == 401, "a phone paired with somebody else must not confirm the mobile"
    assert borrowed.json()["error"]["code"] == "invalid_credentials"


def test_a_revoked_device_cannot_open_a_session(seeded: TestClient) -> None:
    person = ROLES["reception"]
    seeded.app.state.devices.revoke(device_id=person.device_id, actor_id="staff-manager")

    response = _login(seeded)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "device_revoked"


def test_inactive_staff_cannot_log_in(seeded: TestClient, settings: Settings) -> None:
    with seeded.app.state.database.write() as connection:
        staff_repo.set_active(connection, staff_id=ROLES["nurse"].staff_id, active=False)

    response = _login(seeded, "nurse")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials", "deactivation must not be an existence oracle"
    audit = (settings.log_dir / "audit.log").read_text(encoding="utf-8")
    assert "auth.login result=inactive" in audit, "only the audit log may name the reason"


def test_five_wrong_pins_lock_the_account_until_the_manager_unlocks_it(seeded: TestClient) -> None:
    for _ in range(4):
        assert _login(seeded, pin=ALTERNATE_PIN).status_code == 401
    locked = _login(seeded, pin=ALTERNATE_PIN)
    still_locked = _login(seeded, pin=PIN)

    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "account_locked"
    assert still_locked.status_code == 423, "the right PIN must not bypass the lock"

    csrf = _manager_session(seeded)
    unlocked = seeded.post(f"/api/v1/staff/{ROLES['reception'].staff_id}/unlock", headers={"X-CSRFToken": csrf})

    assert unlocked.status_code == 204
    assert _login(seeded).status_code == 200


def test_the_lock_lifts_by_itself_after_fifteen_minutes(database: Database) -> None:
    from auth_fixtures import FixedClock

    create_staff(
        database,
        staff_id="staff-locked",
        full_name="قفل‌شده",
        mobile="09121111111",
        roles=("reception",),
    )
    create_device(database, device_id="device-locked", staff_id="staff-locked")
    clock = FixedClock()
    service = AuthService(database, clock=clock)

    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            service.login(mobile="09121111111", pin=ALTERNATE_PIN, device_id="device-locked")
    with pytest.raises(AccountLocked):
        service.login(mobile="09121111111", pin=ALTERNATE_PIN, device_id="device-locked")
    with pytest.raises(AccountLocked):
        service.login(mobile="09121111111", pin=PIN, device_id="device-locked")

    clock.advance(timedelta(minutes=16))

    assert service.login(mobile="09121111111", pin=PIN, device_id="device-locked").token


def test_me_needs_a_session_and_returns_the_csrf_token(seeded: TestClient) -> None:
    anonymous = seeded.get("/api/v1/auth/me")
    _login(seeded)
    authenticated = seeded.get("/api/v1/auth/me")

    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "not_authenticated"
    assert authenticated.status_code == 200
    assert authenticated.json()["staff"]["id"] == ROLES["reception"].staff_id


def test_logout_revokes_the_session_on_the_server(seeded: TestClient) -> None:
    issued = _login(seeded).json()
    token = seeded.cookies.get(SESSION_COOKIE)

    logout = seeded.post("/api/v1/auth/logout", headers={"X-CSRFToken": issued["csrf_token"]})

    assert logout.status_code == 204
    seeded.cookies.set(SESSION_COOKIE, token)
    assert seeded.get("/api/v1/auth/me").status_code == 401, "the cookie must be dead server-side"
    assert _session_row(seeded, token).revoked_reason == "logout"


def test_unsafe_verbs_without_the_csrf_header_are_refused(seeded: TestClient) -> None:
    _login(seeded)

    missing = seeded.post("/api/v1/auth/logout")
    wrong = seeded.post("/api/v1/auth/logout", headers={"X-CSRFToken": "not-the-token"})

    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_failed"
    assert wrong.status_code == 403


def test_login_without_the_preauth_cookie_is_refused(seeded: TestClient) -> None:
    person = ROLES["reception"]

    response = seeded.post(
        "/api/v1/auth/login",
        json={"mobile": person.mobile, "pin": PIN, "device_id": person.device_id},
        headers={"X-CSRFToken": "invented"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_a_session_dies_after_twelve_hours(seeded: TestClient) -> None:
    token = secrets.token_urlsafe(32)
    person = ROLES["reception"]
    now = datetime.now(UTC)
    with seeded.app.state.database.write() as connection:
        sessions_repo.insert(
            connection,
            token_hash=sha256_hex(token),
            csrf_token="csrf",
            staff_id=person.staff_id,
            device_id=person.device_id,
            issued_at=utc_iso(now - SESSION_TTL),
            expires_at=utc_iso(now - timedelta(seconds=1)),
        )

    seeded.cookies.set(SESSION_COOKIE, token)

    assert seeded.get("/api/v1/auth/me").status_code == 401
    assert SESSION_TTL == timedelta(hours=12)


def test_closing_the_shift_revokes_every_session_of_the_team(seeded: TestClient) -> None:
    _login(seeded)
    reception_token = seeded.cookies.get(SESSION_COOKIE)
    _login(seeded, "nurse")

    revoked = seeded.app.state.auth.revoke_staff_sessions(
        staff_ids=[ROLES["reception"].staff_id, ROLES["nurse"].staff_id], reason="shift_closed"
    )

    assert revoked == 2
    seeded.cookies.set(SESSION_COOKIE, reception_token)
    assert seeded.get("/api/v1/auth/me").status_code == 401
    assert _session_row(seeded, reception_token).revoked_reason == "shift_closed"


def test_pin_reset_is_manager_only_and_policy_checked(seeded: TestClient) -> None:
    reception = ROLES["reception"]
    issued = _login(seeded).json()
    reception_token = seeded.cookies.get(SESSION_COOKIE)
    url = f"/api/v1/staff/{reception.staff_id}/pin-reset"

    forbidden = seeded.post(url, json={"pin": ALTERNATE_PIN}, headers={"X-CSRFToken": issued["csrf_token"]})

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"

    csrf = _manager_session(seeded)
    weak = seeded.post(url, json={"pin": "123456"}, headers={"X-CSRFToken": csrf})

    assert weak.status_code == 400
    assert weak.json()["error"]["code"] == "pin_rejected"
    assert weak.json()["error"]["details"]["rule"] == "pin_sequence"

    accepted = seeded.post(url, json={"pin": ALTERNATE_PIN}, headers={"X-CSRFToken": csrf})

    assert accepted.status_code == 204
    seeded.cookies.set(SESSION_COOKIE, reception_token)
    assert seeded.get("/api/v1/auth/me").status_code == 401, "a reset PIN must end old sessions"
    assert _login(seeded, pin=ALTERNATE_PIN).status_code == 200
    assert _login(seeded, pin=PIN).status_code == 401


def test_the_staff_directory_never_exposes_pin_hashes(seeded: TestClient) -> None:
    csrf = _manager_session(seeded)

    response = seeded.get("/api/v1/staff", headers={"X-CSRFToken": csrf})

    assert response.status_code == 200
    assert len(response.json()) == len(ROLES) + 1
    assert "argon2" not in response.text
    assert "pin" not in response.text.lower()


def test_no_pin_or_token_is_written_to_the_logs(seeded: TestClient, settings: Settings) -> None:
    issued = _login(seeded).json()
    token = seeded.cookies.get(SESSION_COOKIE)

    audit = (settings.log_dir / "audit.log").read_text(encoding="utf-8")
    public = (settings.log_dir / "shift.log").read_text(encoding="utf-8")

    for secret in (PIN, token, issued["csrf_token"]):
        assert secret not in audit
        assert secret not in public


@pytest.mark.parametrize(
    "hostile",
    [
        "//evil.example/steal",
        "/\\evil.example/steal",
        "/\t/evil.example",
        "\\\\evil.example/steal",
        "http://evil.example/steal",
        "evil.example/steal",
        "/admin\\devices",
    ],
)
def test_the_login_page_never_offers_an_offsite_redirect(seeded: TestClient, hostile: str) -> None:
    response = seeded.get("/login", params={"next": hostile})

    assert response.headers["cache-control"] == "no-store"
    assert 'data-next="/"' in response.text, f"{hostile!r} must not survive as a destination"


def test_the_login_page_keeps_a_same_origin_destination(seeded: TestClient) -> None:
    response = seeded.get("/login", params={"next": "/admin/devices"})

    assert response.headers["cache-control"] == "no-store"
    assert 'data-next="/admin/devices"' in response.text


def test_a_page_sends_anonymous_visitors_to_login_and_back(seeded: TestClient) -> None:
    response = seeded.get("/admin/devices", headers={"Accept": "text/html"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/admin/devices"


def test_the_audit_trail_records_lock_unlock_and_reset(seeded: TestClient, settings: Settings) -> None:
    for _ in range(5):
        _login(seeded, pin=ALTERNATE_PIN)
    csrf = _manager_session(seeded)
    seeded.post(f"/api/v1/staff/{ROLES['reception'].staff_id}/unlock", headers={"X-CSRFToken": csrf})
    seeded.post(
        f"/api/v1/staff/{ROLES['reception'].staff_id}/pin-reset",
        json={"pin": ALTERNATE_PIN},
        headers={"X-CSRFToken": csrf},
    )

    audit = (settings.log_dir / "audit.log").read_text(encoding="utf-8")

    for marker in ("auth.lock", "auth.unlock", "auth.pin_reset", "auth.login result=bad_pin"):
        assert marker in audit, marker


def test_a_session_dies_when_its_device_is_revoked_behind_its_back(seeded: TestClient) -> None:
    _login(seeded)
    token = seeded.cookies.get(SESSION_COOKIE)
    with seeded.app.state.database.write() as connection:
        devices_repo.revoke_device(
            connection,
            device_id=ROLES["reception"].device_id,
            revoked_at=utc_iso(datetime.now(UTC)),
            reason="lost_phone",
        )

    seeded.cookies.set(SESSION_COOKIE, token)

    assert seeded.get("/api/v1/auth/me").status_code == 401, "authenticate() must re-check the device"


def test_a_session_dies_when_the_person_is_deactivated(seeded: TestClient) -> None:
    _login(seeded, "nurse")
    token = seeded.cookies.get(SESSION_COOKIE)
    with seeded.app.state.database.write() as connection:
        staff_repo.set_active(connection, staff_id=ROLES["nurse"].staff_id, active=False)

    seeded.cookies.set(SESSION_COOKIE, token)

    assert seeded.get("/api/v1/auth/me").status_code == 401


def test_a_role_outside_the_matrix_invalidates_the_session(seeded: TestClient) -> None:
    _login(seeded, "nurse")
    token = seeded.cookies.get(SESSION_COOKIE)
    with seeded.app.state.database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO staff_role (staff_id, role, granted_at) VALUES (?, 'purchasing_owner', ?)",
            (ROLES["nurse"].staff_id, utc_iso(datetime.now(UTC))),
        )

    seeded.cookies.set(SESSION_COOKIE, token)

    assert seeded.get("/api/v1/auth/me").status_code == 401, "an unknown role must fail closed"


def test_unlock_and_reset_report_a_missing_staff(seeded: TestClient) -> None:
    csrf = _manager_session(seeded)

    unlocked = seeded.post("/api/v1/staff/staff-ghost/unlock", headers={"X-CSRFToken": csrf})
    reset = seeded.post(
        "/api/v1/staff/staff-ghost/pin-reset", json={"pin": ALTERNATE_PIN}, headers={"X-CSRFToken": csrf}
    )

    assert unlocked.status_code == 404
    assert reset.status_code == 404
    assert unlocked.json()["error"]["code"] == "staff_not_found"


def test_one_good_login_forgets_the_earlier_failures(seeded: TestClient) -> None:
    assert _login(seeded, pin=ALTERNATE_PIN).status_code == 401

    assert _login(seeded).status_code == 200
    with seeded.app.state.database.read() as connection:
        staff = staff_repo.find(connection, ROLES["reception"].staff_id)

    assert staff.failed_attempts == 0
    assert staff.locked_until is None


def test_the_service_refuses_empty_and_forged_session_tokens(database: Database) -> None:
    service = AuthService(database)

    with pytest.raises(SessionInvalid, match="absent"):
        service.authenticate("")
    with pytest.raises(SessionInvalid, match="unknown"):
        service.authenticate("a-token-that-was-never-issued")


@pytest.mark.parametrize(
    "raw",
    ["09120000001", "+989120000001", "00989120000001", "۰۹۱۲۰۰۰۰۰۰۱", "0912 000 0001"],
)
def test_every_mobile_spelling_normalises_to_the_same_identity(raw: str) -> None:
    assert normalise_mobile(raw) == "09120000001"


@pytest.mark.parametrize("raw", ["9120000001", "0912000000", "091200000012", "not-a-mobile"])
def test_impossible_mobile_numbers_are_refused_before_any_lookup(raw: str) -> None:
    with pytest.raises(InvalidCredentials):
        normalise_mobile(raw)


def test_the_production_hasher_is_argon2id_with_owner_grade_parameters() -> None:
    digest = hash_pin(PIN)

    assert digest.startswith("$argon2id$")
    assert PIN_HASHER.time_cost >= 3
    assert PIN_HASHER.memory_cost >= 65536
    assert verify_pin(digest, PIN) is True
    assert verify_pin(digest, ALTERNATE_PIN) is False
    assert verify_pin("not-a-digest", PIN) is False


def test_the_install_secret_peppers_every_pin() -> None:
    from auth_fixtures import TEST_HASHER

    first = hash_pin(PIN, pepper="install-one", hasher=TEST_HASHER)
    second = hash_pin(PIN, pepper="install-two", hasher=TEST_HASHER)

    assert first != second, "the same PIN must hash differently under another install secret"
    assert verify_pin(first, PIN, pepper="install-one", hasher=TEST_HASHER) is True
    assert verify_pin(first, PIN, pepper="install-two", hasher=TEST_HASHER) is False
    assert verify_pin(first, PIN, hasher=TEST_HASHER) is False, "the digest needs its pepper back"


def test_the_app_hashes_pins_with_its_install_secret(seeded: TestClient) -> None:
    from auth_fixtures import TEST_HASHER

    with seeded.app.state.database.read() as connection:
        staff = staff_repo.find(connection, ROLES["reception"].staff_id)

    assert verify_pin(staff.pin_hash, PIN, pepper=seeded.app.state.secret_key, hasher=TEST_HASHER), (
        "the running service must pepper with the secret file it created at startup"
    )
