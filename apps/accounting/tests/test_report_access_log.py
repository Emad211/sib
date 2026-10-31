"""گزارش‌ها و خروجی‌ها در لاگ فعالیت ثبت می‌شوند (E02، ممیزی #10)."""

import sqlite3

import pytest
from conftest import login_as


def q(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def mgr(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def logs(app, action_type):
    return q(app, "SELECT * FROM activity_logs WHERE action_type = ? ORDER BY id", action_type)


def test_viewing_a_report_lands_in_the_activity_log(synthetic_app):
    client = mgr(synthetic_app)
    assert client.get("/manager/reports/invoices").status_code == 200

    rows = logs(synthetic_app, "report_view")
    assert len(rows) == 1
    row = rows[0]
    assert row["target_name"] == "invoices"
    assert row["action_category"] == "report"
    assert row["username"] == "manager1"
    assert row["patient_id"] is None and row["patient_name"] is None


def test_exporting_a_report_lands_in_the_activity_log(synthetic_app):
    client = mgr(synthetic_app)
    assert client.get("/manager/export/invoices/csv").status_code == 200

    rows = logs(synthetic_app, "report_export")
    assert len(rows) == 1
    assert rows[0]["target_name"] == "invoices"
    assert rows[0]["action_category"] == "report"


def test_a_patient_search_term_is_never_written_to_the_log(synthetic_app):
    client = mgr(synthetic_app)
    assert client.get("/manager/reports/patients?search_name=زهرا").status_code == 200

    row = logs(synthetic_app, "report_view")[0]
    written = " ".join(str(row[key] or "") for key in ("target_name", "new_value", "old_value", "description"))
    assert "زهرا" not in written


def test_a_broken_log_never_fails_the_report(synthetic_app, monkeypatch):
    from src.api.manager import reports as reports_module

    def boom(*args, **kwargs):
        raise RuntimeError("log is down")

    monkeypatch.setattr(reports_module, "log_activity", boom)
    client = mgr(synthetic_app)

    assert client.get("/manager/reports/invoices").status_code == 200
    assert client.get("/manager/export/invoices/csv").status_code == 200
    assert logs(synthetic_app, "report_view") == []


@pytest.mark.parametrize(
    ("url", "action_type", "report"),
    [
        ("/manager/reports/visits", "report_view", "visits"),
        ("/manager/reports/users", "report_view", "users"),
        ("/manager/export/visits/csv", "report_export", "visits"),
        ("/manager/export/patients/csv", "report_export", "patients"),
    ],
)
def test_each_report_and_export_is_named_in_the_log(synthetic_app, url, action_type, report):
    client = mgr(synthetic_app)
    assert client.get(url).status_code == 200
    assert [r["target_name"] for r in logs(synthetic_app, action_type)] == [report]
