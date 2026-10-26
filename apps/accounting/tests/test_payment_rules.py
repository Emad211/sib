"""Mandatory payment type and the close-time payment facts (ACC-2.9, ACC-2.11)."""

import sqlite3

from conftest import login_as

from src.domain import identity


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


def reception(app):
    c = app.test_client()
    login_as(c, app, "reception1")
    return c


def free_invoice(app, price=250000):
    """An open free-pay ('آزاد') invoice with one nursing item the patient pays for."""
    pid = q(app, "SELECT id FROM patients LIMIT 1")[0][0]
    inv = run(
        app,
        "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, opened_by)"
        " VALUES (?, 'open', 'آزاد', 0, '2026-09-24', 'morning', '2026-09-24 08:00:00', 'reception1')",
        pid,
    )
    item = run(
        app,
        "INSERT INTO injections (patient_id, injection_type, work_date, total_price, invoice_id) VALUES (?, 'سرم', '2026-09-24', ?, ?)",
        pid, price, inv,
    )  # fmt: skip
    return inv, item


def covered_invoice(app):
    """An open invoice for an insurance that covers nursing: the patient owes nothing for the item."""
    pid = q(app, "SELECT id FROM patients LIMIT 1")[0][0]
    covering = q(
        app,
        "SELECT insurance_type FROM visit_tariffs WHERE nursing_covers = 1 AND COALESCE(is_supplementary,0) = 0 LIMIT 1",
    )
    if not covering:
        run(
            app,
            "UPDATE visit_tariffs SET nursing_covers = 1 WHERE is_base_tariff = 0 AND COALESCE(is_supplementary,0) = 0 AND id = (SELECT MIN(id) FROM visit_tariffs WHERE is_base_tariff = 0 AND COALESCE(is_supplementary,0) = 0)",
        )
        covering = q(
            app,
            "SELECT insurance_type FROM visit_tariffs WHERE nursing_covers = 1 AND COALESCE(is_supplementary,0) = 0 LIMIT 1",
        )
    ins = covering[0][0]
    run(app, "DELETE FROM insurance_nursing_exclusions WHERE insurance_type = ?", ins)
    inv = run(
        app,
        "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, opened_by)"
        " VALUES (?, 'open', ?, 0, '2026-09-24', 'morning', '2026-09-24 08:00:00', 'reception1')",
        pid, ins,
    )  # fmt: skip
    item = run(
        app,
        "INSERT INTO injections (patient_id, injection_type, work_date, total_price, invoice_id) VALUES (?, 'سرم', '2026-09-24', 90000, ?)",
        pid, inv,
    )  # fmt: skip
    return inv, item


def pay(c, inv, item, payment_type, paid="true"):
    data = {"invoice_id": inv, "item_type": "injection", "item_id": item, "is_paid": paid}
    if payment_type is not None:
        data["payment_type"] = payment_type
    return c.post("/reception/item/payment", data=data)


# --------------------------------------------------------------------------- set payment


def test_paying_an_item_the_patient_owes_needs_card_or_cash(synthetic_app):
    inv, item = free_invoice(synthetic_app)
    c = reception(synthetic_app)
    for bad in (None, "", "insurance", "CARD"):
        r = pay(c, inv, item, bad)
        assert r.status_code == 400 and "روش پرداخت" in r.get_json()["error"], bad
    assert q(synthetic_app, "SELECT COUNT(*) FROM invoice_item_payments WHERE invoice_id=?", inv) == [(0,)]
    assert pay(c, inv, item, "card").status_code == 200
    assert pay(c, inv, item, "cash").status_code == 200
    assert q(synthetic_app, "SELECT payment_type, is_paid FROM invoice_item_payments WHERE invoice_id=?", inv) == [
        ("cash", 1)
    ]


def test_unticking_paid_does_not_need_a_method(synthetic_app):
    inv, item = free_invoice(synthetic_app)
    c = reception(synthetic_app)
    pay(c, inv, item, "card")
    assert pay(c, inv, item, None, paid="false").status_code == 200
    assert q(synthetic_app, "SELECT is_paid FROM invoice_item_payments WHERE invoice_id=?", inv) == [(0,)]


def test_an_item_covered_by_insurance_can_be_paid_without_a_method(synthetic_app):
    inv, item = covered_invoice(synthetic_app)
    c = reception(synthetic_app)
    r = pay(c, inv, item, None)
    assert r.status_code == 200, r.get_json()
    assert q(synthetic_app, "SELECT payment_type, is_paid FROM invoice_item_payments WHERE invoice_id=?", inv) == [
        (None, 1)
    ]


def test_paying_an_item_that_is_not_on_the_invoice_is_a_404(synthetic_app):
    inv, _ = free_invoice(synthetic_app)
    assert pay(reception(synthetic_app), inv, 999999, None).status_code == 404


def test_settle_all_needs_card_or_cash(synthetic_app):
    inv, _ = free_invoice(synthetic_app)
    c = reception(synthetic_app)
    assert c.post("/reception/item/settle_all", data={"invoice_id": inv}).status_code == 400
    assert (
        c.post("/reception/item/settle_all", data={"invoice_id": inv, "payment_type": "insurance"}).status_code == 400
    )
    assert c.post("/reception/item/settle_all", data={"invoice_id": inv, "payment_type": "cash"}).status_code == 200
    assert q(synthetic_app, "SELECT payment_type FROM invoice_item_payments WHERE invoice_id=?", inv) == [("cash",)]


# --------------------------------------------------------------------------- closing


def test_close_is_blocked_while_a_paid_item_has_no_method(synthetic_app):
    """Legacy rows: marked paid without a type (the dropdown showed 'card' but the database had NULL)."""
    inv, item = free_invoice(synthetic_app)
    run(
        synthetic_app,
        "INSERT INTO invoice_item_payments (invoice_id, item_type, item_id, payment_type, is_paid) VALUES (?, 'injection', ?, NULL, 1)",
        inv,
        item,
    )
    r = reception(synthetic_app).post("/reception/invoice/close", data={"invoice_id": inv})
    body = r.get_json()
    assert r.status_code == 400 and "روش پرداخت" in body["error"]
    assert [i["id"] for i in body["unpaid_items"]] == [item] and "مشخص نیست" in body["unpaid_items"][0]["description"]
    assert q(synthetic_app, "SELECT status FROM invoices WHERE id=?", inv) == [("open",)]


def test_close_is_still_blocked_by_an_unpaid_item_first(synthetic_app):
    inv, _ = free_invoice(synthetic_app)
    r = reception(synthetic_app).post("/reception/invoice/close", data={"invoice_id": inv})
    assert r.status_code == 400 and "تسویه نشده" in r.get_json()["error"]


def test_a_covered_invoice_closes_with_no_method(synthetic_app):
    inv, item = covered_invoice(synthetic_app)
    c = reception(synthetic_app)
    pay(c, inv, item, None)
    r = c.post("/reception/invoice/close", data={"invoice_id": inv})
    assert r.status_code == 200, r.get_json()
    assert q(synthetic_app, "SELECT status FROM invoices WHERE id=?", inv) == [("closed",)]


def test_the_reception_page_asks_for_a_method_on_legacy_untyped_rows(synthetic_app):
    inv, item = free_invoice(synthetic_app)
    run(
        synthetic_app,
        "INSERT INTO invoice_item_payments (invoice_id, item_type, item_id, payment_type, is_paid) VALUES (?, 'injection', ?, NULL, 1)",
        inv,
        item,
    )
    html = reception(synthetic_app).get(f"/reception/?invoice_id={inv}").get_data(as_text=True)
    assert "⚠ روش پرداخت؟" in html


def test_a_normal_unpaid_row_still_defaults_to_card(synthetic_app):
    inv, _ = free_invoice(synthetic_app)
    html = reception(synthetic_app).get(f"/reception/?invoice_id={inv}").get_data(as_text=True)
    assert "⚠ روش پرداخت؟" not in html and 'value="card" selected' in html


# --------------------------------------------------------------------------- facts frozen at close


def test_closing_freezes_amount_and_method_per_item(synthetic_app):
    inv, item = free_invoice(synthetic_app, price=250000)
    c = reception(synthetic_app)
    pay(c, inv, item, "cash")
    assert q(synthetic_app, "SELECT COUNT(*) FROM invoice_payment_lines WHERE invoice_id=?", inv) == [
        (0,)
    ]  # nothing before close
    assert c.post("/reception/invoice/close", data={"invoice_id": inv}).status_code == 200
    lines = q(
        synthetic_app,
        "SELECT item_type, item_id, payment_type, patient_amount, closed_at IS NOT NULL FROM invoice_payment_lines WHERE invoice_id=?",
        inv,
    )
    assert lines == [("injection", item, "cash", 250000.0, 1)]
    assert q(synthetic_app, "SELECT amount FROM v_shift_cash_receipts_v1 WHERE invoice_id=?", inv) == [(250000.0,)]


def test_a_later_tariff_change_does_not_rewrite_what_was_paid(synthetic_app):
    inv, item = covered_invoice(synthetic_app)
    pid = q(synthetic_app, "SELECT patient_id FROM invoices WHERE id=?", inv)[0][0]
    visit = run(
        synthetic_app,
        "INSERT INTO visits (patient_id, doctor_name, work_date, price, invoice_id) VALUES (?, 'د', '2026-09-24', 500000, ?)",
        pid,
        inv,
    )
    c = reception(synthetic_app)
    pay(c, inv, item, None)
    r = c.post(
        "/reception/item/payment",
        data={"invoice_id": inv, "item_type": "visit", "item_id": visit, "payment_type": "cash", "is_paid": "true"},
    )
    assert r.status_code == 200
    assert c.post("/reception/invoice/close", data={"invoice_id": inv}).status_code == 200
    frozen = q(
        synthetic_app,
        "SELECT item_type, payment_type, patient_amount FROM invoice_payment_lines WHERE invoice_id=? ORDER BY item_type",
        inv,
    )
    run(synthetic_app, "UPDATE visit_tariffs SET tariff_price = tariff_price + 777777")
    assert (
        q(
            synthetic_app,
            "SELECT item_type, payment_type, patient_amount FROM invoice_payment_lines WHERE invoice_id=? ORDER BY item_type",
            inv,
        )
        == frozen
    )
    assert ("injection", None, 0.0) in frozen  # the covered item: no method, no money


def test_closing_twice_does_not_duplicate_lines(synthetic_app):
    inv, item = free_invoice(synthetic_app)
    c = reception(synthetic_app)
    pay(c, inv, item, "card")
    c.post("/reception/invoice/close", data={"invoice_id": inv})
    assert c.post("/reception/invoice/close", data={"invoice_id": inv}).status_code == 400
    assert q(synthetic_app, "SELECT COUNT(*) FROM invoice_payment_lines WHERE invoice_id=?", inv) == [(1,)]


# --------------------------------------------------------------------------- patient identity input


def new_patient(c, **overrides):
    data = {
        "name": "تست",
        "family_name": "ثبت",
        "phone": "09123456789",
        "national_id": "2170415981",
        "insurance_type": "آزاد",
    }
    data.update(overrides)
    return c.post("/reception/new", data=data)


def test_persian_digits_are_stored_as_ascii(synthetic_app):
    r = new_patient(reception(synthetic_app), phone="۰۹۱۲۳۴۵۶۷۸۹", national_id="۲۱۷۰۴۱۵۹۸۱")
    assert r.status_code == 200, r.get_json()
    assert q(
        synthetic_app, "SELECT national_id, phone_number FROM patients WHERE name='تست' AND family_name='ثبت'"
    ) == [("2170415981", "09123456789")]


def test_the_same_person_in_persian_and_ascii_digits_is_one_patient(synthetic_app):
    c = reception(synthetic_app)
    new_patient(c)
    new_patient(c, national_id="۲۱۷۰۴۱۵۹۸۱")
    assert q(synthetic_app, "SELECT COUNT(*) FROM patients WHERE national_id='2170415981'") == [(1,)]


def test_invalid_identity_input_is_still_rejected(synthetic_app):
    c = reception(synthetic_app)
    assert new_patient(c, national_id="2170415982").status_code == 400
    assert new_patient(c, phone="9123456789").status_code == 400
    assert new_patient(c, national_id="").status_code == 400  # Iranian patients need an ID
    foreign = new_patient(c, national_id="", is_foreign="on")
    assert foreign.status_code == 200 and foreign.get_json()["success"]


# --------------------------------------------------------------------------- the SQL rule matches the Python rule


def test_sql_national_id_rule_matches_the_python_rule(synthetic_app):
    import random

    rng = random.Random(7)
    samples = {
        "2170415981",
        "2110530979",
        "0499370899",
        "0000000000",
        "1111111111",
        "123456789",
        "12345678901",
        "abcdefghij",
        "217041598a",
        "",
        "2170415982",
    }
    samples |= {"".join(rng.choice("0123456789") for _ in range(10)) for _ in range(3000)}
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    conn.execute("DELETE FROM patients")
    conn.executemany(
        "INSERT INTO patients (name, family_name, national_id) VALUES ('x', 'y', ?)", [(s or None,) for s in samples]
    )
    conn.commit()
    rows = conn.execute(
        "SELECT p.national_id, v.has_valid_nid FROM v_shift_patient_registrations_v1 v JOIN patients p ON p.id = v.patient_id"
    ).fetchall()
    conn.close()
    assert len(rows) == len(samples)
    for nid, flag in rows:
        assert bool(flag) == identity.is_valid_national_id(nid or ""), nid
    assert sum(flag for _, flag in rows) > 150  # the sample really contains valid ids
