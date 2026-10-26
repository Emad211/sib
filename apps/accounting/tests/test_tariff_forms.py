"""Tariff forms: amounts are parsed, never guessed (ACC-2.2), and consumables carry the stock mapping
the shift app needs (ACC-2.11)."""

import re
import sqlite3

import pytest
from conftest import login_as


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


def history(app):
    return q(app, "SELECT kind, label, action, field, old_value, new_value FROM tariff_changes ORDER BY id")


# --------------------------------------------------------------------------- amounts


@pytest.mark.parametrize("typed, saved", [("120,000", 120000.0), ("۱۲۰٬۰۰۰", 120000.0), (" 5000 ", 5000.0), ("0", 0.0)])
def test_nursing_price_is_parsed_from_what_people_type(synthetic_app, typed, saved):
    mgr(synthetic_app).post("/manager/tariffs/nursing", data={"action": "add", "service_name": "خدمت تست", "unit_price": typed})
    assert q(synthetic_app, "SELECT unit_price FROM nursing_services WHERE service_name='خدمت تست'") == [(saved,)]


@pytest.mark.parametrize("typed", ["", "abc", "12x", "-5", "nan", "1e3"])
def test_an_unreadable_price_is_refused_not_saved_as_zero(synthetic_app, typed):
    """It used to become price 0 without a word (a pasted '۱۲۰٬۰۰۰' silently made the service free)."""
    c = mgr(synthetic_app)
    r = c.post("/manager/tariffs/nursing", data={"action": "add", "service_name": "خدمت بد", "unit_price": typed}, follow_redirects=True)
    assert "مبلغ نامعتبر است" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT COUNT(*) FROM nursing_services WHERE service_name='خدمت بد'") == [(0,)]
    assert history(synthetic_app) == []


def test_a_bad_price_on_update_keeps_the_old_price(synthetic_app):
    sid, name, price = q(synthetic_app, "SELECT id, service_name, unit_price FROM nursing_services LIMIT 1")[0]
    mgr(synthetic_app).post("/manager/tariffs/nursing", data={"action": "update", "service_id": sid, "service_name": name, "unit_price": "oops", "is_active": "1"})
    assert q(synthetic_app, "SELECT unit_price FROM nursing_services WHERE id=?", sid) == [(price,)]


def test_insurance_and_base_prices_refuse_garbage(synthetic_app):
    c = mgr(synthetic_app)
    base = q(synthetic_app, "SELECT tariff_price FROM visit_tariffs WHERE is_base_tariff = 1")[0][0]
    c.post("/manager/tariffs/insurance", data={"action": "set_base", "base_visit_price": "free"})
    assert q(synthetic_app, "SELECT tariff_price FROM visit_tariffs WHERE is_base_tariff = 1") == [(base,)]
    c.post("/manager/tariffs/insurance", data={"action": "add", "insurance_type": "بیمه تست", "tariff_price": "x"})
    assert q(synthetic_app, "SELECT COUNT(*) FROM visit_tariffs WHERE insurance_type='بیمه تست'") == [(0,)]
    c.post("/manager/tariffs/insurance", data={"action": "add", "insurance_type": "بیمه تست", "tariff_price": "۵۰٬۰۰۰"})
    assert q(synthetic_app, "SELECT tariff_price FROM visit_tariffs WHERE insurance_type='بیمه تست'") == [(50000.0,)]


# --------------------------------------------------------------------------- consumable stock mapping


def add_consumable(c, **extra):
    data = {"action": "add", "name": "سرنگ تست", "default_price": "3000", "category": "supply"} | extra
    return c.post("/manager/tariffs/consumables", data=data, follow_redirects=True)


def test_consumable_is_saved_with_stock_code_and_factor(synthetic_app):
    add_consumable(mgr(synthetic_app), acc_code=" SYR-5 ", stock_factor="0.5")
    assert q(synthetic_app, "SELECT acc_code, stock_factor FROM consumable_tariffs WHERE name='سرنگ تست'") == [("SYR-5", 0.5)]
    created = [h for h in history(synthetic_app) if h[2] == "create"][0]
    assert "کد کالا (انبار): SYR-5" in created[5]


def test_empty_code_is_null_and_empty_factor_is_one(synthetic_app):
    add_consumable(mgr(synthetic_app))
    assert q(synthetic_app, "SELECT acc_code, stock_factor FROM consumable_tariffs WHERE name='سرنگ تست'") == [(None, 1.0)]


@pytest.mark.parametrize("factor", ["0", "-1", "x", "۰"])
def test_a_factor_that_is_not_positive_is_refused(synthetic_app, factor):
    r = add_consumable(mgr(synthetic_app), acc_code="A", stock_factor=factor)
    assert "ضریب تبدیل" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT COUNT(*) FROM consumable_tariffs WHERE name='سرنگ تست'") == [(0,)]


def test_a_code_longer_than_forty_characters_is_refused(synthetic_app):
    r = add_consumable(mgr(synthetic_app), acc_code="X" * 41)
    assert "حداکثر ۴۰" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT COUNT(*) FROM consumable_tariffs WHERE name='سرنگ تست'") == [(0,)]


def test_updating_the_mapping_is_recorded_and_unchanged_fields_are_not(synthetic_app):
    c = mgr(synthetic_app)
    add_consumable(c, acc_code="OLD", stock_factor="1")
    iid = q(synthetic_app, "SELECT id FROM consumable_tariffs WHERE name='سرنگ تست'")[0][0]
    c.post(
        "/manager/tariffs/consumables",
        data={"action": "update", "item_id": iid, "name": "سرنگ تست", "default_price": "3000", "category": "supply", "is_active": "1", "acc_code": "NEW", "stock_factor": "2"},
    )
    updates = [(h[3], h[4], h[5]) for h in history(synthetic_app) if h[2] == "update"]
    assert sorted(updates) == sorted([("کد کالا (انبار)", "OLD", "NEW"), ("ضریب تبدیل به انبار", "1", "2")])
    assert q(synthetic_app, "SELECT acc_code, stock_factor FROM consumable_tariffs WHERE id=?", iid) == [("NEW", 2.0)]


def test_the_list_shows_mapped_codes_and_marks_unmapped_items(synthetic_app):
    c = mgr(synthetic_app)
    add_consumable(c, acc_code="SYR-5", stock_factor="2")
    html = c.get("/manager/tariffs/consumables").get_data(as_text=True)
    assert "SYR-5" in html and "×2" in html and "هنوز به کالای انبار وصل نشده" in html


def test_the_mapping_reaches_the_shift_view(synthetic_app):
    add_consumable(mgr(synthetic_app), acc_code="SYR-5", stock_factor="2")
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    pid = conn.execute("SELECT id FROM patients LIMIT 1").fetchone()[0]
    inv = conn.execute(
        "INSERT INTO invoices (patient_id, status, insurance_type, work_date, shift, opened_at, closed_at, opened_by)"
        " VALUES (?, 'closed', 'آزاد', '2026-09-24', 'morning', '2026-09-24 08:00:00', '2026-09-24 09:00:00', 'u')",
        (pid,),
    ).lastrowid
    conn.execute("INSERT INTO consumables_ledger (patient_id, item_name, quantity, total_cost, invoice_id) VALUES (?, 'سرنگ تست', 3, 9000, ?)", (pid, inv))
    conn.commit()
    row = conn.execute("SELECT acc_code, quantity FROM v_shift_consumption_v1 WHERE invoice_id = ?", (inv,)).fetchone()
    conn.close()
    assert row == ("SYR-5", 6.0)


# --------------------------------------------------------------------------- names cannot break the page


EVIL = "x');alert(1);//"


@pytest.mark.parametrize(
    "page, post, handler",
    [
        ("/manager/tariffs/nursing", {"action": "add", "service_name": EVIL, "unit_price": "1"}, "openEditModal"),
        ("/manager/tariffs/consumables", {"action": "add", "name": EVIL, "default_price": "1", "category": "supply"}, "openEditModal"),
        ("/manager/tariffs/insurance", {"action": "add", "insurance_type": EVIL, "tariff_price": "1"}, "openExclusionsModal"),
        ("/manager/tariffs/insurance", {"action": "add", "insurance_type": EVIL, "tariff_price": "1"}, "openEditModal"),
    ],
)
def test_tariff_names_with_quotes_stay_inside_the_inline_handler(synthetic_app, page, post, handler):
    c = mgr(synthetic_app)
    c.post(page, data=post)
    html = c.get(page).get_data(as_text=True)
    mine = [h for h in re.findall(rf"onclick='{handler}\(([^']*)'", html) if "alert" in h]
    assert len(mine) == 1 and r"\u0027" in mine[0] and "&#39;" not in mine[0]


@pytest.mark.parametrize("page", ["/manager/tariffs/nursing", "/manager/tariffs/consumables", "/manager/tariffs/insurance"])
def test_tariff_pages_show_the_outcome_of_what_you_did(synthetic_app, page):
    """These pages had no place to show flash messages: errors such as 'already defined' were invisible."""
    c = mgr(synthetic_app)
    field = {"nursing": "service_name", "consumables": "name", "insurance": "insurance_type"}[page.rsplit("/", 1)[1]]
    price = {"nursing": "unit_price", "consumables": "default_price", "insurance": "tariff_price"}[page.rsplit("/", 1)[1]]
    data = {"action": "add", field: "تکراری تست", price: "10", "category": "supply"}
    first = c.post(page, data=data, follow_redirects=True).get_data(as_text=True)
    assert "با موفقیت اضافه شد" in first
    again = c.post(page, data=data, follow_redirects=True).get_data(as_text=True)
    assert "قبلاً" in again  # "already defined" (consumables, insurance) / "already exists" (nursing: database refusal)


def test_renaming_a_nursing_service_to_an_existing_name_is_refused_politely(synthetic_app):
    c = mgr(synthetic_app)
    (first, name_first), (second, name_second) = q(synthetic_app, "SELECT id, service_name FROM nursing_services ORDER BY id LIMIT 2")
    r = c.post("/manager/tariffs/nursing", data={"action": "update", "service_id": second, "service_name": name_first, "unit_price": "1", "is_active": "1"}, follow_redirects=True)
    assert r.status_code == 200 and "قبلاً وجود دارد" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT service_name FROM nursing_services WHERE id=?", second) == [(name_second,)]
    assert history(synthetic_app) == []  # the rolled-back change left no history row


def test_renaming_a_consumable_to_an_existing_name_is_refused_politely(synthetic_app):
    c = mgr(synthetic_app)
    (first, name_first), (second, name_second) = q(synthetic_app, "SELECT id, name FROM consumable_tariffs ORDER BY id LIMIT 2")
    r = c.post("/manager/tariffs/consumables", data={"action": "update", "item_id": second, "name": name_first, "default_price": "1", "category": "supply", "is_active": "1"}, follow_redirects=True)
    assert r.status_code == 200 and "قبلاً وجود دارد" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT name FROM consumable_tariffs WHERE id=?", second) == [(name_second,)]
