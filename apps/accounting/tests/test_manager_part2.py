"""Manager area part 2 (ACC-2.8): tariff history, unlock/last login, invoice history, duplicates."""

import sqlite3
from datetime import datetime

import pytest
from conftest import login_as
from test_manager_insights import NOW, TODAY, invoice, item, keys  # noqa: F401  (db fixture re-exported below)
from test_manager_insights import db as db  # noqa: F401,PLC0414

from src.adapters.sqlite.migrations import LATEST
from src.services import manager_insights as mi
from src.services import tariff_audit

# --------------------------------------------------------------------------- migration 0003


def test_schema_version_is_three_and_the_history_table_exists(db):
    assert LATEST == 3
    assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    cols = {r[1] for r in db.execute("PRAGMA table_info(tariff_changes)")}
    assert {"changed_at", "username", "kind", "tariff_id", "label", "action", "field", "old_value", "new_value"} <= cols


# --------------------------------------------------------------------------- tariff history


def q(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def mgr(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def changes(app):
    return q(app, "SELECT username, kind, label, action, field, old_value, new_value FROM tariff_changes ORDER BY id")


def test_nursing_tariff_add_update_delete_are_recorded(synthetic_app):
    c = mgr(synthetic_app)
    c.post("/manager/tariffs/nursing", data={"action": "add", "service_name": "سرم تست", "unit_price": "100,000"})
    sid = q(synthetic_app, "SELECT id FROM nursing_services WHERE service_name='سرم تست'")[0][0]
    c.post(
        "/manager/tariffs/nursing",
        data={
            "action": "update",
            "service_id": sid,
            "service_name": "سرم تست",
            "unit_price": "120000",
            "is_active": "1",
        },
    )
    c.post("/manager/tariffs/nursing", data={"action": "delete", "service_id": sid})
    rows = changes(synthetic_app)
    assert [r[3] for r in rows] == ["create", "update", "delete"]
    assert rows[0][:3] == ("manager1", "nursing", "سرم تست")
    assert rows[1][4:] == ("قیمت", "100,000", "120,000")
    assert "قیمت: 120,000" in rows[2][5]


def test_an_update_that_changes_nothing_records_nothing(synthetic_app):
    c = mgr(synthetic_app)
    sid, name, price = q(
        synthetic_app, "SELECT id, service_name, unit_price FROM nursing_services WHERE is_active=1 LIMIT 1"
    )[0]
    c.post(
        "/manager/tariffs/nursing",
        data={"action": "update", "service_id": sid, "service_name": name, "unit_price": str(price), "is_active": "1"},
    )
    assert changes(synthetic_app) == []


def test_only_changed_fields_are_recorded_one_row_each(synthetic_app):
    c = mgr(synthetic_app)
    sid, name, price = q(
        synthetic_app, "SELECT id, service_name, unit_price FROM nursing_services WHERE is_active=1 LIMIT 1"
    )[0]
    c.post(
        "/manager/tariffs/nursing",
        data={"action": "update", "service_id": sid, "service_name": name + " ب", "unit_price": str(price + 5000)},
    )
    rows = changes(synthetic_app)
    assert sorted(r[4] for r in rows) == ["قیمت", "نام", "وضعیت"]
    assert next(r for r in rows if r[4] == "وضعیت")[5:] == ("فعال", "غیرفعال")


def test_insurance_base_tariff_and_update_are_recorded(synthetic_app):
    c = mgr(synthetic_app)
    old = q(synthetic_app, "SELECT tariff_price FROM visit_tariffs WHERE is_base_tariff = 1")[0][0]
    c.post("/manager/tariffs/insurance", data={"action": "set_base", "base_visit_price": str(int(old) + 1000)})
    row = changes(synthetic_app)[-1]
    assert row[1] == "insurance" and row[4] == "قیمت" and row[6] == f"{int(old) + 1000:,}"


def test_consumable_changes_are_recorded(synthetic_app):
    c = mgr(synthetic_app)
    c.post(
        "/manager/tariffs/consumables",
        data={"action": "add", "name": "گاز تست", "default_price": "5000", "category": "supply"},
    )
    iid = q(synthetic_app, "SELECT id FROM consumable_tariffs WHERE name='گاز تست'")[0][0]
    c.post(
        "/manager/tariffs/consumables",
        data={
            "action": "update",
            "item_id": iid,
            "name": "گاز تست",
            "default_price": "6000",
            "category": "drug",
            "is_active": "1",
        },
    )
    fields = {r[4]: (r[5], r[6]) for r in changes(synthetic_app) if r[3] == "update"}
    assert fields == {"قیمت": ("5,000", "6,000"), "دسته": ("لوازم", "دارو")}


def test_nursing_exclusion_changes_name_the_services(synthetic_app):
    c = mgr(synthetic_app)
    sid, name = q(synthetic_app, "SELECT id, service_name FROM nursing_services LIMIT 1")[0]
    c.post("/manager/insurance/تامین اجتماعی/nursing_exclusions", json={"service_ids": []})  # start clean
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    conn.execute("DELETE FROM tariff_changes")
    conn.commit()
    conn.close()
    c.post("/manager/insurance/تامین اجتماعی/nursing_exclusions", json={"service_ids": [sid]})
    c.post("/manager/insurance/تامین اجتماعی/nursing_exclusions", json={"service_ids": []})
    rows = [r for r in changes(synthetic_app) if r[1] == "exclusion"]
    assert [(r[2], r[4], r[6]) for r in rows] == [
        ("تامین اجتماعی", "استثنا شد", name),
        ("تامین اجتماعی", "از استثنا خارج شد", name),
    ]


def test_history_is_written_in_the_same_transaction_as_the_change(synthetic_app):
    """If recording fails the price must not change either (no silent price edits)."""
    from unittest import mock

    c = mgr(synthetic_app)
    sid, name, price = q(synthetic_app, "SELECT id, service_name, unit_price FROM nursing_services LIMIT 1")[0]
    with mock.patch.object(tariff_audit, "_insert", side_effect=RuntimeError("disk")):
        synthetic_app.config["PROPAGATE_EXCEPTIONS"] = False
        r = c.post(
            "/manager/tariffs/nursing",
            data={
                "action": "update",
                "service_id": sid,
                "service_name": name,
                "unit_price": str(price + 1),
                "is_active": "1",
            },
        )
    assert r.status_code == 500
    assert q(synthetic_app, "SELECT unit_price FROM nursing_services WHERE id=?", sid)[0][0] == price


def test_history_page_filters_and_exports(synthetic_app):
    c = mgr(synthetic_app)
    c.post(
        "/manager/tariffs/consumables",
        data={"action": "add", "name": "الف تست", "default_price": "1000", "category": "supply"},
    )
    c.post("/manager/tariffs/nursing", data={"action": "add", "service_name": "ب تست", "unit_price": "2000"})
    html = c.get("/manager/tariffs/history").get_data(as_text=True)
    assert "الف تست" in html and "ب تست" in html and "manager1" in html
    only = c.get("/manager/tariffs/history?kind=consumable").get_data(as_text=True)
    assert "الف تست" in only and "ب تست" not in only
    assert c.get("/manager/tariffs/history?kind=bogus").status_code == 200  # unknown kind = no filter
    xlsx = c.get("/manager/tariffs/history?format=xlsx")
    assert "spreadsheetml" in xlsx.headers["Content-Type"] and xlsx.headers["Content-Disposition"].startswith(
        "attachment"
    )


def test_history_is_manager_only_and_linked_from_the_hub(synthetic_app):
    rec = synthetic_app.test_client()
    login_as(rec, synthetic_app, "reception1")
    assert rec.get("/manager/tariffs/history").status_code == 302
    assert "/manager/tariffs/history" in mgr(synthetic_app).get("/manager/tariffs").get_data(as_text=True)


def test_a_change_in_the_last_day_is_an_attention_item(db):
    db.execute(
        "INSERT INTO tariff_changes (changed_at, username, kind, label, action, field, old_value, new_value) VALUES ('2026-09-24 09:00:00', 'm', 'nursing', 'سرم', 'update', 'قیمت', '1', '2')"
    )
    db.execute(
        "INSERT INTO tariff_changes (changed_at, username, kind, label, action, field, old_value, new_value) VALUES ('2026-09-22 09:00:00', 'm', 'nursing', 'سرم', 'update', 'قیمت', '0', '1')"
    )
    db.commit()
    assert keys(db) == {"tariff_changes": 1}
    row = mi.attention_detail(db, "tariff_changes", NOW)[3][0]
    assert row[1] == "m" and row[5:] == ["1", "2"]


# --------------------------------------------------------------------------- duplicate invoices


def at(db, inv, hhmm):
    db.execute("UPDATE invoices SET opened_at = ? WHERE id = ?", (f"{TODAY} {hhmm}:00", inv))
    db.commit()


def test_two_invoices_with_items_within_ten_minutes_are_flagged(db):
    a, b = invoice(db), invoice(db)
    item(db, a), item(db, b)
    at(db, a, "08:00"), at(db, b, "08:09")
    assert keys(db) == {"duplicate_invoices": 1}
    row = mi.attention_detail(db, "duplicate_invoices", NOW)[3][0]
    assert row[:2] == [a, b] and row[4] == 9


@pytest.mark.parametrize(
    "case",
    ["eleven minutes apart", "different shift", "one is empty"],
)
def test_near_misses_are_not_flagged(db, case):
    a = invoice(db)
    b = invoice(db, shift="evening" if case == "different shift" else "morning")
    item(db, a)
    if case != "one is empty":
        item(db, b)
    at(db, a, "08:00")
    at(db, b, "08:11" if case == "eleven minutes apart" else "08:05")
    assert "duplicate_invoices" not in keys(db)


# --------------------------------------------------------------------------- users


def set_user(app, username, **cols):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.execute(
        f"UPDATE users SET {', '.join(f'{k} = ?' for k in cols)} WHERE username = ?", [*cols.values(), username]
    )
    conn.commit()
    conn.close()


def test_locked_user_is_shown_and_can_be_unlocked_with_an_audit_row(synthetic_app):
    future = "2099-01-01T00:00:00"
    set_user(synthetic_app, "reception2", locked_until=future, failed_attempts=3)
    c = mgr(synthetic_app)
    html = c.get("/manager/users").get_data(as_text=True)
    assert "🔒 قفل" in html and 'value="unlock"' in html
    uid = q(synthetic_app, "SELECT id FROM users WHERE username='reception2'")[0][0]
    r = c.post("/manager/users", data={"entity_type": "user", "action": "unlock", "user_id": uid})
    assert r.status_code == 302
    assert q(synthetic_app, "SELECT locked_until, failed_attempts FROM users WHERE id=?", uid) == [(None, 0)]
    assert q(synthetic_app, "SELECT username, target_name FROM activity_logs WHERE action_type='user_unlock'") == [
        ("manager1", "reception2")
    ]
    assert "🔒 قفل" not in c.get("/manager/users").get_data(as_text=True)


def test_an_expired_lock_is_not_shown_as_locked(synthetic_app):
    set_user(synthetic_app, "reception2", locked_until="2020-01-01T00:00:00")
    assert "🔒 قفل" not in mgr(synthetic_app).get("/manager/users").get_data(as_text=True)


def test_unlocking_an_unknown_user_changes_nothing(synthetic_app):
    c = mgr(synthetic_app)
    r = c.post("/manager/users", data={"entity_type": "user", "action": "unlock", "user_id": 99999})
    assert r.status_code == 302
    assert q(synthetic_app, "SELECT COUNT(*) FROM activity_logs WHERE action_type='user_unlock'") == [(0,)]


def test_reception_cannot_unlock_anyone(synthetic_app):
    set_user(synthetic_app, "reception2", locked_until="2099-01-01T00:00:00")
    rec = synthetic_app.test_client()
    login_as(rec, synthetic_app, "reception1")
    uid = q(synthetic_app, "SELECT id FROM users WHERE username='reception2'")[0][0]
    rec.post("/manager/users", data={"entity_type": "user", "action": "unlock", "user_id": uid})
    assert q(synthetic_app, "SELECT locked_until FROM users WHERE id=?", uid)[0][0] is not None


def test_users_page_shows_last_login_and_recent_activity(synthetic_app):
    set_user(synthetic_app, "reception1", last_login="2026-09-20 10:30:00")
    html = mgr(synthetic_app).get("/manager/users").get_data(as_text=True)
    assert "1405/06/29 10:30" in html and "آخرین ورود" in html and "فعالیت ۳۰ روز" in html


def test_names_with_quotes_cannot_break_out_of_the_inline_handlers(synthetic_app):
    """The name used to be pasted into a JS string inside onclick (an apostrophe ended the string)."""
    import re

    set_user(synthetic_app, "reception2", full_name="x');alert(1);//")
    html = mgr(synthetic_app).get("/manager/users").get_data(as_text=True)
    handlers = re.findall(r"onclick='openUserModal\(([^']*)'", html)
    mine = [h for h in handlers if "alert" in h]
    assert len(mine) == 1
    assert "&#39;" not in mine[0] and r"\u0027" in mine[0]  # JSON-escaped, so it stays inside the string


# --------------------------------------------------------------------------- invoice history


def make_invoice_with_events(app):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    pid = conn.execute("SELECT id FROM patients LIMIT 1").fetchone()[0]
    inv = conn.execute(
        "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, opened_by)"
        " VALUES (?, 'open', 'آزاد', 0, '2026-09-24', 'morning', '2026-09-24 08:00:00', 'reception1')",
        (pid,),
    ).lastrowid
    conn.execute(
        "INSERT INTO injections (patient_id, injection_type, work_date, total_price, invoice_id) VALUES (?, 'سرم', '2026-09-24', 50000, ?)",
        (pid, inv),
    )
    for ts, action, desc, amount in [
        ("2026-09-24 08:05:00", "injection_add", "افزودن تزریق", 50000),
        ("2026-09-24 08:20:00", "injection_delete", "حذف تزریق", 50000),
        ("2026-09-24 08:01:00", "invoice_create", "ایجاد فاکتور", 0),
    ]:
        conn.execute(
            "INSERT INTO activity_logs (username, action_type, action_category, description, amount, invoice_id, created_at) VALUES ('reception1', ?, 'invoice', ?, ?, ?, ?)",
            (action, desc, amount, inv, ts),
        )
    conn.commit()
    conn.close()
    return inv


def test_invoice_history_shows_facts_items_and_events_in_time_order(synthetic_app):
    inv = make_invoice_with_events(synthetic_app)
    r = mgr(synthetic_app).get(f"/manager/invoice/{inv}/history")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and f"تاریخچهٔ فاکتور {inv}" in html and "سرم" in html and "50,000" in html
    assert html.index("ایجاد فاکتور") < html.index("افزودن تزریق") < html.index("حذف تزریق")


def test_invoice_history_404_and_manager_only(synthetic_app):
    inv = make_invoice_with_events(synthetic_app)
    assert mgr(synthetic_app).get("/manager/invoice/999999/history").status_code == 404
    rec = synthetic_app.test_client()
    login_as(rec, synthetic_app, "reception1")
    assert rec.get(f"/manager/invoice/{inv}/history").status_code == 302


def test_invoice_numbers_link_to_the_history_in_logs(synthetic_app):
    inv = make_invoice_with_events(synthetic_app)
    c = mgr(synthetic_app)
    assert f"/manager/invoice/{inv}/history" in c.get(f"/manager/logs?invoice_id={inv}").get_data(as_text=True)


def test_attention_detail_links_invoice_numbers(synthetic_app):
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    pid = conn.execute("SELECT id FROM patients LIMIT 1").fetchone()[0]
    inv = conn.execute(
        "INSERT INTO invoices (patient_id, status, insurance_type, work_date, shift, opened_at, opened_by) VALUES (?, 'open', 'آزاد', ?, 'morning', '2000-01-01 00:00:00', 'reception1')",
        (pid, datetime.now().strftime("%Y-%m-%d")),
    ).lastrowid
    conn.commit()
    conn.close()
    html = mgr(synthetic_app).get("/manager/attention/open_invoices").get_data(as_text=True)
    assert f"/manager/invoice/{inv}/history" in html
