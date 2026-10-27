"""Void / refund of a closed invoice (FB-25): kept, audited, gone from revenue."""

import sqlite3

from conftest import login_as


def q(app, sql, *a):
    c = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def mgr(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def closed_invoice(app):
    return q(app, "SELECT id, total_amount FROM invoices WHERE status='closed' AND total_amount > 0 LIMIT 1")[0]


def test_void_keeps_row_audits_and_leaves_revenue(synthetic_app):
    iid, total = closed_invoice(synthetic_app)
    before = q(synthetic_app, "SELECT COALESCE(SUM(total_amount),0) FROM invoices WHERE status='closed'")[0][0]
    c = mgr(synthetic_app)
    c.post(f"/manager/invoice/{iid}/void", data={"kind": "void", "reason": "اشتباه ثبت شد"})
    assert q(synthetic_app, "SELECT status FROM invoices WHERE id=?", iid) == [("voided",)]
    assert q(synthetic_app, "SELECT kind, amount, reason, voided_by FROM invoice_voids") == [
        ("void", total, "اشتباه ثبت شد", "manager1")
    ]
    after = q(synthetic_app, "SELECT COALESCE(SUM(total_amount),0) FROM invoices WHERE status='closed'")[0][0]
    assert after == before - total
    assert q(synthetic_app, "SELECT COUNT(*) FROM activity_logs WHERE action_type='invoice_void' AND invoice_id=?", iid) == [(1,)]
    assert "باطل‌شده" in c.get(f"/manager/invoice/{iid}/history").get_data(as_text=True)


def test_refund_needs_method_and_reason_and_cannot_repeat(synthetic_app):
    iid, _ = closed_invoice(synthetic_app)
    c = mgr(synthetic_app)
    c.post(f"/manager/invoice/{iid}/void", data={"kind": "refund", "reason": "x"})
    c.post(f"/manager/invoice/{iid}/void", data={"kind": "void", "reason": ""})
    assert q(synthetic_app, "SELECT COUNT(*) FROM invoice_voids") == [(0,)]
    c.post(f"/manager/invoice/{iid}/void", data={"kind": "refund", "refund_method": "card", "reason": "انصراف"})
    c.post(f"/manager/invoice/{iid}/void", data={"kind": "void", "reason": "دوباره"})
    assert q(synthetic_app, "SELECT kind, refund_method FROM invoice_voids") == [("refund", "card")]


def test_reception_cannot_void(synthetic_app):
    iid, _ = closed_invoice(synthetic_app)
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    c.post(f"/manager/invoice/{iid}/void", data={"kind": "void", "reason": "x"})
    assert q(synthetic_app, "SELECT status FROM invoices WHERE id=?", iid) == [("closed",)]


def test_consumables_summary_groups_item_shift_receptionist(synthetic_app):
    c = mgr(synthetic_app)
    html = c.get("/manager/reports/consumables?from=1404/01/01&to=1405/12/29").get_data(as_text=True)
    assert "خلاصه: هر قلم به تفکیک شیفت و پذیرش" in html


def test_staff_performance_page_matches_db(synthetic_app):
    c = mgr(synthetic_app)
    html = c.get("/manager/reports/staff?from=1404/01/01&to=1405/12/29")
    assert html.status_code == 200
    text = html.get_data(as_text=True)
    assert "عملکرد پرسنل" in text and "پذیرش" in text
    visits = q(synthetic_app, "SELECT COUNT(*) FROM visits v JOIN invoices i ON i.id=v.invoice_id AND i.status='closed'")[0][0]
    from src.services import staff_performance as perf

    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    rec = perf.receptionists(conn, "2000-01-01", "2100-01-01")
    conn.close()
    assert sum(r["visits"] for r in rec) == visits
