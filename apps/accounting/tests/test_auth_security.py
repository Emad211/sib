"""Authentication & session hardening (ACC-1.5..1.7; SEC-01, SEC-03, SEC-04, SEC-07, SEC-09)."""

import sqlite3
from pathlib import Path
from unittest import mock

import bcrypt
import pytest
from conftest import Config, core, login_as

from src.config.settings import DEFAULT_SECRET_KEY
from src.services.auth_service import MIN_PASSWORD_LENGTH, SHORT_PASSWORD_MESSAGE
from tools.make_synthetic_db import TEST_PASSWORD


def _login(client, role, username, password=TEST_PASSWORD):
    field = {"manager": "username", "reception": "reception_username"}.get(role, "reception_username")
    return client.post("/auth/login", data={"role": role, field: username, "password": password})


def _set_active(app, username, active):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.execute("UPDATE users SET is_active = ? WHERE username = ?", (active, username))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- login & session


def test_active_user_can_log_in(synthetic_app):
    r = _login(synthetic_app.test_client(), "reception", "reception1")
    assert r.status_code == 302 and r.headers["Location"].endswith("/reception/")


def test_deactivated_user_cannot_log_in(synthetic_app):
    """SEC-09: is_active was never checked at login."""
    _set_active(synthetic_app, "reception1", 0)
    c = synthetic_app.test_client()
    r = _login(c, "reception", "reception1")
    assert r.status_code == 200  # back on the login form
    with c.session_transaction() as s:
        assert "user_id" not in s


def test_deactivation_ends_an_open_session(synthetic_app):
    """SEC-04: a user disabled by the manager is out on the very next request."""
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    assert c.get("/reception/").status_code == 200
    _set_active(synthetic_app, "reception1", 0)
    r = c.get("/reception/")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]
    with c.session_transaction() as s:
        assert "user_id" not in s


def test_session_of_a_deleted_user_is_cleared(synthetic_app):
    c = synthetic_app.test_client()
    with c.session_transaction() as s:
        s["user_id"], s["role"] = 999_999, "manager"
    r = c.get("/manager/")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


def test_login_session_is_permanent_with_eight_hour_lifetime(synthetic_app):
    c = synthetic_app.test_client()
    r = _login(c, "manager", "manager1")
    cookie = r.headers.get("Set-Cookie", "")
    assert "Expires=" in cookie  # a permanent cookie: PERMANENT_SESSION_LIFETIME applies
    assert synthetic_app.permanent_session_lifetime.total_seconds() == 8 * 3600


# --------------------------------------------------------------------------- per-install secret key


def test_install_secret_is_created_once_and_reused(tmp_path):
    from src.config.settings import load_or_create_install_secret

    first = load_or_create_install_secret(tmp_path)
    assert len(first) == 64 and int(first, 16)
    assert load_or_create_install_secret(tmp_path) == first
    assert (tmp_path / ".hesabdari_secret_key").read_text().strip() == first


def test_install_secret_rejects_a_tampered_short_file(tmp_path):
    from src.config.settings import load_or_create_install_secret

    (tmp_path / ".hesabdari_secret_key").write_text("short")
    with pytest.raises(RuntimeError):
        load_or_create_install_secret(tmp_path)


def _desktop_app(db_path):
    """The exe configuration: not TESTING, not PRODUCTION, default key in Config."""
    from src.app import create_app

    cfg = {k: getattr(Config, k) for k in dir(Config) if k.isupper()}
    cfg.update(
        {
            "TESTING": False,
            "PRODUCTION": False,
            "SECRET_KEY": DEFAULT_SECRET_KEY,
            "DATABASE_PATH": db_path,
            "BACKUP_FOLDER": str(Path(db_path).parent / "backups"),
        }
    )
    with mock.patch("src.services.scheduler.init_scheduler"):
        return create_app(cfg)


def test_desktop_mode_never_runs_with_the_public_default_key(synthetic_app):
    """SEC-01: the default key is public on GitHub; the exe must use its own key."""
    app = _desktop_app(synthetic_app.config["DATABASE_PATH"])
    assert app.config["SECRET_KEY"] != DEFAULT_SECRET_KEY
    assert (Path(synthetic_app.config["DATABASE_PATH"]).parent / ".hesabdari_secret_key").exists()


def test_session_forged_with_the_default_key_is_rejected(synthetic_app):
    from flask.sessions import SecureCookieSessionInterface

    app = _desktop_app(synthetic_app.config["DATABASE_PATH"])
    forger = _desktop_app(synthetic_app.config["DATABASE_PATH"])
    forger.config["SECRET_KEY"] = DEFAULT_SECRET_KEY
    serializer = SecureCookieSessionInterface().get_signing_serializer(forger)
    forged = serializer.dumps({"user_id": 1, "role": "manager"})
    c = app.test_client()
    c.set_cookie("session", forged)
    r = c.get("/manager/")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


# --------------------------------------------------------------------------- reset database


def _manager_client(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def test_reset_requires_the_managers_password(synthetic_app):
    c = _manager_client(synthetic_app)
    r = c.post("/manager/settings/reset-database", data={"confirm_code": "DELETE_ALL_DATA", "password": "wrong"})
    assert r.status_code == 403
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] > 0
    conn.close()


def test_reset_backs_up_first_and_keeps_the_current_manager_not_admin_admin(synthetic_app):
    c = _manager_client(synthetic_app)
    r = c.post("/manager/settings/reset-database", data={"confirm_code": "DELETE_ALL_DATA", "password": TEST_PASSWORD})
    assert r.status_code == 200 and r.get_json()["success"]
    backups = list(Path(synthetic_app.config["BACKUP_FOLDER"]).glob("backup_pre_reset_*.db"))
    assert len(backups) == 1
    saved = sqlite3.connect(backups[0])
    assert saved.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] > 0
    saved.close()
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    users = conn.execute("SELECT username, role FROM users").fetchall()
    assert users == [("manager1", "manager")]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == core.LATEST
    conn.close()
    fresh = synthetic_app.test_client()
    assert _login(fresh, "manager", "manager1").status_code == 302
    assert _login(fresh, "manager", "admin", "admin").status_code == 200  # no admin/admin


def test_reset_is_forbidden_for_other_roles(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    r = c.post("/manager/settings/reset-database", data={"confirm_code": "DELETE_ALL_DATA", "password": TEST_PASSWORD})
    assert r.status_code == 403


def test_install_secret_leaves_no_temp_files(tmp_path):
    from src.config.settings import load_or_create_install_secret

    load_or_create_install_secret(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [".hesabdari_secret_key"]


# --------------------------------------------------------------------------- password policy (SEC-07)


def _sql(app, sql, params=()):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _add_user(client, username, password, full_name="کاربر آزمون"):
    return client.post(
        "/manager/users",
        data={
            "entity_type": "user",
            "action": "add",
            "username": username,
            "password": password,
            "full_name": full_name,
            "role": "reception",
        },
    )


def _update_password(client, user_id, username, new_password):
    return client.post(
        "/manager/users",
        data={
            "entity_type": "user",
            "action": "update",
            "user_id": user_id,
            "username": username,
            "full_name": "پذیرش یک",
            "role": "reception",
            "is_active": "1",
            "new_password": new_password,
        },
    )


def _insert_legacy_user(app, username, password):
    """A row written before the minimum length existed: a hash of a 3-character password."""
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.execute(
        "INSERT INTO users (username, password_hash, role, full_name, is_active) VALUES (?, ?, 'reception', ?, 1)",
        (username, bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)), "رمز کوتاه قدیمی"),
    )
    conn.commit()
    conn.close()


def test_the_minimum_password_length_and_its_message():
    """SEC-07: E02 names the rule but no number, so 8 characters is the floor."""
    assert MIN_PASSWORD_LENGTH == 8
    assert SHORT_PASSWORD_MESSAGE == "رمز عبور باید حداقل ۸ نویسه باشد."


def test_creating_a_user_with_a_short_password_is_refused(synthetic_app):
    """SEC-07: no minimum length was enforced when a user was created."""
    c = _manager_client(synthetic_app)
    assert _add_user(c, "shorty", "a" * (MIN_PASSWORD_LENGTH - 1)).status_code == 302
    assert _sql(synthetic_app, "SELECT COUNT(*) FROM users WHERE username = 'shorty'") == [(0,)]
    with c.session_transaction() as s:
        assert ("error", SHORT_PASSWORD_MESSAGE) in s["_flashes"]


def test_a_password_of_exactly_the_minimum_length_is_accepted(synthetic_app):
    password = "a" * MIN_PASSWORD_LENGTH
    assert _add_user(_manager_client(synthetic_app), "minpass", password).status_code == 302
    assert _sql(synthetic_app, "SELECT COUNT(*) FROM users WHERE username = 'minpass'") == [(1,)]
    r = _login(synthetic_app.test_client(), "reception", "minpass", password)
    assert r.status_code == 302 and r.headers["Location"].endswith("/reception/")


def test_a_short_password_cannot_replace_an_existing_one(synthetic_app):
    """SEC-07: the same floor applies when a password is changed, and nothing else changes."""
    c = _manager_client(synthetic_app)
    uid = _sql(synthetic_app, "SELECT id FROM users WHERE username = 'reception1'")[0][0]
    before = _sql(synthetic_app, "SELECT password_hash FROM users WHERE id = ?", (uid,))
    assert _update_password(c, uid, "reception1", "a" * (MIN_PASSWORD_LENGTH - 1)).status_code == 302
    assert _sql(synthetic_app, "SELECT password_hash FROM users WHERE id = ?", (uid,)) == before
    assert _login(synthetic_app.test_client(), "reception", "reception1").status_code == 302


def test_a_long_enough_new_password_replaces_the_old_one(synthetic_app):
    c = _manager_client(synthetic_app)
    uid = _sql(synthetic_app, "SELECT id FROM users WHERE username = 'reception1'")[0][0]
    new = "b" * MIN_PASSWORD_LENGTH
    assert _update_password(c, uid, "reception1", new).status_code == 302
    assert _login(synthetic_app.test_client(), "reception", "reception1", new).status_code == 302


def test_a_user_with_an_older_short_password_can_still_log_in(synthetic_app):
    """SEC-07: the floor is checked only where a password is written, never at login."""
    _insert_legacy_user(synthetic_app, "legacy_short", "old")
    r = _login(synthetic_app.test_client(), "reception", "legacy_short", "old")
    assert r.status_code == 302 and r.headers["Location"].endswith("/reception/")


def test_register_user_refuses_a_short_password(webapp_db):
    """The create-user CLI (src/app.py) goes through register_user, so it inherits this."""
    from src.services.auth_service import AuthService

    service = AuthService()
    assert service.register_user("cliuser", "a" * (MIN_PASSWORD_LENGTH - 1), "reception", "CLI") is False
    assert webapp_db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert service.register_user("cliuser", "a" * MIN_PASSWORD_LENGTH, "reception", "CLI") is True
