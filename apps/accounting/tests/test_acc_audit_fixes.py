"""Audit fixes: foreign patients keep their ID (FB-03/04), arrears skip voided invoices, void + log are atomic."""

import csv
import io
import sqlite3

from conftest import login_as
from test_payment_rules import new_patient, put_on_current_shift  # noqa: F401  (helpers shared with the identity tests)

RANGE = "from=1405/06/19&to=1405/07/02"


def q(app, sql, *a):
    c = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def run(app, sql, *a):
    c = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        c.execute(sql, a)
        c.commit()
    finally:
        c.close()


def reception(app):
    c = app.test_client()
    login_as(c, app, "reception1")
    put_on_current_shift(app, "reception1")
    return c


def manager(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


# --------------------------------------------------------------------------- 1. foreign patients


def test_foreign_id_is_stored_normalised(synthetic_app):
    r = new_patient(reception(synthetic_app), national_id="p1234567", is_foreign="on", phone="09120000001")
    assert r.status_code == 200, r.get_json()
    assert q(synthetic_app, "SELECT national_id, is_foreign FROM patients WHERE phone_number='09120000001'") == [
        ("P1234567", 1)
    ]


def test_foreign_duplicate_is_detected_and_not_duplicated(synthetic_app):
    c = reception(synthetic_app)
    assert new_patient(c, national_id="P1234567", is_foreign="on", phone="09120000001").status_code == 200
    # same passport typed for a different name: must ask, like an Iranian ID would (FB-04)
    again = new_patient(
        c, name="سارا", family_name="محمدی", national_id="p1234567", is_foreign="on", phone="09120000002"
    )
    assert again.status_code == 409 and again.get_json()["need_confirm"]
    # same person coming back reuses the patient instead of creating a second one
    same = new_patient(c, national_id="P1234567", is_foreign="on", phone="09120000001")
    assert same.status_code == 200
    assert q(synthetic_app, "SELECT COUNT(*) FROM patients WHERE national_id='P1234567'") == [(1,)]


def test_search_patient_finds_a_foreigner_by_passport_in_any_case(synthetic_app):
    c = reception(synthetic_app)
    new_patient(c, national_id="P1234567", is_foreign="on", phone="09120000001")
    found = c.get("/reception/search_patient?national_id=p1234567").get_json()
    assert found["found"] and found["patient"]["name"] == "مهرداد"


# --------------------------------------------------------------------------- 2. arrears and voided invoices


def _csv_rows(resp):
    return list(csv.reader(io.StringIO(resp.get_data(as_text=True).lstrip("﻿"))))[1:]


def _insured_invoice_in_range(app):
    return q(
        app,
        """SELECT v.invoice_id FROM visits v JOIN invoices inv ON inv.id = v.invoice_id
           WHERE v.insurance_type IS NOT NULL AND v.insurance_type != 'آزاد'
             AND v.work_date BETWEEN '2026-09-11' AND '2026-09-24' LIMIT 1""",
    )[0][0]


def test_voided_invoice_leaves_insurance_arrears_page_and_export(synthetic_app):
    c = manager(synthetic_app)
    iid = _insured_invoice_in_range(synthetic_app)
    page = c.get(f"/manager/insurance_arrears?{RANGE}&_diag=1").get_json()
    before_page = len(page["visit_arrears"]) + len(page["nursing_arrears"]) + len(page["supplementary_arrears"])
    before_total = page["total_debt"]
    before_csv = len(_csv_rows(c.get(f"/manager/insurance_arrears/export?{RANGE}")))
    assert any(a["invoice_id"] == iid for a in page["visit_arrears"] + page["nursing_arrears"])

    run(synthetic_app, "UPDATE invoices SET status='voided' WHERE id=?", iid)

    page = c.get(f"/manager/insurance_arrears?{RANGE}&_diag=1").get_json()
    after_page = len(page["visit_arrears"]) + len(page["nursing_arrears"]) + len(page["supplementary_arrears"])
    assert not any(
        a["invoice_id"] == iid for a in page["visit_arrears"] + page["nursing_arrears"] + page["supplementary_arrears"]
    )
    assert after_page < before_page and page["total_debt"] < before_total
    assert len(_csv_rows(c.get(f"/manager/insurance_arrears/export?{RANGE}"))) < before_csv


def test_open_invoices_still_count_in_arrears(synthetic_app):
    """Unchanged convention (same as the dashboard: everything except voided)."""
    c = manager(synthetic_app)
    iid = _insured_invoice_in_range(synthetic_app)
    before = c.get(f"/manager/insurance_arrears?{RANGE}&_diag=1").get_json()["total_debt"]
    run(synthetic_app, "UPDATE invoices SET status='open' WHERE id=?", iid)
    assert c.get(f"/manager/insurance_arrears?{RANGE}&_diag=1").get_json()["total_debt"] == before


# --------------------------------------------------------------------------- 3. void and its log are one transaction


def _break_the_log(app, action):
    run(
        app,
        f"""CREATE TRIGGER fail_log BEFORE INSERT ON activity_logs WHEN NEW.action_type = '{action}'
            BEGIN SELECT RAISE(ABORT, 'log down'); END""",
    )


def _closed_invoice(app):
    return q(app, "SELECT id FROM invoices WHERE status='closed' AND total_amount > 0 LIMIT 1")[0][0]


def test_void_is_rolled_back_when_its_log_cannot_be_written(synthetic_app):
    iid = _closed_invoice(synthetic_app)
    _break_the_log(synthetic_app, "invoice_void")
    manager(synthetic_app).post(f"/manager/invoice/{iid}/void", data={"kind": "void", "reason": "اشتباه"})
    assert q(synthetic_app, "SELECT status FROM invoices WHERE id=?", iid) == [("closed",)]
    assert q(synthetic_app, "SELECT COUNT(*) FROM invoice_voids") == [(0,)]


def test_reopen_is_rolled_back_when_its_log_cannot_be_written(synthetic_app):
    from src.common.utils import iran_now
    from src.domain.shifts import scheduled_shift

    shift, work_date = scheduled_shift(iran_now())
    iid = _closed_invoice(synthetic_app)
    run(synthetic_app, "UPDATE invoices SET work_date=?, shift=? WHERE id=?", work_date, shift, iid)
    _break_the_log(synthetic_app, "invoice_reopen")
    manager(synthetic_app).post(f"/manager/invoice/{iid}/reopen", data={"reason": "اصلاح"})
    assert q(synthetic_app, "SELECT status FROM invoices WHERE id=?", iid) == [("closed",)]
