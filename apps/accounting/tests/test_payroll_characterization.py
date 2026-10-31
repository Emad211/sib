"""Characterization / golden-master tests for the current payroll maths (audit section 14, item 3).

These tests PIN the behaviour that ships today, not the behaviour we might want:

* ``compute_payroll`` (``src/api/manager/payroll.py``): the doctor/nurse shift base
  (``base_morning`` / ``base_evening`` / ``base_night``), the doctor visit fee, the
  injection / nursing / procedure percentages and the doctor-only tax deduction.
* ``POST /manager/payroll/calculate``: the JSON the payroll page receives for a date range.
* The manager dashboard "حقوق" tile (``manager_finance.month_tiles`` -> ``month.salaries``)
  and the month balance that subtracts it.

Every test seeds synthetic rows into the isolated temp DB from the ``webapp_db`` fixture and
never opens anything under ``sib/_data/``. A failure here means a money figure moved: review
the change, do not "fix" the expectation.
"""

import re
from types import SimpleNamespace

import pytest
from conftest import CsrfClient
from flask import current_app

from src.api.manager.payroll import compute_payroll
from src.common.utils import iran_now
from src.services import manager_finance as finance

WORK_FROM = "2024-01-01"
WORK_TO = "2024-01-31"

_SETTINGS_COLUMNS = (
    "base_morning",
    "base_evening",
    "base_night",
    "visit_fee",
    "injection_percent",
    "procedure_percent",
    "tax_percent",
    "nursing_percent",
    "nurse_procedure_percent",
)


# ---------------------------------------------------------------------------
# Helpers: synthetic rows only, straight into the isolated temp DB
# ---------------------------------------------------------------------------


def _insert_staff(db, full_name, staff_type):
    cur = db.execute(
        "INSERT INTO medical_staff (full_name, staff_type, is_active) VALUES (?, ?, 1)",
        (full_name, staff_type),
    )
    db.commit()
    return cur.lastrowid


def _insert_payroll_settings(db, staff_id, **values):
    """One payroll_settings row; every column defaults to 0 (the "not configured" value)."""
    unknown = set(values) - set(_SETTINGS_COLUMNS)
    assert not unknown, f"unknown payroll setting(s): {sorted(unknown)}"
    row = {column: 0.0 for column in _SETTINGS_COLUMNS} | values
    placeholders = ", ".join("?" for _ in range(len(_SETTINGS_COLUMNS) + 1))
    db.execute(
        f"INSERT INTO payroll_settings (staff_id, {', '.join(_SETTINGS_COLUMNS)}) VALUES ({placeholders})",
        (staff_id, *(row[column] for column in _SETTINGS_COLUMNS)),
    )
    db.commit()


def _insert_patient(db, name="بیمار آزمون"):
    cur = db.execute("INSERT INTO patients (name, family_name) VALUES (?, 'آزمون')", (name,))
    db.commit()
    return cur.lastrowid


def _insert_invoice(db, patient_id, *, work_date, shift, status="closed"):
    cur = db.execute(
        "INSERT INTO invoices (patient_id, status, work_date, shift, total_amount) VALUES (?, ?, ?, ?, 0)",
        (patient_id, status, work_date, shift),
    )
    db.commit()
    return cur.lastrowid


def _insert_visit(db, patient_id, invoice_id, *, work_date, shift, doctor_id=None):
    db.execute(
        "INSERT INTO visits (patient_id, invoice_id, doctor_id, work_date, shift, price, insurance_type)"
        " VALUES (?, ?, ?, ?, ?, 0, 'آزاد')",
        (patient_id, invoice_id, doctor_id, work_date, shift),
    )
    db.commit()


def _insert_injection(db, patient_id, invoice_id, *, work_date, shift, total_price, doctor_id=None, nurse_id=None):
    db.execute(
        "INSERT INTO injections"
        " (patient_id, invoice_id, injection_type, total_price, work_date, shift, doctor_id, nurse_id)"
        " VALUES (?, ?, 'تزریق آزمون', ?, ?, ?, ?, ?)",
        (patient_id, invoice_id, total_price, work_date, shift, doctor_id, nurse_id),
    )
    db.commit()


def _insert_procedure(
    db, patient_id, invoice_id, *, work_date, shift, price, doctor_id=None, nurse_id=None, performer_type="doctor"
):
    db.execute(
        "INSERT INTO procedures"
        " (patient_id, invoice_id, procedure_type, performer_type, price, work_date, shift, doctor_id, nurse_id)"
        " VALUES (?, ?, 'کار عملی آزمون', ?, ?, ?, ?, ?, ?)",
        (patient_id, invoice_id, performer_type, price, work_date, shift, doctor_id, nurse_id),
    )
    db.commit()


def _manager_client(db, username):
    """A logged-in manager client for the temp DB (session only; the login form is not used)."""
    cur = db.execute(
        "INSERT INTO users (username, password_hash, role, full_name, is_active)"
        " VALUES (?, ?, 'manager', 'مدیر آزمون', 1)",
        (username, b"test-only"),
    )
    db.commit()
    app = current_app._get_current_object()
    app.config["SECRET_KEY"] = "test-only"  # webapp_db passes TESTING only; sessions need a key
    app.test_client_class = CsrfClient
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = cur.lastrowid
        session["role"] = "manager"
    return client


def _stat_tile(html: str, card_class: str) -> str:
    """The formatted ``.stat-value`` of one dashboard stat card (e.g. the blue "حقوق" tile)."""
    match = re.search(
        rf'<div class="stat-card {card_class}">.*?<div class="stat-value">([^<]+)</div>',
        html,
        re.DOTALL,
    )
    assert match is not None, f"no stat-card {card_class!r} in the dashboard HTML"
    return match.group(1).strip()


# ---------------------------------------------------------------------------
# Seed: one configured doctor, one configured nurse, one doctor without settings
# ---------------------------------------------------------------------------


@pytest.fixture()
def payroll_staff(webapp_db):
    """A configured doctor + nurse and an active doctor with no payroll_settings row.

    January 2024 work:

    * doctor: visits on 2 mornings (different work dates), 1 evening and 1 night;
      one 80,000 injection in the first invoice; one 120,000 procedure (15 Jan).
    * nurse: a morning invoice with a 100,000 injection and a 40,000 procedure,
      and a night invoice with a 50,000 injection (each invoice also has a visit,
      which is what the nurse's shift counter reads).
    """
    db = webapp_db
    doctor = _insert_staff(db, "دکتر آزمون", "doctor")
    nurse = _insert_staff(db, "پرستار آزمون", "nurse")
    unconfigured = _insert_staff(db, "دکتر بدون تنظیمات", "doctor")

    _insert_payroll_settings(
        db,
        doctor,
        base_morning=100000.0,
        base_evening=120000.0,
        base_night=150000.0,
        visit_fee=50000.0,
        injection_percent=25.0,
        procedure_percent=50.0,
        tax_percent=10.0,
    )
    _insert_payroll_settings(
        db,
        nurse,
        base_morning=60000.0,
        base_evening=70000.0,
        base_night=80000.0,
        nursing_percent=8.0,
        nurse_procedure_percent=35.0,
    )

    patient = _insert_patient(db)
    doctor_shifts = (
        ("2024-01-10", "morning"),
        ("2024-01-11", "morning"),
        ("2024-01-12", "evening"),
        ("2024-01-13", "night"),
    )
    doctor_invoices = []
    for work_date, shift in doctor_shifts:
        invoice = _insert_invoice(db, patient, work_date=work_date, shift=shift)
        _insert_visit(db, patient, invoice, work_date=work_date, shift=shift, doctor_id=doctor)
        doctor_invoices.append(invoice)

    _insert_injection(
        db,
        patient,
        doctor_invoices[0],
        work_date="2024-01-10",
        shift="morning",
        total_price=80000.0,
        doctor_id=doctor,
    )
    procedure_invoice = _insert_invoice(db, patient, work_date="2024-01-15", shift="morning")
    _insert_procedure(
        db,
        patient,
        procedure_invoice,
        work_date="2024-01-15",
        shift="morning",
        price=120000.0,
        doctor_id=doctor,
        performer_type="doctor",
    )

    nurse_morning = _insert_invoice(db, patient, work_date="2024-01-20", shift="morning")
    _insert_visit(db, patient, nurse_morning, work_date="2024-01-20", shift="morning")
    _insert_injection(
        db,
        patient,
        nurse_morning,
        work_date="2024-01-20",
        shift="morning",
        total_price=100000.0,
        nurse_id=nurse,
    )
    _insert_procedure(
        db,
        patient,
        nurse_morning,
        work_date="2024-01-20",
        shift="morning",
        price=40000.0,
        nurse_id=nurse,
        performer_type="nurse",
    )
    nurse_night = _insert_invoice(db, patient, work_date="2024-01-21", shift="night")
    _insert_visit(db, patient, nurse_night, work_date="2024-01-21", shift="night")
    _insert_injection(
        db,
        patient,
        nurse_night,
        work_date="2024-01-21",
        shift="night",
        total_price=50000.0,
        nurse_id=nurse,
    )

    return SimpleNamespace(doctor=doctor, nurse=nurse, unconfigured=unconfigured)


# ---------------------------------------------------------------------------
# 1. Doctor: shift base + visit fee + injection/procedure percentages - tax
# ---------------------------------------------------------------------------


class TestDoctorPayrollMaths:
    """پزشک: پایهٔ هر شیفت + حق هر ویزیت + درصد تزریقات/کار عملی، منهای مالیات."""

    def test_doctor_base_visit_fee_percentages_and_tax(self, webapp_db, payroll_staff):
        rows = compute_payroll(webapp_db, WORK_FROM, WORK_TO, staff_id=str(payroll_staff.doctor))
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == payroll_staff.doctor
        assert row["type"] == "doctor"
        assert row["type_label"] == "پزشک"
        # Shift base: 2 x 100,000 + 1 x 120,000 + 1 x 150,000 = 470,000
        # Visits:    4 closed visits x 50,000                       = 200,000
        # Injection: 80,000 x 25%                                   =  20,000
        # Procedure: 120,000 x 50%                                  =  60,000
        # Subtotal 750,000; tax 10% = 75,000                        -> 675,000
        assert row["details"] == [
            {"type": "شیفت صبح", "count": 2, "unit_price": 100000.0, "total": 200000.0},
            {"type": "شیفت عصر", "count": 1, "unit_price": 120000.0, "total": 120000.0},
            {"type": "شیفت شب", "count": 1, "unit_price": 150000.0, "total": 150000.0},
            {"type": "ویزیت", "count": 4, "unit_price": 50000.0, "total": 200000.0},
            {"type": "سهم تزریقات (25.0%)", "count": 1, "unit_price": 80000.0, "total": 20000.0},
            {"type": "سهم کار عملی (50.0%)", "count": 1, "unit_price": 120000.0, "total": 60000.0},
            {"type": "کسر مالیات (10.0%)", "count": 1, "unit_price": 750000.0, "total": -75000.0},
        ]
        assert row["total_salary"] == 675000.0


# ---------------------------------------------------------------------------
# 2. Nurse: shift base + nursing/procedure percentages, no tax
# ---------------------------------------------------------------------------


class TestNursePayrollMaths:
    """پرستار: پایهٔ هر شیفت + درصد تزریقات + درصد کار عملی؛ مالیات کم نمی‌شود."""

    def test_nurse_base_nursing_and_procedure_percentages(self, webapp_db, payroll_staff):
        rows = compute_payroll(webapp_db, WORK_FROM, WORK_TO, staff_id=str(payroll_staff.nurse))
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == payroll_staff.nurse
        assert row["type"] == "nurse"
        assert row["type_label"] == "پرستار"
        # Shift base: 1 morning x 60,000 + 1 night x 80,000 = 140,000
        # Nursing:    (100,000 + 50,000) x 8%              =  12,000
        # Procedure:  40,000 x 35%                         =  14,000
        # No tax line for nurses                           -> 166,000
        assert row["details"] == [
            {"type": "شیفت صبح", "count": 1, "unit_price": 60000.0, "total": 60000.0},
            {"type": "شیفت شب", "count": 1, "unit_price": 80000.0, "total": 80000.0},
            {"type": "سهم خدمات پرستاری (8.0%)", "count": 1, "unit_price": 150000.0, "total": 12000.0},
            {"type": "سهم کار عملی پرستار (35.0%)", "count": 1, "unit_price": 40000.0, "total": 14000.0},
        ]
        assert row["total_salary"] == 166000.0


# ---------------------------------------------------------------------------
# 3. POST /manager/payroll/calculate returns those exact figures
# ---------------------------------------------------------------------------


class TestCalculatePayrollRoute:
    """مسیر POST /manager/payroll/calculate همان اعداد سرویس را برمی‌گرداند."""

    def test_calculate_returns_the_service_figures(self, webapp_db, payroll_staff):
        client = _manager_client(webapp_db, "manager-payroll")
        response = client.post(
            "/manager/payroll/calculate",
            data={"date_from": WORK_FROM, "date_to": WORK_TO, "staff_type": "all", "shift": "all"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True
        totals = {row["id"]: row["total_salary"] for row in body["results"]}
        assert totals[payroll_staff.doctor] == 675000.0
        assert totals[payroll_staff.nurse] == 166000.0
        assert totals[payroll_staff.unconfigured] == 0.0

    def test_calculate_for_another_month_returns_zero(self, webapp_db, payroll_staff):
        client = _manager_client(webapp_db, "manager-payroll-empty")
        response = client.post(
            "/manager/payroll/calculate",
            data={"date_from": "2024-02-01", "date_to": "2024-02-29", "staff_type": "all", "shift": "all"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert {row["id"]: row["total_salary"] for row in body["results"]} == {
            payroll_staff.doctor: 0.0,
            payroll_staff.nurse: 0.0,
            payroll_staff.unconfigured: 0.0,
        }


# ---------------------------------------------------------------------------
# 4. Dashboard "حقوق" tile: month-to-date payroll, subtracted in the balance
# ---------------------------------------------------------------------------


class TestDashboardSalariesTile:
    """کاشی «حقوق» داشبورد: جمع حقوق از اول ماه، و همان عدد در «مانده» کم می‌شود."""

    def test_salaries_tile_and_balance_pin_the_month_payroll(self, webapp_db):
        db = webapp_db
        today = iran_now().date().isoformat()

        doctor = _insert_staff(db, "دکتر امروز", "doctor")
        _insert_payroll_settings(
            db,
            doctor,
            base_morning=100000.0,
            visit_fee=50000.0,
            injection_percent=25.0,
            procedure_percent=50.0,
            tax_percent=10.0,
        )
        nurse = _insert_staff(db, "پرستار امروز", "nurse")
        _insert_payroll_settings(
            db,
            nurse,
            base_morning=60000.0,
            nursing_percent=8.0,
            nurse_procedure_percent=35.0,
        )

        patient = _insert_patient(db, "بیمار امروز")
        doctor_invoice = _insert_invoice(db, patient, work_date=today, shift="morning")
        _insert_visit(db, patient, doctor_invoice, work_date=today, shift="morning", doctor_id=doctor)
        _insert_injection(
            db,
            patient,
            doctor_invoice,
            work_date=today,
            shift="morning",
            total_price=80000.0,
            doctor_id=doctor,
        )
        _insert_procedure(
            db,
            patient,
            doctor_invoice,
            work_date=today,
            shift="morning",
            price=120000.0,
            doctor_id=doctor,
            performer_type="doctor",
        )
        nurse_invoice = _insert_invoice(db, patient, work_date=today, shift="morning")
        _insert_visit(db, patient, nurse_invoice, work_date=today, shift="morning")
        _insert_injection(
            db,
            patient,
            nurse_invoice,
            work_date=today,
            shift="morning",
            total_price=100000.0,
            nurse_id=nurse,
        )
        _insert_procedure(
            db,
            patient,
            nurse_invoice,
            work_date=today,
            shift="morning",
            price=40000.0,
            nurse_id=nurse,
            performer_type="nurse",
        )

        # Doctor: 100,000 shift + 50,000 visit + 20,000 injection + 60,000 procedure
        #          = 230,000; minus 10% tax (23,000) = 207,000
        # Nurse:   60,000 shift + 8,000 nursing + 14,000 procedure =  82,000
        # Tile salaries = 289,000; no transactions, no expenses -> balance = -289,000
        tiles = finance.month_tiles(db, iran_now().date())
        assert tiles["transactions"] == 0.0
        assert tiles["expenses"] == 0.0
        assert tiles["salaries"] == 289000.0
        assert tiles["balance"] == -289000.0

        html = _manager_client(db, "manager-dashboard").get("/manager/").get_data(as_text=True)
        assert "حقوق پزشکان و پرستاران از اول ماه" in html
        assert _stat_tile(html, "blue") == "289,000"
        assert _stat_tile(html, "purple") == "-289,000"


# ---------------------------------------------------------------------------
# 5. Guards: a staff member without payroll_settings
# ---------------------------------------------------------------------------


class TestStaffWithoutPayrollSettings:
    """کارمند بدون ردیف payroll_settings: فعلاً صفر است، ولی تنظیمات صفر یعنی «پیش‌فرض کد»."""

    def test_staff_without_a_settings_row_contributes_zero(self, webapp_db, payroll_staff):
        rows = compute_payroll(webapp_db, WORK_FROM, WORK_TO)
        by_id = {row["id"]: row for row in rows}
        unconfigured = by_id[payroll_staff.unconfigured]
        assert unconfigured["type"] == "doctor"
        assert unconfigured["total_salary"] == 0.0
        # Today it is still listed and carries the hardcoded 10% doctor tax line on a zero subtotal.
        assert [line["type"] for line in unconfigured["details"]] == ["کسر مالیات (10%)"]

    def test_zero_percent_settings_fall_back_to_the_code_defaults(self, webapp_db):
        # PINNED SURPRISE: 0 in payroll_settings is falsy, so `or` replaces it with the defaults
        # (visit fee 20,000; tax 10%). A configured 0% tax is therefore charged 10%.
        db = webapp_db
        doctor = _insert_staff(db, "دکتر تنظیمات صفر", "doctor")
        _insert_payroll_settings(db, doctor)
        patient = _insert_patient(db, "بیمار تنظیمات صفر")
        invoice = _insert_invoice(db, patient, work_date="2024-01-10", shift="morning")
        _insert_visit(db, patient, invoice, work_date="2024-01-10", shift="morning", doctor_id=doctor)

        row = compute_payroll(db, WORK_FROM, WORK_TO, staff_id=str(doctor))[0]
        assert row["details"] == [
            {"type": "شیفت صبح", "count": 1, "unit_price": 0.0, "total": 0.0},
            {"type": "ویزیت", "count": 1, "unit_price": 20000, "total": 20000},
            {"type": "کسر مالیات (10%)", "count": 1, "unit_price": 20000, "total": -2000.0},
        ]
        assert row["total_salary"] == 18000.0
