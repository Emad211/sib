"""Delete user = deactivation (#17).

`activity_logs.username` is the join key to `users`, so erasing the users row also erased the
person's history from the activity log. The delete action now sets `is_active = 0`, keeps the
row, keeps the logs, logs the deactivation itself, and the page says «غیرفعال‌سازی».
"""

import sqlite3

from conftest import login_as

from src.services.activity_logger import ACTION_DESCRIPTIONS, ActionCategory, ActionType
from tools.make_synthetic_db import TEST_PASSWORD


def _sql(app, sql, params=()):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _user_id(app, username):
    return _sql(app, "SELECT id FROM users WHERE username = ?", (username,))[0][0]


def _manager(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def _deactivate(client, user_id):
    return client.post("/manager/users", data={"entity_type": "user", "action": "delete", "user_id": user_id})


def _inactive_badges(client):
    """How many rows the users page marks «✗ غیرفعال» (the synthetic DB starts with one)."""
    return client.get("/manager/users").get_data(as_text=True).count("✗ غیرفعال")


def test_deleting_a_user_deactivates_the_row_instead_of_erasing_it(synthetic_app):
    """The users row is the join key for activity_logs: deleting it destroyed the history."""
    uid = _user_id(synthetic_app, "reception1")
    logs_before = _sql(synthetic_app, "SELECT COUNT(*) FROM activity_logs WHERE username = 'reception1'")[0][0]
    assert logs_before > 0  # this person's own synthetic history

    assert _deactivate(_manager(synthetic_app), uid).status_code == 302

    assert _sql(synthetic_app, "SELECT username, is_active FROM users WHERE id = ?", (uid,)) == [("reception1", 0)]
    assert _sql(synthetic_app, "SELECT COUNT(*) FROM activity_logs WHERE username = 'reception1'")[0][0] == logs_before


def test_the_deactivation_is_logged_once_with_the_username(synthetic_app):
    uid = _user_id(synthetic_app, "reception2")
    _deactivate(_manager(synthetic_app), uid)
    assert _sql(
        synthetic_app,
        "SELECT action_type, action_category, target_type, target_name, username FROM activity_logs "
        "WHERE action_type = ?",
        (ActionType.USER_DEACTIVATE,),
    ) == [(ActionType.USER_DEACTIVATE, ActionCategory.SYSTEM, "user", "reception2", "manager1")]


def test_the_deactivation_action_has_a_persian_label():
    assert ActionType.USER_DEACTIVATE == "user_deactivate"
    assert ACTION_DESCRIPTIONS[ActionType.USER_DEACTIVATE] == "غیرفعال‌سازی کاربر"


def test_a_manager_cannot_deactivate_themselves(synthetic_app):
    c = _manager(synthetic_app)
    own = _user_id(synthetic_app, "manager1")
    assert _deactivate(c, own).status_code == 302
    assert _sql(synthetic_app, "SELECT is_active FROM users WHERE id = ?", (own,)) == [(1,)]
    assert _sql(
        synthetic_app, "SELECT COUNT(*) FROM activity_logs WHERE action_type = ?", (ActionType.USER_DEACTIVATE,)
    ) == [(0,)]
    with c.session_transaction() as s:
        assert ("error", "نمی‌توانید حساب کاربری خودتان را حذف کنید.") in s["_flashes"]


def test_deactivating_an_unknown_user_changes_nothing_and_logs_nothing(synthetic_app):
    c = _manager(synthetic_app)
    assert _deactivate(c, 99999).status_code == 302
    assert _sql(
        synthetic_app, "SELECT COUNT(*) FROM activity_logs WHERE action_type = ?", (ActionType.USER_DEACTIVATE,)
    ) == [(0,)]


def test_a_deactivated_user_cannot_log_in(synthetic_app):
    """SEC-09 pinned through this route: deactivation alone locks the account out."""
    uid = _user_id(synthetic_app, "reception1")
    _deactivate(_manager(synthetic_app), uid)
    c = synthetic_app.test_client()
    r = c.post("/auth/login", data={"role": "reception", "reception_username": "reception1", "password": TEST_PASSWORD})
    assert r.status_code == 200  # back on the login form
    with c.session_transaction() as s:
        assert "user_id" not in s


def test_a_deactivated_user_stays_in_the_list_and_can_be_reactivated(synthetic_app):
    c = _manager(synthetic_app)
    uid = _user_id(synthetic_app, "reception1")
    assert _inactive_badges(c) == 1  # only the synthetic reception_old starts inactive

    _deactivate(c, uid)

    assert _inactive_badges(c) == 2  # still listed, now marked inactive
    assert "reception1" in c.get("/manager/users").get_data(as_text=True)
    assert (
        c.post(
            "/manager/users",
            data={
                "entity_type": "user",
                "action": "update",
                "user_id": uid,
                "username": "reception1",
                "full_name": "پذیرش یک",
                "role": "reception",
                "is_active": "1",
            },
        ).status_code
        == 302
    )
    assert _sql(synthetic_app, "SELECT is_active FROM users WHERE id = ?", (uid,)) == [(1,)]
    assert _inactive_badges(c) == 1
    r = synthetic_app.test_client().post(
        "/auth/login", data={"role": "reception", "reception_username": "reception1", "password": TEST_PASSWORD}
    )
    assert r.status_code == 302 and r.headers["Location"].endswith("/reception/")


def test_the_users_page_calls_it_deactivation_not_delete(synthetic_app):
    html = _manager(synthetic_app).get("/manager/users").get_data(as_text=True)
    assert "غیرفعال‌سازی" in html
    assert "آیا از غیرفعال‌سازی این کاربر مطمئن هستید؟" in html
    # the POST contract is unchanged: the form still posts entity_type=user action=delete
    assert '<input type="hidden" name="action" value="delete">' in html
