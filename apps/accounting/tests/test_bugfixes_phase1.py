"""Regression tests for BUG-01..04 (ACC-1.9). Each failed with HTTP 500 before the fix."""

import csv
import io
import sqlite3

from conftest import login_as

# The synthetic DB covers the 14 days ending 2026-09-24 (= 1405/07/02).
RANGE = "from=1405/06/19&to=1405/07/02"
FIRST_DAY, LAST_DAY = "2026-09-11", "2026-09-24"


def _manager(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def _csv_rows(resp):
    text = resp.get_data(as_text=True).lstrip("﻿")
    return list(csv.reader(io.StringIO(text)))


def _q(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def test_bug01_insurance_arrears_export_lists_covered_nursing_and_skips_exclusions(synthetic_app):
    r = _manager(synthetic_app).get(f"/manager/insurance_arrears/export?{RANGE}")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    rows = _csv_rows(r)[1:]
    nursing = [row for row in rows if row[4] != "ویزیت"]
    assert nursing, "covered nursing services must appear"
    covered = {"تامین اجتماعی", "خدمات درمانی", "نیروهای مسلح"}
    assert {row[3] for row in nursing} <= covered
    # «نوار قلب» is excluded for تامین اجتماعی in the synthetic catalogue
    assert not [row for row in nursing if row[3] == "تامین اجتماعی" and row[4] == "نوار قلب"]
    expected = _q(
        synthetic_app,
        """SELECT COUNT(*) FROM injections i JOIN invoices inv ON inv.id = i.invoice_id
           JOIN visit_tariffs t ON t.insurance_type = inv.insurance_type AND t.nursing_covers = 1
           WHERE i.work_date BETWEEN ? AND ?
             AND NOT EXISTS (SELECT 1 FROM insurance_nursing_exclusions e
                             WHERE e.insurance_type = inv.insurance_type AND e.nursing_service_id = i.service_id)""",
        FIRST_DAY,
        LAST_DAY,
    )
    assert len(nursing) == expected


def test_bug04_invoices_export_has_one_row_per_invoice(synthetic_app):
    r = _manager(synthetic_app).get(f"/manager/export/invoices/csv?{RANGE}")
    assert r.status_code == 200
    rows = _csv_rows(r)
    assert rows[0][0] == "شماره فاکتور"
    expected = _q(synthetic_app, "SELECT COUNT(*) FROM invoices WHERE work_date BETWEEN ? AND ?", FIRST_DAY, LAST_DAY)
    assert len(rows) - 1 == expected
    total = _q(
        synthetic_app,
        "SELECT CAST(SUM(total_amount) AS INT) FROM invoices WHERE work_date BETWEEN ? AND ?",
        FIRST_DAY,
        LAST_DAY,
    )
    assert sum(int(row[4]) for row in rows[1:]) == total
    assert all(row[7] for row in rows[1:])  # «پذیرش» column filled


def test_bug02_staff_page_leads_to_users_page(synthetic_app):
    r = _manager(synthetic_app).get("/manager/staff")
    assert r.status_code == 302 and r.headers["Location"].endswith("/manager/users")


def test_bug03_my_shifts_leads_to_shift_performance_keeping_the_selection(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    r = c.get("/reception/my_shifts?work_date=2026-09-20&shift=night&evil=1")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "/reception/shift_performance" in loc and "work_date=2026-09-20" in loc and "shift=night" in loc
    assert "evil" not in loc
    assert c.get(loc).status_code == 200
