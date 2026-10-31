"""Manager settings: password-gated backup restore/delete (ACC-1.4) and the schedule/threshold form.

Everything runs on the isolated temp DB from `webapp_db`; backups live in a temp folder under
`tmp_path`. The real `clinic_new.db` and the real `backups/` folder are never touched.
"""

import sqlite3
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
