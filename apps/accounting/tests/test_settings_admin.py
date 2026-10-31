"""Manager settings: password-gated backup restore/delete (ACC-1.4) and the schedule/threshold form.

Everything runs on the isolated temp DB from `webapp_db`; backups live in a temp folder under
`tmp_path`. The real `clinic_new.db` and the real `backups/` folder are never touched.
"""

import re
import sqlite3
from datetime import time
from pathlib import Path
from types import SimpleNamespace

import bcrypt
import pytest
from conftest import CsrfClient, login_as
from flask import current_app

from src.services import backup as bk

MANAGER = "settings-manager"
PASSWORD = "t3st-pass"

WRONG_PASSWORD_FLASH = "رمز عبور مدیر نادرست است"

SHIFT_TIME_ERROR = "ساعت شروع شیفت‌ها نامعتبر است"
SHIFT_ORDER_ERROR = "ترتیب ساعت شروع شیفت‌ها"
STALE_THRESHOLD_ERROR = "ساعت فاکتورهای باز باید عددی بین ۱ تا ۷۲ باشد"
ANOMALY_THRESHOLD_ERROR = "آستانهٔ حذف غیرعادی باید عددی بین ۱ تا ۵۰۰ باشد"

VALID_SCHEDULE = {
    "shift_start_morning": "08:00",
    "shift_start_evening": "14:00",
    "shift_start_night": "20:00",
    "stale_open_hours": "8",
    "anomaly_delete_threshold": "30",
}


def _seed_manager(db, username=MANAGER, password=PASSWORD):
    db.execute(
        "INSERT INTO users (username, password_hash, role, full_name, is_active) VALUES (?, ?, 'manager', ?, 1)",
        (username, bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)), "مدیر تنظیمات"),
    )
    db.commit()


@pytest.fixture()
def admin(webapp_db, tmp_path):
    """A signed-in manager on the isolated temp DB, with backups in a temp folder."""
    _seed_manager(webapp_db)
    app = current_app._get_current_object()
    app.config.update(SECRET_KEY="test-only", BACKUP_FOLDER=str(tmp_path / "backups"))
    assert "clinic_new.db" not in Path(app.config["DATABASE_PATH"]).name
    app.test_client_class = CsrfClient
    client = app.test_client()
    login_as(client, app, MANAGER)
    return SimpleNamespace(
        app=app,
        client=client,
        db_path=app.config["DATABASE_PATH"],
        backup_dir=Path(app.config["BACKUP_FOLDER"]),
    )


def _count(db_path, table="patients"):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _make_backup(admin):
    return bk.create_backup(admin.db_path, admin.backup_dir, bk.KIND_MANUAL)


def _settings(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT key, value FROM settings")}
    finally:
        conn.close()


def _put_settings(db_path, **values):
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", list(values.items()))
        conn.commit()
    finally:
        conn.close()


def _form_value(page, name):
    match = re.search(rf'name="{name}"[^>]*\bvalue="([^"]*)"', page)
    assert match, f"no input named {name} with a value"
    return match.group(1)


def _post_settings(admin, **data):
    return admin.client.post("/manager/settings", data={"action": "save_settings", **data})


def _page(admin):
    return admin.client.get("/manager/settings").get_data(as_text=True)


# --------------------------------------------------------------------------- #7 restore/delete password


@pytest.mark.parametrize("password", ["", "wrong-password"])
def test_restore_rejects_a_blank_or_wrong_password(admin, password):
    conn = sqlite3.connect(admin.db_path)
    conn.execute("INSERT INTO patients (name, family_name) VALUES ('قبل', 'از پشتیبان')")
    conn.commit()
    conn.close()
    saved = _make_backup(admin)
    before = _count(admin.db_path)
    # Change the data after the backup: a successful restore would undo this.
    conn = sqlite3.connect(admin.db_path)
    conn.execute("DELETE FROM patients")
    conn.commit()
    conn.close()

    resp = admin.client.post(
        "/manager/settings",
        data={"action": "restore_backup", "backup_name": saved.name, "password": password},
    )

    assert resp.status_code == 302
    assert _count(admin.db_path) == 0  # nothing restored
    assert before == 1
    assert not list(admin.backup_dir.glob("backup_pre_restore_*.db"))  # not even a safety copy
    assert WRONG_PASSWORD_FLASH in _page(admin)


@pytest.mark.parametrize("password", ["", "wrong-password"])
def test_delete_rejects_a_blank_or_wrong_password(admin, password):
    saved = _make_backup(admin)

    resp = admin.client.post(
        "/manager/settings",
        data={"action": "delete_backup", "backup_name": saved.name, "password": password},
    )

    assert resp.status_code == 302
    assert saved.exists()  # nothing deleted
    assert WRONG_PASSWORD_FLASH in _page(admin)


def test_restore_with_the_manager_password_restores_and_keeps_the_safety_copy(admin):
    conn = sqlite3.connect(admin.db_path)
    conn.execute("INSERT INTO patients (name, family_name) VALUES ('قبل', 'از پشتیبان')")
    conn.commit()
    conn.close()
    saved = _make_backup(admin)
    conn = sqlite3.connect(admin.db_path)
    conn.execute("DELETE FROM patients")
    conn.commit()
    conn.close()

    resp = admin.client.post(
        "/manager/settings",
        data={"action": "restore_backup", "backup_name": saved.name, "password": PASSWORD},
    )

    assert resp.status_code == 302
    assert _count(admin.db_path) == 1  # restored
    safety = list(admin.backup_dir.glob("backup_pre_restore_*.db"))
    assert len(safety) == 1  # the pre-restore state was saved first
    conn = sqlite3.connect(safety[0])
    assert conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0
    conn.close()
    assert f"دیتابیس از «{saved.name}» بازگردانی شد" in _page(admin)


def test_delete_with_the_manager_password_deletes_the_backup(admin):
    saved = _make_backup(admin)

    resp = admin.client.post(
        "/manager/settings",
        data={"action": "delete_backup", "backup_name": saved.name, "password": PASSWORD},
    )

    assert resp.status_code == 302
    assert not saved.exists()
    assert f"بکاپ حذف شد: {saved.name}" in _page(admin)


def test_restore_and_delete_forms_ask_for_the_manager_password(admin):
    _make_backup(admin)

    page = _page(admin)

    assert page.count('name="password"') >= 2
    assert "رمز عبور مدیر" in page


# --------------------------------------------------------------------------- #5 schedule + thresholds


def test_settings_page_shows_the_effective_defaults_when_nothing_is_stored(admin):
    page = _page(admin)

    expected = {
        "shift_start_morning": "07:30",
        "shift_start_evening": "13:30",
        "shift_start_night": "19:30",
        "stale_open_hours": "6",
        "anomaly_delete_threshold": "20",
    }
    for key, value in expected.items():
        assert _form_value(page, key) == value


def test_settings_page_shows_stored_values_and_defaults_for_the_rest(admin):
    _put_settings(admin.db_path, shift_start_morning="08:15", stale_open_hours="9")

    page = _page(admin)

    assert _form_value(page, "shift_start_morning") == "08:15"
    assert _form_value(page, "shift_start_evening") == "13:30"
    assert _form_value(page, "shift_start_night") == "19:30"
    assert _form_value(page, "stale_open_hours") == "9"
    assert _form_value(page, "anomaly_delete_threshold") == "20"


def test_settings_page_ignores_a_broken_stored_value(admin):
    _put_settings(admin.db_path, shift_start_evening="25:99", stale_open_hours="abc")

    page = _page(admin)

    assert _form_value(page, "shift_start_evening") == "13:30"
    assert _form_value(page, "stale_open_hours") == "6"


def test_save_settings_persists_shift_starts_and_thresholds(admin):
    resp = _post_settings(admin, section="schedule", **VALID_SCHEDULE)

    assert resp.status_code == 302
    stored = _settings(admin.db_path)
    for key, value in VALID_SCHEDULE.items():
        assert stored[key] == value
    page = _page(admin)
    assert "تنظیمات ذخیره شد" in page
    for key, value in VALID_SCHEDULE.items():
        assert _form_value(page, key) == value

    conn = sqlite3.connect(admin.db_path)
    conn.row_factory = sqlite3.Row
    try:
        from src.services.manager_insights import get_threshold
        from src.services.shift_handover import load_schedule

        assert load_schedule(conn) == {"morning": time(8, 0), "evening": time(14, 0), "night": time(20, 0)}
        assert get_threshold(conn, "stale_open_hours") == 8
        assert get_threshold(conn, "anomaly_delete_threshold") == 30
    finally:
        conn.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("shift_start_morning", "7:30"),
        ("shift_start_morning", "07:3"),
        ("shift_start_morning", ""),
        ("shift_start_evening", "24:00"),
        ("shift_start_night", "12:60"),
        ("shift_start_night", "abc"),
    ],
)
def test_invalid_shift_time_is_rejected_and_nothing_is_saved(admin, field, value):
    _put_settings(admin.db_path, **VALID_SCHEDULE)

    _post_settings(admin, section="schedule", **{**VALID_SCHEDULE, field: value})

    assert _settings(admin.db_path)[field] == VALID_SCHEDULE[field]
    assert SHIFT_TIME_ERROR in _page(admin)


def test_shift_starts_must_be_in_clock_order(admin):
    _put_settings(admin.db_path, **VALID_SCHEDULE)

    _post_settings(admin, section="schedule", **{**VALID_SCHEDULE, "shift_start_morning": "21:00"})

    assert _settings(admin.db_path)["shift_start_morning"] == "08:00"
    assert SHIFT_ORDER_ERROR in _page(admin)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("stale_open_hours", "0", STALE_THRESHOLD_ERROR),
        ("stale_open_hours", "73", STALE_THRESHOLD_ERROR),
        ("stale_open_hours", "abc", STALE_THRESHOLD_ERROR),
        ("stale_open_hours", "1.5", STALE_THRESHOLD_ERROR),
        ("stale_open_hours", "", STALE_THRESHOLD_ERROR),
        ("anomaly_delete_threshold", "0", ANOMALY_THRESHOLD_ERROR),
        ("anomaly_delete_threshold", "501", ANOMALY_THRESHOLD_ERROR),
        ("anomaly_delete_threshold", "abc", ANOMALY_THRESHOLD_ERROR),
    ],
)
def test_invalid_threshold_is_rejected_and_nothing_is_saved(admin, field, value, message):
    _put_settings(admin.db_path, **VALID_SCHEDULE)

    _post_settings(admin, section="schedule", **{**VALID_SCHEDULE, field: value})

    assert _settings(admin.db_path)[field] == VALID_SCHEDULE[field]
    assert message in _page(admin)


@pytest.mark.parametrize(("stale", "anomaly"), [("1", "1"), ("72", "500")])
def test_threshold_bounds_are_accepted(admin, stale, anomaly):
    _post_settings(
        admin,
        section="schedule",
        **{**VALID_SCHEDULE, "stale_open_hours": stale, "anomaly_delete_threshold": anomaly},
    )

    stored = _settings(admin.db_path)
    assert stored["stale_open_hours"] == stale
    assert stored["anomaly_delete_threshold"] == anomaly


def test_saving_clinic_info_keeps_the_schedule_thresholds_and_backup_toggle(admin):
    _put_settings(admin.db_path, **VALID_SCHEDULE, auto_backup="0")

    _post_settings(
        admin,
        section="clinic",
        clinic_name="درمانگاه سیب",
        clinic_phone="021-12345678",
        clinic_address="تهران",
    )

    stored = _settings(admin.db_path)
    for key, value in VALID_SCHEDULE.items():
        assert stored[key] == value
    assert stored["auto_backup"] == "0"
    assert stored["clinic_name"] == "درمانگاه سیب"


def test_saving_the_backup_toggle_keeps_the_schedule_thresholds_and_clinic_info(admin):
    _put_settings(admin.db_path, **VALID_SCHEDULE, clinic_name="درمانگاه سیب")

    _post_settings(admin, section="backup")  # unchecked toggle posts nothing

    stored = _settings(admin.db_path)
    assert stored["auto_backup"] == "0"
    assert stored["clinic_name"] == "درمانگاه سیب"
    for key, value in VALID_SCHEDULE.items():
        assert stored[key] == value

    _post_settings(admin, section="backup", auto_backup="1")
    assert _settings(admin.db_path)["auto_backup"] == "1"


def test_saving_the_schedule_keeps_clinic_info_and_the_backup_toggle(admin):
    _put_settings(admin.db_path, clinic_name="درمانگاه سیب", auto_backup="0")

    _post_settings(admin, section="schedule", **VALID_SCHEDULE)

    stored = _settings(admin.db_path)
    assert stored["clinic_name"] == "درمانگاه سیب"
    assert stored["auto_backup"] == "0"
