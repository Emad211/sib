"""The "doctor" login role is retired (Q-19, ACC-2.1).

Doctors stay as `medical_staff` rows: every visit, doctor procedure and doctor payroll line
depends on them. Only the login role and its /doctor panel are gone.
"""

import sqlite3

from conftest import login_as

from tools.make_synthetic_db import TEST_PASSWORD


def _q(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_doctor_account_cannot_log_in(synthetic_app):
    c = synthetic_app.test_client()
    for field in ("doctor_username", "reception_username", "username"):
        r = c.post("/auth/login", data={"role": "doctor", field: "doctor1", "password": TEST_PASSWORD})
        assert r.status_code == 200
        with c.session_transaction() as s:
            assert "user_id" not in s


def test_existing_doctor_session_is_dropped(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "doctor1")
    r = c.get("/reception/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")
    with c.session_transaction() as s:
        assert "user_id" not in s


def test_doctor_panel_is_gone(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    assert c.get("/doctor/").status_code == 404
    assert not [r for r in synthetic_app.url_map.iter_rules() if r.rule.startswith("/doctor")]


def test_login_page_has_no_doctor_tab(synthetic_app):
    html = synthetic_app.test_client().get("/auth/login").get_data(as_text=True)
    assert "tab-doctor" not in html and "doctor_username" not in html
    assert "tab-reception" in html and "tab-manager" in html


def test_manager_cannot_create_or_promote_to_unknown_role(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    c.post(
        "/manager/users",
        data={
            "entity_type": "user",
            "action": "add",
            "username": "newdoc",
            "password": "x-pass-123",
            "full_name": "د",
            "role": "doctor",
        },
    )
    assert _q(synthetic_app, "SELECT 1 FROM users WHERE username='newdoc'") == []

    rid = _q(synthetic_app, "SELECT id FROM users WHERE username='reception2'")[0][0]
    c.post(
        "/manager/users",
        data={
            "entity_type": "user",
            "action": "update",
            "user_id": rid,
            "username": "reception2",
            "full_name": "پذیرش دو",
            "role": "superuser",
            "is_active": "1",
        },
    )
    assert _q(synthetic_app, "SELECT role FROM users WHERE id=?", rid) == [("reception",)]


def test_manager_can_still_add_reception_user(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    c.post(
        "/manager/users",
        data={
            "entity_type": "user",
            "action": "add",
            "username": "rec9",
            "password": "x-pass-123",
            "full_name": "پذیرش نه",
            "role": "reception",
        },
    )
    assert _q(synthetic_app, "SELECT role FROM users WHERE username='rec9'") == [("reception",)]


def test_adding_doctor_staff_creates_no_login(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    users_before = _q(synthetic_app, "SELECT COUNT(*) FROM users")[0][0]
    c.post(
        "/manager/users",
        data={
            "entity_type": "staff",
            "action": "add",
            "staff_type": "doctor",
            "full_name": "دکتر تازه",
            "username": "drnew",
            "password": "x-pass-123",
        },
    )
    assert _q(synthetic_app, "SELECT staff_type, is_active FROM medical_staff WHERE full_name='دکتر تازه'") == [
        ("doctor", 1)
    ]
    assert _q(synthetic_app, "SELECT COUNT(*) FROM users")[0][0] == users_before


def test_unknown_staff_type_is_rejected(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    c.post("/manager/users", data={"entity_type": "staff", "action": "add", "staff_type": "admin", "full_name": "x"})
    assert _q(synthetic_app, "SELECT 1 FROM medical_staff WHERE full_name='x'") == []


def test_doctors_remain_selectable_for_visits(synthetic_app):
    """Removing the login role must not touch doctors as medical staff."""
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    html = c.get("/reception/").get_data(as_text=True)
    assert "دکتر الف" in html
