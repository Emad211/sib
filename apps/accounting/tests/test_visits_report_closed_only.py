"""M1-B1: the visits report and its CSV count only visits of closed invoices.

Every other manager report (dashboard, nursing, procedures, payroll exports) already
filters `invoices.status = 'closed'`; the visits report did not, so open and voided
invoices inflated its rows and its total.
"""

import sqlite3

import jdatetime
import pytest
from conftest import login_as
from flask import template_rendered


def _db(app):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _jalali(iso_date):
    y, m, d = (int(x) for x in iso_date.split("-"))
    j = jdatetime.date.fromgregorian(year=y, month=m, day=d)
    return f"{j.year:04d}/{j.month:02d}/{j.day:02d}"


def _window(conn):
    row = conn.execute(
        "SELECT MIN(v.work_date) lo, MAX(v.work_date) hi FROM visits v "
        "JOIN invoices i ON i.id = v.invoice_id WHERE i.status = 'closed'"
    ).fetchone()
    return f"from={_jalali(row['lo'])}&to={_jalali(row['hi'])}"


def _closed_visit_ids(conn):
    return {r["id"] for r in conn.execute(
        "SELECT v.id FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.status = 'closed'"
    )}


@pytest.fixture()
def app_with_unclosed_visits(synthetic_app):
    """Synthetic DB where two closed invoices with visits are flipped to open and voided."""
    conn = _db(synthetic_app)
    inv_ids = [r["invoice_id"] for r in conn.execute(
        "SELECT DISTINCT v.invoice_id FROM visits v JOIN invoices i ON i.id = v.invoice_id "
        "WHERE i.status = 'closed' ORDER BY v.invoice_id LIMIT 2"
    )]
    assert len(inv_ids) == 2
    conn.execute("UPDATE invoices SET status = 'open' WHERE id = ?", (inv_ids[0],))
    conn.execute("UPDATE invoices SET status = 'voided' WHERE id = ?", (inv_ids[1],))
    conn.commit()
    conn.close()
    return synthetic_app


def test_csv_lists_only_visits_of_closed_invoices(app_with_unclosed_visits):
    app = app_with_unclosed_visits
    conn = _db(app)
    expected = len(_closed_visit_ids(conn))
    window = _window(conn)
    conn.close()
    c = app.test_client()
    login_as(c, app, "manager1")
    body = c.get(f"/manager/export/visits/csv?{window}").get_data(as_text=True)
    rows = [line for line in body.splitlines() if line.strip()]
    assert len(rows) - 1 == expected  # minus the header


def test_report_page_counts_only_visits_of_closed_invoices(app_with_unclosed_visits):
    app = app_with_unclosed_visits
    conn = _db(app)
    ids = _closed_visit_ids(conn)
    window = _window(conn)
    unclosed = {r["id"] for r in conn.execute(
        "SELECT v.id FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.status != 'closed'"
    )}
    price = conn.execute(
        "SELECT COALESCE(SUM(v.price), 0) FROM visits v JOIN invoices i ON i.id = v.invoice_id "
        "WHERE i.status = 'closed' AND v.work_date BETWEEN ? AND ?",
        _iso_bounds(conn),
    ).fetchone()[0]
    conn.close()
    assert unclosed  # the fixture really produced visits on unclosed invoices

    seen = []

    def record(_app, template, context, **kw):
        seen.append(context)

    template_rendered.connect(record, app, weak=False)
    c = app.test_client()
    login_as(c, app, "manager1")
    try:
        r = c.get(f"/manager/reports/visits?{window}")
    finally:
        template_rendered.disconnect(record, app)
    assert r.status_code == 200
    ctx = next(x for x in seen if "total_visits" in x)
    assert ctx["total_visits"] == len(ids)
    assert {v["id"] for v in ctx["visits"]} == ids
    assert ctx["total_amount"] == price


def _iso_bounds(conn):
    row = conn.execute(
        "SELECT MIN(v.work_date), MAX(v.work_date) FROM visits v "
        "JOIN invoices i ON i.id = v.invoice_id WHERE i.status = 'closed'"
    ).fetchone()
    return tuple(row)
