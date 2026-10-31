"""Staff and roles (ADR-022): the manager adds people and changes roles, and login obeys the same answer.

Before this slice the manager's role API wrote one table and login read another, so a role change did
nothing. These tests drive the real routes and then sign in again, which is the only proof that counts.
"""

from datetime import datetime, timedelta

import pytest
from auth_fixtures import PIN, ROLES, SPARE, login, seed_roster
from fastapi.testclient import TestClient

from shift.common.clock import TEHRAN
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering

NEW_PIN = "573819"


def _previous(shift: ShiftKey) -> ShiftKey:
    """The shift before `shift`: a decision made during it starts exactly now."""
    if shift.rank > 1:
        return ShiftKey(shift.work_date, shift.rank - 1)
    return ShiftKey(shift.work_date - timedelta(days=1), 3)


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
    return client


def _sign_in(client: TestClient, role: str = "manager") -> dict[str, str]:
    person = ROLES[role]
    client.cookies.clear()
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return {"X-CSRFToken": response.json()["csrf_token"]}


def _roles_after_login(client: TestClient, *, staff_id: str) -> list[str]:
    """Open a session for `staff_id` straight through the auth service, as a phone login would."""
    person = SPARE if staff_id == SPARE.staff_id else next(p for p in ROLES.values() if p.staff_id == staff_id)
    issued = client.app.state.auth.login(mobile=person.mobile, pin=PIN, device_id=person.device_id)
    return list(issued.principal.roles)


def test_a_new_person_can_sign_in_at_once_with_the_starting_roles(seeded: TestClient) -> None:
    headers = _sign_in(seeded)
    created = seeded.post(
        "/api/v1/staff",
        json={
            "full_name": "  پرستار  تازه ",
            "mobile": "۰۹۱۲۰۰۰۰۱۰۱",
            "pin": NEW_PIN,
            "roles": ["nurse", "storekeeper"],
        },
        headers=headers,
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "پرستار تازه"
    assert body["mobile"] == "09120000101"
    assert body["roles"] == ["nurse", "storekeeper"]
    listed = {row["id"]: row for row in seeded.get("/api/v1/staff", headers=headers).json()}
    assert listed[body["id"]]["roles"] == ["nurse", "storekeeper"]
    # No device is paired yet, so the phone is refused the way every unknown phone is; the row exists.
    assert listed[body["id"]]["active"] is True


def test_the_same_mobile_twice_and_a_weak_pin_are_refused_in_persian(seeded: TestClient) -> None:
    headers = _sign_in(seeded)
    body = {"full_name": "کارمند", "mobile": ROLES["nurse"].mobile, "pin": NEW_PIN, "roles": ["nurse"]}

    duplicate = seeded.post("/api/v1/staff", json=body, headers=headers)
    assert duplicate.status_code == 409
    assert "قبلاً" in duplicate.json()["error"]["message"]

    weak = seeded.post("/api/v1/staff", json={**body, "mobile": "09120000102", "pin": "111111"}, headers=headers)
    assert weak.status_code == 400

    no_role = seeded.post("/api/v1/staff", json={**body, "mobile": "09120000103", "roles": []}, headers=headers)
    assert no_role.status_code == 422


def test_only_the_staff_manager_may_use_the_staff_routes(seeded: TestClient) -> None:
    headers = _sign_in(seeded, "reception")
    body = {"full_name": "کارمند", "mobile": "09120000104", "pin": NEW_PIN, "roles": ["nurse"]}

    assert seeded.post("/api/v1/staff", json=body, headers=headers).status_code == 403
    assert seeded.get("/manager/staff", headers={"Accept": "text/html"}).status_code == 403
    assert (
        seeded.post(f"/api/v1/staff/{SPARE.staff_id}/active", json={"active": False}, headers=headers).status_code
        == 403
    )


def test_a_role_change_waits_for_the_next_shift_but_login_reads_the_same_answer(seeded: TestClient) -> None:
    """The API change is the next shift's truth; today's login keeps today's roles (R-09)."""
    headers = _sign_in(seeded)
    assert _roles_after_login(seeded, staff_id=SPARE.staff_id) == ["nurse"]

    granted = seeded.put(f"/api/v1/staff/{SPARE.staff_id}/roles/storekeeper", json={"granted": True}, headers=headers)
    assert granted.status_code == 200, granted.text
    now_shift = covering(datetime.now(TEHRAN))
    assert granted.json()["effective_from_shift"] == now_shift.following().text

    assert _roles_after_login(seeded, staff_id=SPARE.staff_id) == ["nurse"], "not before the next shift"
    row = next(r for r in seeded.get("/api/v1/staff", headers=headers).json() if r["id"] == SPARE.staff_id)
    assert row["roles"] == ["nurse"]
    assert row["next_roles"] == ["nurse", "storekeeper"]


def test_a_decision_already_in_force_changes_the_session_roles(seeded: TestClient) -> None:
    """A version effective from this shift on grants and takes back roles at the next sign-in."""
    catalog = seeded.app.state.catalog
    now_shift = covering(datetime.now(TEHRAN))

    catalog.set_role(
        SPARE.staff_id, "storekeeper", granted=True, actor_id="staff-manager", running_shift=_previous(now_shift)
    )
    assert _roles_after_login(seeded, staff_id=SPARE.staff_id) == ["nurse", "storekeeper"]

    catalog.set_role(
        SPARE.staff_id, "nurse", granted=False, actor_id="staff-manager", running_shift=_previous(now_shift)
    )
    assert _roles_after_login(seeded, staff_id=SPARE.staff_id) == ["storekeeper"]


def test_deactivating_a_person_ends_their_session_and_keeps_one_manager(seeded: TestClient) -> None:
    spare_session = seeded.app.state.auth.login(mobile=SPARE.mobile, pin=PIN, device_id=SPARE.device_id)
    headers = _sign_in(seeded)

    off = seeded.post(f"/api/v1/staff/{SPARE.staff_id}/active", json={"active": False}, headers=headers)
    assert off.status_code == 204
    with pytest.raises(Exception, match="session rejected"):
        seeded.app.state.auth.authenticate(spare_session.token)

    own = seeded.post(f"/api/v1/staff/{ROLES['manager'].staff_id}/active", json={"active": False}, headers=headers)
    assert own.status_code == 400, "a manager cannot lock themselves out"
    last = seeded.put(
        f"/api/v1/staff/{ROLES['manager'].staff_id}/roles/manager", json={"granted": False}, headers=headers
    )
    assert last.status_code == 400, "the clinic never ends up without a manager"

    back = seeded.post(f"/api/v1/staff/{SPARE.staff_id}/active", json={"active": True}, headers=headers)
    assert back.status_code == 204


def test_the_staff_page_lists_everyone_with_a_form_for_a_new_person(seeded: TestClient) -> None:
    _sign_in(seeded)
    page = seeded.get("/manager/staff", headers={"Accept": "text/html"})

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert ROLES["nurse"].full_name in page.text
    assert "افزودن کارمند" in page.text
    assert "/static/manager-staff.js" in page.text
