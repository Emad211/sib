"""Procedure catalog (FB-08) and the procedure page (FB-07)."""

import sqlite3

import pytest
from conftest import login_as, put_on_current_shift


def q(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def run(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    cur = conn.execute(sql, args)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last


@pytest.fixture(autouse=True)
def empty_catalog(synthetic_app):
    """The synthetic database ships a few procedure tariffs; every test here starts from none."""
    run(synthetic_app, "DELETE FROM procedure_tariffs")


def mgr(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def rec(app):
    c = app.test_client()
    login_as(c, app, "reception1")
    put_on_current_shift(app, "reception1")
    return c


# --------------------------------------------------------------------------- manager page


def test_manager_adds_updates_and_deactivates_with_history(synthetic_app):
    c = mgr(synthetic_app)
    c.post("/manager/tariffs/procedures", data={"action": "add", "name": "کشیدن  بخیه", "unit_price": "۲۰٬۰۰۰"})
    ((tid, name, price, active),) = q(synthetic_app, "SELECT id, name, unit_price, is_active FROM procedure_tariffs")
    assert (name, price, active) == ("کشیدن بخیه", 20000.0, 1)  # spaces collapsed, Persian digits read
    c.post(
        "/manager/tariffs/procedures",
        data={"action": "update", "item_id": tid, "name": "کشیدن بخیه", "unit_price": "25000", "is_active": "1"},
    )
    c.post("/manager/tariffs/procedures", data={"action": "deactivate", "item_id": tid})
    assert q(synthetic_app, "SELECT unit_price, is_active FROM procedure_tariffs WHERE id=?", tid) == [(25000.0, 0)]
    kinds = q(synthetic_app, "SELECT kind, action, field FROM tariff_changes ORDER BY id")
    assert (
        kinds[0] == ("procedure", "create", "ایجاد")
        and ("procedure", "update", "قیمت") in kinds
        and ("procedure", "update", "وضعیت") in kinds
    )


@pytest.mark.parametrize("price", ["", "abc", "0", "-5"])
def test_a_bad_price_is_refused(synthetic_app, price):
    r = mgr(synthetic_app).post(
        "/manager/tariffs/procedures",
        data={"action": "add", "name": "پانسمان", "unit_price": price},
        follow_redirects=True,
    )
    assert "مبلغ نامعتبر" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT COUNT(*) FROM procedure_tariffs") == [(0,)]


def test_duplicate_name_is_refused(synthetic_app):
    c = mgr(synthetic_app)
    c.post("/manager/tariffs/procedures", data={"action": "add", "name": "پانسمان", "unit_price": "100"})
    r = c.post(
        "/manager/tariffs/procedures",
        data={"action": "add", "name": "پانسمان", "unit_price": "100"},
        follow_redirects=True,
    )
    assert "قبلاً تعریف شده" in r.get_data(as_text=True)
    assert q(synthetic_app, "SELECT COUNT(*) FROM procedure_tariffs") == [(1,)]


def test_suggestions_come_from_history_and_disappear_once_added(synthetic_app):
    pid = q(synthetic_app, "SELECT id FROM patients LIMIT 1")[0][0]
    for price in (20000, 20000, 30000):
        run(
            synthetic_app,
            "INSERT INTO procedures (patient_id, procedure_type, price, work_date) VALUES (?, 'ییی بخیه تاریخی', ?, '2026-09-01')",
            pid,
            price,
        )
    c = mgr(synthetic_app)
    html = c.get("/manager/tariffs/procedures").get_data(as_text=True)
    assert "ییی بخیه تاریخی" in html and "2 قیمت متفاوت" in html and 'value="20000"' in html
    c.post("/manager/tariffs/procedures", data={"action": "add", "name": "ییی بخیه تاریخی", "unit_price": "20000"})
    html = c.get("/manager/tariffs/procedures").get_data(as_text=True)
    assert 'type="hidden" name="name" value="ییی بخیه تاریخی"' not in html  # in the list now, not a suggestion


def test_reception_cannot_manage_the_catalog_and_the_hub_links_it(synthetic_app):
    r = rec(synthetic_app).post("/manager/tariffs/procedures", data={"action": "add", "name": "x", "unit_price": "1"})
    assert r.status_code == 302 and q(synthetic_app, "SELECT COUNT(*) FROM procedure_tariffs") == [(0,)]
    assert "/manager/tariffs/procedures" in mgr(synthetic_app).get("/manager/tariffs").get_data(as_text=True)


# --------------------------------------------------------------------------- reception page and submit


@pytest.fixture()
def ready(synthetic_app):
    """An open invoice, a shift doctor and nurse, and two catalog items."""
    pid = q(synthetic_app, "SELECT id FROM patients LIMIT 1")[0][0]
    inv = run(
        synthetic_app,
        "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, opened_by)"
        " VALUES (?, 'open', 'آزاد', 0, '2026-09-24', 'morning', '2026-09-24 08:00:00', 'reception1')",
        pid,
    )
    doctors = [
        r[0]
        for r in q(synthetic_app, "SELECT id FROM medical_staff WHERE staff_type='doctor' AND is_active=1 ORDER BY id")
    ]
    nurse = q(synthetic_app, "SELECT id FROM medical_staff WHERE staff_type='nurse' AND is_active=1 ORDER BY id")[0][0]
    c = rec(synthetic_app)
    c.post("/reception/shift_staff", data={"doctor_id": doctors[0], "nurse_id": nurse})
    t1 = run(
        synthetic_app, "INSERT INTO procedure_tariffs (name, unit_price, is_active) VALUES ('پانسمان کوچک', 100000, 1)"
    )
    t2 = run(synthetic_app, "INSERT INTO procedure_tariffs (name, unit_price, is_active) VALUES ('خاموش', 5, 0)")
    return {"c": c, "inv": inv, "doctors": doctors, "nurse": nurse, "t1": t1, "t2": t2}


def submit(ready, *lines):
    return ready["c"].post(
        "/reception/procedures", json={"invoice_id": ready["inv"], "procedures": list(lines), "consumables": []}
    )


def test_page_offers_the_catalog_named_doctors_and_the_shift_nurse(synthetic_app, ready):
    html = ready["c"].get(f"/reception/procedures/new?invoice_id={ready['inv']}").get_data(as_text=True)
    assert 'id="procTariff"' in html and "پانسمان کوچک" in html and "خاموش" not in html
    assert f'value="doctor:{ready["doctors"][0]}"' in html and "پرستار شیفت:" in html
    assert "✨ ثبت کار عملی و مصرفی" in html and "💉 ثبت خدمات پرستاری" not in html  # FB-07 wrong sidebar label


def test_price_comes_from_the_catalog_not_from_the_browser(synthetic_app, ready):
    r = submit(ready, {"tariff_id": ready["t1"], "qty": 2, "performer": "nurse", "name": "x", "unit_price": 1})
    assert r.status_code == 200, r.get_json()
    rows = q(
        synthetic_app,
        "SELECT procedure_type, price, performer_type, doctor_id, nurse_id FROM procedures WHERE invoice_id=?",
        ready["inv"],
    )
    assert rows == [("پانسمان کوچک", 100000.0, "nurse", None, ready["nurse"])] * 2


def test_a_named_doctor_is_recorded_as_the_performer(synthetic_app, ready):
    chosen = ready["doctors"][-1]
    assert submit(ready, {"tariff_id": ready["t1"], "qty": 1, "performer": f"doctor:{chosen}"}).status_code == 200
    assert q(
        synthetic_app, "SELECT performer_type, doctor_id, nurse_id FROM procedures WHERE invoice_id=?", ready["inv"]
    ) == [("doctor", chosen, None)]


@pytest.mark.parametrize(
    "line, message",
    [
        ({"tariff_id": 999999, "qty": 1, "performer": "nurse"}, "فهرست"),
        (
            {"qty": 1, "performer": "nurse", "name": "دستی", "unit_price": 5},
            "فهرست",
        ),  # manual is closed once a catalog exists
        ({"tariff_id": "inactive", "qty": 1, "performer": "nurse"}, "فهرست"),
        ({"tariff_id": "t1", "qty": 0, "performer": "nurse"}, "تعداد"),
        ({"tariff_id": "t1", "qty": 99, "performer": "nurse"}, "تعداد"),
        ({"tariff_id": "t1", "qty": 1, "performer": "doctor:999999"}, "پزشک"),
        ({"tariff_id": "t1", "qty": 1, "performer": "doctor:abc"}, "پزشک"),
    ],
)
def test_bad_lines_are_refused_and_nothing_is_saved(synthetic_app, ready, line, message):
    line = dict(line)
    if line.get("tariff_id") == "t1":
        line["tariff_id"] = ready["t1"]
    elif line.get("tariff_id") == "inactive":
        line["tariff_id"] = ready["t2"]
    good = {"tariff_id": ready["t1"], "qty": 1, "performer": "nurse"}
    r = submit(ready, good, line)
    assert r.status_code == 400 and message in r.get_json()["error"]
    assert q(synthetic_app, "SELECT COUNT(*) FROM procedures WHERE invoice_id=?", ready["inv"]) == [
        (0,)
    ]  # all or nothing


def test_shift_nurse_must_be_set_for_a_nurse_line(synthetic_app, ready):
    run(synthetic_app, "UPDATE shift_staff SET nurse_id = NULL")
    r = submit(ready, {"tariff_id": ready["t1"], "qty": 1, "performer": "nurse"})
    assert r.status_code == 400


def test_with_an_empty_catalog_manual_entry_still_works(synthetic_app, ready):
    run(synthetic_app, "DELETE FROM procedure_tariffs")
    html = ready["c"].get(f"/reception/procedures/new?invoice_id={ready['inv']}").get_data(as_text=True)
    assert 'id="procName"' in html and "هنوز «تعرفهٔ کارهای عملی»" in html
    r = submit(
        ready, {"name": "بخیه دستی", "unit_price": 30000, "qty": 1, "performer": f"doctor:{ready['doctors'][0]}"}
    )
    assert r.status_code == 200
    assert q(synthetic_app, "SELECT procedure_type, price FROM procedures WHERE invoice_id=?", ready["inv"]) == [
        ("بخیه دستی", 30000.0)
    ]
