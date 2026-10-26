"""CSRF protection (SEC-05, ACC-2.1)."""

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from conftest import login_as

APP_ROOT = Path(__file__).resolve().parent.parent


def _manager(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def _count(app, sql):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_post_without_token_is_rejected_and_changes_nothing(synthetic_app):
    c = _manager(synthetic_app)
    before = (
        len(list(Path(synthetic_app.config["BACKUP_FOLDER"]).glob("*.db")))
        if Path(synthetic_app.config["BACKUP_FOLDER"]).exists()
        else 0
    )
    r = c.post("/manager/settings", data={"action": "create_backup"}, csrf=False)
    assert r.status_code == 400 and "درخواست نامعتبر" in r.get_data(as_text=True)
    after = (
        len(list(Path(synthetic_app.config["BACKUP_FOLDER"]).glob("*.db")))
        if Path(synthetic_app.config["BACKUP_FOLDER"]).exists()
        else 0
    )
    assert after == before


def test_wrong_token_is_rejected(synthetic_app):
    c = _manager(synthetic_app)
    with c.session_transaction() as s:
        s["_csrf"] = "the-real-token"
    r = c.post("/manager/settings", data={"action": "create_backup", "csrf_token": "forged"}, csrf=False)
    assert r.status_code == 400


def test_json_callers_get_a_json_error(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    n = _count(synthetic_app, "SELECT COUNT(*) FROM invoices")
    r = c.post("/reception/invoice/close", data={"invoice_id": 1}, headers={"X-CSRFToken": "nope"}, csrf=False)
    assert r.status_code == 400 and r.is_json and r.get_json()["success"] is False
    assert _count(synthetic_app, "SELECT COUNT(*) FROM invoices") == n


def test_form_field_token_is_accepted(synthetic_app):
    c = _manager(synthetic_app)
    with c.session_transaction() as s:
        s["_csrf"] = "tok"
    r = c.post("/manager/settings", data={"action": "create_backup", "csrf_token": "tok"}, csrf=False)
    assert r.status_code == 302


def test_login_itself_is_protected(synthetic_app):
    c = synthetic_app.test_client()
    r = c.post("/auth/login", data={"role": "manager", "username": "manager1", "password": "x"}, csrf=False)
    assert r.status_code == 400


def test_every_html_page_carries_the_token_and_script(synthetic_app):
    c = _manager(synthetic_app)
    for url in ("/auth/login", "/manager/", "/manager/settings", "/reception/", "/manager/does-not-exist"):
        html = c.get(url).get_data(as_text=True)
        m = re.search(r'<meta name="csrf-token" content="([^"]+)">', html)
        assert m, url
        assert "js/csrf.js" in html, url
        with c.session_transaction() as s:
            assert s["_csrf"] == m.group(1)


def test_csv_and_json_responses_are_not_touched(synthetic_app):
    c = _manager(synthetic_app)
    r = c.get("/manager/export/patients/csv")
    assert "csrf-token" not in r.get_data(as_text=True)
    assert "csrf-token" not in c.get("/reception/api/shift/status").get_data(as_text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_csrf_js_parses():
    r = subprocess.run(
        [shutil.which("node"), "--check", str(APP_ROOT / "src/static/js/csrf.js")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
