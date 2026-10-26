"""Manager dashboard money tiles, expenses ledger and chart series (FB-20 to FB-23)."""

import sqlite3
from datetime import date

import pytest
from conftest import login_as

from src.services import manager_finance as finance


def q(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def run(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def mgr(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def today_fa():
    from src.common.utils import iran_now
    from src.domain import jalali

    return jalali.jalali_text(iran_now().date())


# --------------------------------------------------------------------------- expenses page


def test_an_expense_is_recorded_listed_and_audited(synthetic_app):
    c = mgr(synthetic_app)
    r = c.post(
        "/manager/expenses",
        data={
            "action": "add",
            "spent_on": today_fa(),
            "category": "اجاره",
            "amount": "۲٬۵۰۰٬۰۰۰",
            "note": "اجارهٔ  آبان",
        },
        follow_redirects=True,
    )
    assert "هزینه ثبت شد" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT category, amount, note, created_by, is_void FROM expenses") == [
        ("اجاره", 2500000.0, "اجارهٔ آبان", "manager1", 0)
    ]
    html = c.get("/manager/expenses").get_data(as_text=True)
    assert "2,500,000" in html and "اجاره: 2,500,000" in html
    assert q(synthetic_app, "SELECT action_type, amount FROM activity_logs WHERE action_type = 'expense_add'") == [
        ("expense_add", 2500000.0)
    ]


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("amount", "abc", "مبلغ نامعتبر"),
        ("amount", "0", "مبلغ نامعتبر"),
        ("spent_on", "1405/13/01", "تاریخ نامعتبر"),
        ("spent_on", "1499/01/01", "تاریخ آینده"),
        ("category", "خرید ماشین", "دسته‌بندی"),
    ],
)
def test_bad_expenses_are_refused(synthetic_app, field, value, message):
    data = {"action": "add", "spent_on": today_fa(), "category": "اجاره", "amount": "1000"} | {field: value}
    r = mgr(synthetic_app).post("/manager/expenses", data=data, follow_redirects=True)
    assert message in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT COUNT(*) FROM expenses") == [(0,)]


def test_an_expense_is_voided_with_a_reason_never_deleted(synthetic_app):
    c = mgr(synthetic_app)
    c.post("/manager/expenses", data={"action": "add", "spent_on": today_fa(), "category": "سایر", "amount": "1000"})
    eid = q(synthetic_app, "SELECT id FROM expenses")[0][0]
    r = c.post("/manager/expenses", data={"action": "void", "expense_id": eid, "reason": ""}, follow_redirects=True)
    assert "دلیل ابطال" in r.get_data(as_text=True) and q(synthetic_app, "SELECT is_void FROM expenses") == [(0,)]
    c.post("/manager/expenses", data={"action": "void", "expense_id": eid, "reason": "تکراری"})
    assert q(synthetic_app, "SELECT is_void, void_reason, voided_by FROM expenses") == [(1, "تکراری", "manager1")]
    again = c.post(
        "/manager/expenses", data={"action": "void", "expense_id": eid, "reason": "x"}, follow_redirects=True
    )
    assert "قبلاً باطل" in again.get_data(as_text=True)


def test_months_can_be_browsed(synthetic_app):
    run(synthetic_app, "INSERT INTO expenses (spent_on, category, amount) VALUES ('2026-09-24', 'اجاره', 777)")
    html = mgr(synthetic_app).get("/manager/expenses?month=1405/07").get_data(as_text=True)
    assert "مهر 1405" in html and "777" in html
    assert "777" not in mgr(synthetic_app).get("/manager/expenses?month=1405/06").get_data(as_text=True)


def test_reception_cannot_see_or_add_expenses(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    assert c.get("/manager/expenses").status_code == 302
    c.post("/manager/expenses", data={"action": "add", "spent_on": today_fa(), "category": "اجاره", "amount": "1"})
    assert q(synthetic_app, "SELECT COUNT(*) FROM expenses") == [(0,)]


# --------------------------------------------------------------------------- month tiles


def test_month_tiles_add_up(synthetic_app):
    run(synthetic_app, "INSERT INTO expenses (spent_on, category, amount) VALUES ('2026-09-23', 'اجاره', 1000)")
    run(
        synthetic_app,
        "INSERT INTO expenses (spent_on, category, amount, is_void) VALUES ('2026-09-23', 'سایر', 99999, 1)",
    )  # voided
    run(
        synthetic_app, "INSERT INTO expenses (spent_on, category, amount) VALUES ('2026-09-22', 'سایر', 55555)"
    )  # previous month
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    with synthetic_app.app_context():
        tiles = finance.month_tiles(conn, date(2026, 9, 24))  # 1405/07/02
    expected_paid = (
        conn.execute(
            "SELECT SUM(total_amount) FROM invoices WHERE status='closed' AND work_date BETWEEN '2026-09-23' AND '2026-09-24'"
        ).fetchone()[0]
        or 0
    )
    conn.close()
    assert tiles["month"] == "مهر 1405"
    assert tiles["transactions"] == pytest.approx(expected_paid)
    assert tiles["expenses"] == 1000
    assert tiles["balance"] == pytest.approx(tiles["transactions"] - 1000 - tiles["salaries"])


def test_dashboard_shows_the_new_tiles_and_not_the_old_ones(synthetic_app):
    html = mgr(synthetic_app).get("/manager/").get_data(as_text=True)
    assert (
        "تراکنش از اول" in html
        and "هزینه‌های درمانگاه از اول ماه" in html
        and "حقوق پزشکان و پرستاران" in html
        and "مانده:" in html
    )
    assert (
        "فاکتور امروز" not in html and "کادر درمان" not in html and 'class="bottom-grid"' not in html
    )  # FB-20 / FB-23
    assert 'value="transactions"' in html and 'value="salaries"' in html and "/manager/expenses" in html


# --------------------------------------------------------------------------- chart


def test_chart_series_for_money(synthetic_app):
    run(synthetic_app, "INSERT INTO expenses (spent_on, category, amount) VALUES ('2026-09-24', 'اجاره', 4321)")
    c = mgr(synthetic_app)
    exp = c.get("/manager/api/chart-data?from=1405/07/01&to=1405/07/02&type=expenses").get_json()
    assert exp["values"] == [0, 4321]
    tx = c.get("/manager/api/chart-data?from=1405/07/02&to=1405/07/02&type=transactions").get_json()
    paid = q(
        synthetic_app,
        "SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE status='closed' AND work_date='2026-09-24'",
    )[0][0]
    assert tx["values"] == [pytest.approx(paid)]
    sal = c.get("/manager/api/chart-data?from=1405/07/02&to=1405/07/02&type=salaries").get_json()
    assert len(sal["values"]) == 1 and sal["values"][0] >= 0


def test_services_breakdown_is_in_toman(synthetic_app):
    data = mgr(synthetic_app).get("/manager/api/chart-data?from=1405/07/02&to=1405/07/02&type=services").get_json()
    visits = q(
        synthetic_app,
        "SELECT COALESCE(SUM(v.price), 0) FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.status='closed' AND i.work_date='2026-09-24'",
    )[0][0]
    assert data["datasets"]["visits"] == [pytest.approx(visits)]
    assert visits > 1000  # an amount, not a count
