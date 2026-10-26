"""Manager dashboard insights (ACC-2.8): exceptions first, recent shifts, system health.

Each rule is pinned on a tiny hand-built database so a threshold or window can never drift
unnoticed. Thresholds come from real production data (docs/accounting/A06_MANAGER_AREA_SPEC.md §6).
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from conftest import login_as

from src.adapters.sqlite.core import ensure_database_ready
from src.services import backup as bk
from src.services import manager_insights as mi

NOW = datetime(2026, 9, 24, 12, 0, 0)
TODAY = "2026-09-24"


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "insights.db")
    ensure_database_ready(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO patients (name, family_name, national_id) VALUES ('الف', 'ب', '0012345678')")
    conn.commit()
    yield conn
    conn.close()


def invoice(db, status="closed", work_date=TODAY, shift="morning", opened_at=None, total=100000, closer="u1"):
    opened_at = opened_at or f"{work_date} 08:00:00"
    cur = db.execute(
        """INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at,
                                 closed_at, opened_by, closed_by)
           VALUES (1, ?, 'آزاد', ?, ?, ?, ?, ?, 'u1', ?)""",
        (
            status,
            total,
            work_date,
            shift,
            opened_at,
            opened_at if status == "closed" else None,
            closer if status == "closed" else None,
        ),
    )
    db.commit()
    return cur.lastrowid


def item(db, inv, table="injections", price=50000, payment="card"):
    if table == "injections":
        cur = db.execute(
            "INSERT INTO injections (patient_id, injection_type, work_date, total_price, invoice_id) VALUES (1, 'x', ?, ?, ?)",
            (TODAY, price, inv),
        )
        kind = "injection"
    elif table == "visits":
        cur = db.execute(
            "INSERT INTO visits (patient_id, doctor_name, work_date, price, invoice_id) VALUES (1, 'د', ?, ?, ?)",
            (TODAY, price, inv),
        )
        kind = "visit"
    elif table == "procedures":
        cur = db.execute(
            "INSERT INTO procedures (patient_id, procedure_type, work_date, price, invoice_id) VALUES (1, 'p', ?, ?, ?)",
            (TODAY, price, inv),
        )
        kind = "procedure"
    else:
        cur = db.execute(
            "INSERT INTO consumables_ledger (patient_id, item_name, work_date, total_cost, invoice_id) VALUES (1, 'c', ?, ?, ?)",
            (TODAY, price, inv),
        )
        kind = "consumable"
    db.execute(
        "INSERT INTO invoice_item_payments (invoice_id, item_type, item_id, payment_type, is_paid) VALUES (?, ?, ?, ?, 1)",
        (inv, kind, cur.lastrowid, payment),
    )
    db.commit()
    return cur.lastrowid


def keys(db, **kw):
    return {a["key"]: a["count"] for a in mi.attention_items(db, now=NOW, **kw)}


# --------------------------------------------------------------------------- attention


def test_nothing_to_report_on_a_clean_database(db):
    assert keys(db) == {}


def test_stale_open_invoice_is_flagged_but_a_fresh_one_is_not(db):
    invoice(db, "open", opened_at="2026-09-24 05:00:00")  # 7 h old
    invoice(db, "open", opened_at="2026-09-24 10:30:00")  # 1.5 h old
    assert keys(db) == {"open_invoices": 1}


def test_stale_threshold_can_be_overridden_in_settings(db):
    invoice(db, "open", opened_at="2026-09-24 10:30:00")
    db.execute("INSERT INTO settings (key, value) VALUES ('stale_open_hours', '1')")
    db.commit()
    assert keys(db) == {"open_invoices": 1}


def test_garbage_threshold_falls_back_to_default(db):
    db.execute("INSERT INTO settings (key, value) VALUES ('stale_open_hours', 'abc')")
    assert mi.get_threshold(db, "stale_open_hours") == mi.DEFAULTS["stale_open_hours"]
    db.execute("UPDATE settings SET value = '-3' WHERE key = 'stale_open_hours'")
    assert mi.get_threshold(db, "stale_open_hours") == mi.DEFAULTS["stale_open_hours"]


def test_unset_payment_only_counts_the_last_seven_work_days(db):
    recent = invoice(db)
    item(db, recent, payment=None)
    old = invoice(db, work_date="2026-09-10")
    item(db, old, payment=None)
    db.execute("UPDATE invoices SET work_date = '2026-09-24' WHERE id = ?", (recent,))
    assert keys(db) == {"unset_payment": 1}
    _title, _note, _headers, rows = mi.attention_detail(db, "unset_payment", NOW)
    assert [r[0] for r in rows] == [recent]


def test_paid_items_are_not_flagged(db):
    item(db, invoice(db), payment="card")
    item(db, invoice(db), payment="cash")
    assert keys(db) == {}


def test_empty_closed_invoice_is_flagged_but_zero_total_with_items_is_not(db):
    empty = invoice(db, total=0)
    with_item = invoice(db, total=0)  # fully insured: zero patient share but real items
    item(db, with_item)
    assert keys(db) == {"empty_closed": 1}
    assert mi.attention_detail(db, "empty_closed", NOW)[3][0][0] == empty


def test_heavy_deleter_threshold_is_twenty_per_user_per_day(db):
    def deletes(user, n, day="2026-09-24"):
        inv = invoice(db, work_date=day)
        item(db, inv)
        for _ in range(n):
            db.execute(
                "INSERT INTO activity_logs (username, action_type, action_category, amount, invoice_id, created_at) VALUES (?, 'injection_delete', 'injection', 1000, ?, ?)",
                (user, inv, f"{day} 09:00:00"),
            )
        db.commit()

    item(db, invoice(db))  # gives the window a reference work date
    deletes("a", 19)
    deletes("b", 20)
    deletes("c", 30, day="2026-08-01")  # outside the 7-day window
    assert keys(db) == {"heavy_deletes": 1}
    rows = mi.attention_detail(db, "heavy_deletes", NOW)[3]
    assert rows == [["b", mi.jalali_date("2026-09-24"), 20, 20000]]


def test_deletes_after_midnight_count_toward_the_same_work_date(db):
    """A night shift that crosses midnight is one work date, not two calendar days."""
    inv = invoice(db, shift="night")
    item(db, inv)
    for hour in ("22:00:00", "23:30:00", "00:30:00", "01:15:00"):
        day = "2026-09-24" if hour >= "22" else "2026-09-25"
        for _ in range(5):
            db.execute(
                "INSERT INTO activity_logs (username, action_type, action_category, invoice_id, created_at) VALUES ('n', 'visit_delete', 'visit', ?, ?)",
                (inv, f"{day} {hour}"),
            )
    db.commit()
    assert keys(db) == {"heavy_deletes": 1}  # 20 deletes on one work date


def test_deletes_by_two_users_are_not_added_together(db):
    item(db, invoice(db))
    for user in ("a", "b"):
        for _ in range(15):
            db.execute(
                "INSERT INTO activity_logs (username, action_type, action_category, created_at) VALUES (?, 'visit_delete', 'visit', '2026-09-24 09:00:00')",
                (user,),
            )
    db.commit()
    assert keys(db) == {}


def test_lock_written_by_the_app_in_iso_t_format_expires_correctly(db):
    """auth_service writes locked_until with isoformat() ('T'); a text compare kept it 'locked' all day."""
    db.execute(
        "INSERT INTO users (username, password_hash, role, is_active, locked_until) VALUES ('t1', 'h', 'reception', 1, '2026-09-24T09:00:00')"
    )
    db.execute(
        "INSERT INTO users (username, password_hash, role, is_active, locked_until) VALUES ('t2', 'h', 'reception', 1, '2026-09-24T12:10:00')"
    )
    db.commit()
    rows = mi.attention_detail(db, "locked_users", NOW)[3]
    assert [r[0] for r in rows] == ["t2"]


def test_a_malformed_work_date_does_not_crash_the_shift_panel(db):
    inv = invoice(db, work_date="2026-09-24T08:00:00")
    item(db, inv)
    assert mi.recent_shifts(db, 1)


def test_locked_account_is_listed_until_the_lock_expires(db):
    db.execute(
        "INSERT INTO users (username, password_hash, role, full_name, is_active, locked_until) VALUES ('x', 'h', 'reception', 'ایکس', 1, '2026-09-24 12:10:00')"
    )
    db.execute(
        "INSERT INTO users (username, password_hash, role, full_name, is_active, locked_until) VALUES ('y', 'h', 'reception', 'ایگرگ', 1, '2026-09-24 11:00:00')"
    )
    db.commit()
    assert keys(db) == {"locked_users": 1}


def test_unknown_attention_key_is_none(db):
    assert mi.attention_detail(db, "nope") is None


# --------------------------------------------------------------------------- backups


def test_backup_warning_missing_old_and_fresh(db, tmp_path):
    backups = str(tmp_path / "bk")
    assert keys(db, backup_dir=backups) == {"backup": 1}  # none at all

    src = tmp_path / "insights.db"
    made = bk.create_backup(str(src), backups, bk.KIND_MANUAL, now=NOW - timedelta(hours=30))
    assert made.exists()
    assert keys(db, backup_dir=backups) == {"backup": 30}  # 30 h old

    bk.create_backup(str(src), backups, bk.KIND_MANUAL, now=NOW - timedelta(hours=2))
    assert keys(db, backup_dir=backups) == {}


def test_system_health_reports_version_size_and_backup_age(db, tmp_path):
    health = mi.system_health(db, str(tmp_path / "insights.db"), str(tmp_path / "none"), "9.9.9", NOW)
    assert health["app_version"] == "9.9.9"
    assert health["schema_version"] >= 2
    assert health["db_size_mb"] is not None and health["backup_age_hours"] is None and not health["backup_ok"]


# --------------------------------------------------------------------------- shifts


def test_recent_shifts_are_ordered_by_work_date_then_shift(db):
    for wd, sh in [
        ("2026-09-23", "night"),
        ("2026-09-24", "morning"),
        ("2026-09-23", "evening"),
        ("2026-09-24", "evening"),
    ]:
        invoice(db, work_date=wd, shift=sh)
    got = [(s["work_date"], s["shift"]) for s in mi.recent_shifts(db, 3)]
    assert got == [("2026-09-24", "evening"), ("2026-09-24", "morning"), ("2026-09-23", "night")]


def test_shift_revenue_excludes_consumables_and_open_invoices(db):
    closed = invoice(db)
    item(db, closed, "visits", 100)
    item(db, closed, "injections", 200)
    item(db, closed, "procedures", 400)
    item(db, closed, "consumables", 99999)
    open_inv = invoice(db, "open")
    item(db, open_inv, "visits", 7777)
    shift = mi.recent_shifts(db, 1)[0]
    assert (shift["visits"], shift["nursing"], shift["procedures"]) == (100, 200, 400)
    assert shift["revenue"] == 700 and shift["consumables"] == 99999
    assert shift["invoices"] == 2 and shift["open"] == 1 and shift["closed"] == 1


def test_baseline_needs_three_of_the_last_four_weeks(db):
    cur = invoice(db)
    item(db, cur, "visits", 1000)
    for weeks_back, price in [(1, 400), (2, 600)]:
        d = (datetime(2026, 9, 24) - timedelta(days=7 * weeks_back)).strftime("%Y-%m-%d")
        item(db, invoice(db, work_date=d), "visits", price)
    assert mi.recent_shifts(db, 1)[0]["vs_baseline"] is None  # only two weeks of history

    d = (datetime(2026, 9, 24) - timedelta(days=21)).strftime("%Y-%m-%d")
    item(db, invoice(db, work_date=d), "visits", 800)
    shift = mi.recent_shifts(db, 1)[0]
    assert shift["baseline"] == pytest.approx(600) and shift["vs_baseline"] == pytest.approx(1000 / 600 - 1)


def test_running_shift_is_labelled_and_not_compared(db):
    for weeks_back in (1, 2, 3):
        d = (datetime(2026, 9, 24) - timedelta(days=7 * weeks_back)).strftime("%Y-%m-%d")
        item(db, invoice(db, work_date=d), "visits", 1000)
    item(db, invoice(db), "visits", 10)
    invoice(db, "open")  # the newest shift still has an open invoice
    running = mi.recent_shifts(db, 1)[0]
    assert running["in_progress"] and running["vs_baseline"] is None and running["baseline"] == 1000


def test_shift_counts_deletes_zero_totals_and_unset_payments(db):
    inv = invoice(db, total=0)
    item(db, inv, payment=None)
    db.execute(
        "INSERT INTO activity_logs (username, action_type, action_category, invoice_id, created_at) VALUES ('u', 'visit_delete', 'visit', ?, '2026-09-24 09:00:00')",
        (inv,),
    )
    db.commit()
    shift = mi.recent_shifts(db, 1)[0]
    assert (shift["deletes"], shift["zero"], shift["unset_payments"]) == (1, 1, 1)


def test_reference_date_is_the_latest_work_date_not_the_calendar(db):
    """After midnight the night shift of yesterday's work date is still running."""
    invoice(db, work_date="2026-09-23", shift="night")
    assert mi.reference_work_date(db) == "2026-09-23"


def test_empty_database_has_no_shifts(db):
    assert mi.recent_shifts(db) == []


# --------------------------------------------------------------------------- routes


def test_dashboard_shows_attention_shifts_and_health(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    r = c.get("/manager/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "نیاز به توجه" in html and "سه شیفت آخر" in html and "نسخهٔ دیتابیس" in html
    assert "بدون دارو و مصرفی" in html


def test_attention_detail_is_manager_only_and_unknown_keys_404(synthetic_app):
    rec = synthetic_app.test_client()
    login_as(rec, synthetic_app, "reception1")
    assert rec.get("/manager/attention/open_invoices").status_code == 302
    mgr = synthetic_app.test_client()
    login_as(mgr, synthetic_app, "manager1")
    assert mgr.get("/manager/attention/open_invoices").status_code == 200
    assert mgr.get("/manager/attention/nope").status_code == 404


@pytest.mark.parametrize("fmt, mime", [("csv", "text/csv"), ("xlsx", "spreadsheetml")])
def test_attention_detail_exports(synthetic_app, fmt, mime):
    mgr = synthetic_app.test_client()
    login_as(mgr, synthetic_app, "manager1")
    r = mgr.get(f"/manager/attention/empty_closed?format={fmt}")
    assert r.status_code == 200 and mime in r.headers["Content-Type"]
    assert r.headers["Content-Disposition"].endswith(f'.{fmt}"')
