import logging
from datetime import timedelta

from flask import (
    request,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.utils import iran_now
from src.services.activity_logger import ActionType
from src.services.exports import table_response

from . import bp
from ._common import jalali_to_start_end, manager_api_required
from .reports import _patients_report_rows, log_report_access

logger = logging.getLogger(__name__)


@bp.route("/export/invoices/csv")
@login_required
@manager_api_required
def export_invoices_csv():

    log_report_access(ActionType.REPORT_EXPORT, "invoices")
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    status = request.args.get("status", "").strip() or None
    insurance_type = request.args.get("insurance_type", "").strip() or None
    reception_user = request.args.get("reception_user", "").strip() or None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {"date_from": start_dt.strftime("%Y-%m-%d"), "date_to": end_dt.strftime("%Y-%m-%d")}
    where = ["i.work_date BETWEEN :date_from AND :date_to"]
    if status:
        where.append("i.status = :status")
        params["status"] = status
    if insurance_type:
        where.append("i.insurance_type = :insurance_type")
        params["insurance_type"] = insurance_type
    if reception_user:
        where.append("i.opened_by = :reception_user")
        params["reception_user"] = reception_user
    where_sql = " AND ".join(where)

    rows = db.execute(
        f"""
        SELECT i.id, i.opened_at, i.closed_at, i.status, i.total_amount,
               i.insurance_type, i.supplementary_insurance, i.opened_by, i.closed_by,
               COALESCE(u_open.full_name, i.opened_by) AS opened_by_name,
               COALESCE(u_close.full_name, i.closed_by) AS closed_by_name,
               p.full_name AS patient_name
        FROM invoices i
        JOIN patients p ON p.id = i.patient_id
        LEFT JOIN users u_open ON u_open.username = i.opened_by
        LEFT JOIN users u_close ON u_close.username = i.closed_by
        WHERE {where_sql}
        ORDER BY i.opened_at DESC
    """,
        params,
    ).fetchall()

    data = [
        [
            r["id"],
            r["opened_at"] or "",
            r["closed_at"] or "",
            r["status"] or "",
            int(r["total_amount"] or 0),
            r["insurance_type"] or "",
            r["supplementary_insurance"] or "",
            (r["opened_by_name"] or r["opened_by"] or ""),
            (r["closed_by_name"] or r["closed_by"] or ""),
            r["patient_name"] or "",
        ]
        for r in rows
    ]
    headers = [
        "شماره فاکتور",
        "زمان باز شدن",
        "زمان بستن",
        "وضعیت",
        "مبلغ کل",
        "بیمه",
        "بیمه تکمیلی",
        "پذیرش",
        "بستن توسط",
        "بیمار",
    ]
    return make_csv_response(data, headers, "invoices", plain_cols=(0,))


def make_csv_response(data, headers, filename, plain_cols=()):
    """CSV by default, Excel with ?format=xlsx (services/exports.py)."""
    return table_response(data, headers, filename, plain_cols=plain_cols)


@bp.route("/export/users/csv")
@login_required
@manager_api_required
def export_users_csv():
    """خروجی CSV گزارش عملکرد کاربران."""

    log_report_access(ActionType.REPORT_EXPORT, "users")
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    role_filter = request.args.get("role", "").strip() or None
    user_filter = request.args.get("user", "").strip() or None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    results = []

    # پذیرش
    if not role_filter or role_filter == "reception":
        reception_users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
        for u in reception_users:
            uname = u["username"]
            if user_filter and uname != user_filter:
                continue
            inv_count = db.execute(
                "SELECT COUNT(*) as cnt FROM invoices WHERE opened_by = ? AND work_date BETWEEN ? AND ?",
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            visits_count = db.execute(
                "SELECT COUNT(*) as cnt FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.opened_by = ? AND v.work_date BETWEEN ? AND ?",
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            nursing_count = db.execute(
                "SELECT COUNT(*) as cnt FROM injections inj JOIN invoices i ON i.id = inj.invoice_id WHERE i.opened_by = ? AND inj.work_date BETWEEN ? AND ?",
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            proc_count = db.execute(
                "SELECT COUNT(*) as cnt FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id WHERE i.opened_by = ? AND pr.work_date BETWEEN ? AND ?",
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            cons_count = db.execute(
                "SELECT COUNT(*) as cnt FROM consumables_ledger cl JOIN invoices i ON i.id = cl.invoice_id WHERE i.opened_by = ? AND cl.work_date BETWEEN ? AND ? AND (cl.patient_provided = 0 OR COALESCE(cl.is_exception,0) = 1)",
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            # Revenue = visits + injections + procedures (NOT consumables)
            v_rev = db.execute(
                "SELECT COALESCE(SUM(v.price), 0) as total FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'",
                (uname, start_date, end_date),
            ).fetchone()["total"]
            inj_rev = db.execute(
                "SELECT COALESCE(SUM(inj.total_price), 0) as total FROM injections inj JOIN invoices i ON i.id = inj.invoice_id WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'",
                (uname, start_date, end_date),
            ).fetchone()["total"]
            pr_rev = db.execute(
                "SELECT COALESCE(SUM(pr.price), 0) as total FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'",
                (uname, start_date, end_date),
            ).fetchone()["total"]
            revenue = v_rev + inj_rev + pr_rev
            results.append(
                [
                    uname,
                    u["full_name"],
                    "پذیرش",
                    inv_count,
                    visits_count,
                    nursing_count,
                    proc_count,
                    cons_count,
                    revenue,
                ]
            )

    # پزشکان
    if not role_filter or role_filter == "doctor":
        doctors = db.execute(
            "SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' ORDER BY full_name"
        ).fetchall()
        for doc in doctors:
            doc_id = doc["id"]
            if user_filter and str(doc_id) != user_filter:
                continue
            visits_count = db.execute(
                "SELECT COUNT(*) as cnt FROM visits WHERE doctor_id = ? AND work_date BETWEEN ? AND ?",
                (doc_id, start_date, end_date),
            ).fetchone()["cnt"]
            visits_revenue = db.execute(
                "SELECT COALESCE(SUM(price), 0) as total FROM visits v JOIN invoices i ON i.id = v.invoice_id AND i.status = 'closed' WHERE v.doctor_id = ? AND v.work_date BETWEEN ? AND ?",
                (doc_id, start_date, end_date),
            ).fetchone()["total"]
            nursing_count = db.execute(
                "SELECT COUNT(*) as cnt FROM injections WHERE doctor_id = ? AND work_date BETWEEN ? AND ?",
                (doc_id, start_date, end_date),
            ).fetchone()["cnt"]
            nursing_revenue = db.execute(
                "SELECT COALESCE(SUM(total_price), 0) as total FROM injections inj JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed' WHERE inj.doctor_id = ? AND inj.work_date BETWEEN ? AND ?",
                (doc_id, start_date, end_date),
            ).fetchone()["total"]
            proc_count = db.execute(
                "SELECT COUNT(*) as cnt FROM procedures WHERE doctor_id = ? AND performer_type = 'doctor' AND work_date BETWEEN ? AND ?",
                (doc_id, start_date, end_date),
            ).fetchone()["cnt"]
            proc_revenue = db.execute(
                "SELECT COALESCE(SUM(price), 0) as total FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed' WHERE pr.doctor_id = ? AND pr.performer_type = 'doctor' AND pr.work_date BETWEEN ? AND ?",
                (doc_id, start_date, end_date),
            ).fetchone()["total"]
            results.append(
                [
                    str(doc_id),
                    doc["full_name"],
                    "پزشک",
                    0,
                    visits_count,
                    nursing_count,
                    proc_count,
                    0,
                    visits_revenue + nursing_revenue + proc_revenue,
                ]
            )

    # پرستاران
    if not role_filter or role_filter == "nurse":
        nurses = db.execute(
            "SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' ORDER BY full_name"
        ).fetchall()
        for nurse in nurses:
            nurse_id = nurse["id"]
            if user_filter and str(nurse_id) != user_filter:
                continue
            nursing_count = db.execute(
                "SELECT COUNT(*) as cnt FROM injections WHERE nurse_id = ? AND work_date BETWEEN ? AND ?",
                (nurse_id, start_date, end_date),
            ).fetchone()["cnt"]
            nursing_revenue = db.execute(
                "SELECT COALESCE(SUM(total_price), 0) as total FROM injections inj JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed' WHERE inj.nurse_id = ? AND inj.work_date BETWEEN ? AND ?",
                (nurse_id, start_date, end_date),
            ).fetchone()["total"]
            proc_count = db.execute(
                "SELECT COUNT(*) as cnt FROM procedures WHERE nurse_id = ? AND performer_type = 'nurse' AND work_date BETWEEN ? AND ?",
                (nurse_id, start_date, end_date),
            ).fetchone()["cnt"]
            proc_revenue = db.execute(
                "SELECT COALESCE(SUM(price), 0) as total FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed' WHERE pr.nurse_id = ? AND pr.performer_type = 'nurse' AND pr.work_date BETWEEN ? AND ?",
                (nurse_id, start_date, end_date),
            ).fetchone()["total"]
            results.append(
                [
                    str(nurse_id),
                    nurse["full_name"],
                    "پرستار",
                    0,
                    0,
                    nursing_count,
                    proc_count,
                    0,
                    nursing_revenue + proc_revenue,
                ]
            )

    headers = [
        "کاربر",
        "نام کامل",
        "نقش",
        "فاکتورهای بازشده",
        "ویزیت‌ها",
        "خدمات پرستاری",
        "کارهای عملی",
        "مصرفی‌ها",
        "درآمد کل (تومان)",
    ]
    return make_csv_response(results, headers, "users_report.csv")


@bp.route("/export/visits/csv")
@login_required
@manager_api_required
def export_visits_csv():
    """خروجی CSV گزارش ویزیت‌ها."""

    log_report_access(ActionType.REPORT_EXPORT, "visits")
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    doctor_id = request.args.get("doctor_id", type=int)
    insurance_type = request.args.get("insurance_type", "").strip() or None
    reception_user = request.args.get("reception_user", "").strip() or None
    shift = request.args.get("shift", "").strip() or None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {"date_from": start_dt.strftime("%Y-%m-%d"), "date_to": end_dt.strftime("%Y-%m-%d")}
    where_clauses = ["v.work_date BETWEEN :date_from AND :date_to"]
    if doctor_id:
        where_clauses.append("v.doctor_id = :doctor_id")
        params["doctor_id"] = doctor_id
    if insurance_type:
        where_clauses.append("v.insurance_type = :insurance_type")
        params["insurance_type"] = insurance_type
    if reception_user:
        where_clauses.append("inv.opened_by = :reception_user")
        params["reception_user"] = reception_user
    if shift:
        where_clauses.append("v.shift = :shift")
        params["shift"] = shift

    where_sql = " AND ".join(where_clauses)
    visits = db.execute(
        f"""
        SELECT v.visit_date, p.full_name AS patient_name, v.doctor_name, v.insurance_type, v.supplementary_insurance, v.shift, v.price AS total_amount, v.notes
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        JOIN invoices inv ON inv.id = v.invoice_id
        WHERE {where_sql}
        ORDER BY v.visit_date DESC
    """,
        params,
    ).fetchall()

    data = []
    for v in visits:
        data.append(
            [
                v["visit_date"],
                v["patient_name"],
                v["doctor_name"] or "",
                v["insurance_type"] or "",
                v["supplementary_insurance"] or "",
                v["shift"] or "",
                v["total_amount"] or 0,
                v["notes"] or "",
            ]
        )

    headers = ["تاریخ ویزیت", "بیمار", "پزشک", "بیمه", "بیمه تکمیلی", "شیفت", "مبلغ (تومان)", "یادداشت"]
    return make_csv_response(data, headers, "visits_report.csv")


@bp.route("/export/nursing/csv")
@login_required
@manager_api_required
def export_nursing_csv():
    """خروجی CSV گزارش خدمات پرستاری."""

    log_report_access(ActionType.REPORT_EXPORT, "nursing")
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    doctor_id = request.args.get("doctor_id", type=int)
    nurse_id = request.args.get("nurse_id", type=int)
    service_name = request.args.get("service_name", "").strip() or None
    shift = request.args.get("shift", "").strip() or None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {"date_from": start_dt.strftime("%Y-%m-%d"), "date_to": end_dt.strftime("%Y-%m-%d")}
    where_clauses = ["i.work_date BETWEEN :date_from AND :date_to"]
    if doctor_id:
        where_clauses.append("i.doctor_id = :doctor_id")
        params["doctor_id"] = doctor_id
    if nurse_id:
        where_clauses.append("i.nurse_id = :nurse_id")
        params["nurse_id"] = nurse_id
    if service_name:
        where_clauses.append("i.injection_type = :stype")
        params["stype"] = service_name
    if shift:
        where_clauses.append("i.shift = :shift")
        params["shift"] = shift

    where_sql = " AND ".join(where_clauses)
    rows = db.execute(
        f"""
        SELECT i.injection_date, p.full_name AS patient_name, i.injection_type, i.count, i.unit_price, i.total_price, doc.full_name AS doctor_name, nurse.full_name AS nurse_name, i.shift, i.notes
        FROM injections i
        JOIN patients p ON p.id = i.patient_id
        JOIN invoices inv ON inv.id = i.invoice_id AND inv.status = 'closed'
        LEFT JOIN medical_staff doc ON doc.id = i.doctor_id
        LEFT JOIN medical_staff nurse ON nurse.id = i.nurse_id
        WHERE {where_sql}
        ORDER BY i.injection_date DESC
    """,
        params,
    ).fetchall()

    data = []
    for r in rows:
        data.append(
            [
                r["injection_date"],
                r["patient_name"],
                r["injection_type"] or "",
                r["count"] or 1,
                r["unit_price"] or 0,
                r["total_price"] or 0,
                r["doctor_name"] or "",
                r["nurse_name"] or "",
                r["shift"] or "",
                r["notes"] or "",
            ]
        )

    headers = ["تاریخ", "بیمار", "نوع خدمت", "تعداد", "مبلغ واحد", "مبلغ کل", "پزشک", "پرستار", "شیفت", "یادداشت"]
    return make_csv_response(data, headers, "nursing_report.csv")


@bp.route("/export/procedures/csv")
@login_required
@manager_api_required
def export_procedures_csv():
    """خروجی CSV گزارش کارهای عملی."""

    log_report_access(ActionType.REPORT_EXPORT, "procedures")
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    doctor_id = request.args.get("doctor_id", type=int)
    nurse_id = request.args.get("nurse_id", type=int)
    performer_type = request.args.get("performer_type", "").strip() or None
    procedure_type = request.args.get("procedure_type", "").strip() or None
    shift = request.args.get("shift", "").strip() or None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {"date_from": start_dt.strftime("%Y-%m-%d"), "date_to": end_dt.strftime("%Y-%m-%d")}
    where_clauses = ["pr.work_date BETWEEN :date_from AND :date_to"]
    if doctor_id:
        where_clauses.append("pr.doctor_id = :doctor_id")
        params["doctor_id"] = doctor_id
    if nurse_id:
        where_clauses.append("pr.nurse_id = :nurse_id")
        params["nurse_id"] = nurse_id
    if performer_type:
        where_clauses.append("pr.performer_type = :ptype")
        params["ptype"] = performer_type
    if procedure_type:
        where_clauses.append("pr.procedure_type = :prtype")
        params["prtype"] = procedure_type
    if shift:
        where_clauses.append("pr.shift = :shift")
        params["shift"] = shift

    where_sql = " AND ".join(where_clauses)
    rows = db.execute(
        f"""
        SELECT pr.procedure_date, p.full_name AS patient_name, pr.procedure_type, pr.performer_type, doc.full_name AS doctor_name, nurse.full_name AS nurse_name, pr.shift, pr.price, pr.notes
        FROM procedures pr
        JOIN patients p ON p.id = pr.patient_id
        JOIN invoices inv ON inv.id = pr.invoice_id AND inv.status = 'closed'
        LEFT JOIN medical_staff doc ON doc.id = pr.doctor_id
        LEFT JOIN medical_staff nurse ON nurse.id = pr.nurse_id
        WHERE {where_sql}
        ORDER BY pr.procedure_date DESC
    """,
        params,
    ).fetchall()

    data = []
    for r in rows:
        # تبدیل performer_type به فارسی
        performer_fa = (
            "پزشک" if r["performer_type"] == "doctor" else ("پرستار" if r["performer_type"] == "nurse" else "")
        )
        data.append(
            [
                r["procedure_date"],
                r["patient_name"],
                r["procedure_type"] or "",
                performer_fa,
                r["doctor_name"] or "",
                r["nurse_name"] or "",
                r["shift"] or "",
                r["price"] or 0,
                r["notes"] or "",
            ]
        )

    headers = ["تاریخ", "بیمار", "نوع کار", "انجام‌دهنده", "پزشک", "پرستار", "شیفت", "مبلغ (تومان)", "یادداشت"]
    return make_csv_response(data, headers, "procedures_report.csv")


@bp.route("/export/consumables/csv")
@login_required
@manager_api_required
def export_consumables_csv():
    """خروجی CSV گزارش مصرفی‌ها."""

    log_report_access(ActionType.REPORT_EXPORT, "consumables")
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    item_name = request.args.get("item_name", "").strip() or None
    category = request.args.get("category", "").strip() or None
    brought_by_patient = request.args.get("brought_by_patient", "").strip() or None
    shift = request.args.get("shift", "").strip() or None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {"date_from": start_dt.strftime("%Y-%m-%d"), "date_to": end_dt.strftime("%Y-%m-%d")}
    # Exclude patient-provided consumables from manager CSV export, but include exceptions
    where_clauses = [
        "cl.work_date BETWEEN :date_from AND :date_to",
        "(COALESCE(cl.patient_provided,0) = 0 OR COALESCE(cl.is_exception,0) = 1)",
    ]

    if item_name:
        where_clauses.append("cl.item_name LIKE :item_name")
        params["item_name"] = f"%{item_name}%"
    if category:
        where_clauses.append("cl.category = :category")
        params["category"] = category
    if brought_by_patient:
        where_clauses.append("cl.patient_provided = :bbp")
        params["bbp"] = int(brought_by_patient)
    if shift:
        where_clauses.append("cl.shift = :shift")
        params["shift"] = shift

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(
        f"""
        SELECT cl.usage_date, p.full_name AS patient_name, cl.item_name, cl.category, cl.quantity, cl.unit_price, cl.total_cost, cl.patient_provided, cl.shift, cl.notes
        FROM consumables_ledger cl
        JOIN patients p ON p.id = cl.patient_id
        JOIN invoices inv ON inv.id = cl.invoice_id AND inv.status = 'closed'
        WHERE {where_sql}
        ORDER BY cl.usage_date DESC
    """,
        params,
    ).fetchall()

    data = []
    for r in rows:
        cat_fa = "دارو" if r["category"] == "drug" else "عمومی"
        bbp_fa = "بله" if r["patient_provided"] else "خیر"
        data.append(
            [
                r["usage_date"],
                r["patient_name"],
                r["item_name"],
                cat_fa,
                r["quantity"] or 1,
                r["unit_price"] or 0,
                r["total_cost"] or 0,
                bbp_fa,
                r["shift"] or "",
                r["notes"] or "",
            ]
        )

    headers = ["تاریخ", "بیمار", "نام قلم", "دسته", "تعداد", "مبلغ واحد", "مبلغ کل", "آورده بیمار", "شیفت", "یادداشت"]
    return make_csv_response(data, headers, "consumables_report.csv")


@bp.route("/export/patients/csv")
@login_required
@manager_api_required
def export_patients_csv():
    """خروجی CSV گزارش بیماران."""

    log_report_access(ActionType.REPORT_EXPORT, "patients")
    db = get_db()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    search_name = request.args.get("search_name", "").strip() or None
    insurance_type = request.args.get("insurance_type", "").strip() or None
    gender = request.args.get("gender", "").strip() or None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=29)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    data = [
        [
            r["full_name"],
            r["national_id"],
            r["phone"],
            r["insurance_type"] or "",
            r["invoices_count"],
            r["visits_count"],
            r["total_paid"],
            r["last_visit"] or "",
        ]
        for r in _patients_report_rows(db, start_date, end_date, search_name, insurance_type)
    ]
    headers = [
        "نام بیمار",
        "کد ملی",
        "تلفن",
        "بیمه",
        "تعداد فاکتور",
        "تعداد ویزیت",
        "جمع پرداخت (تومان)",
        "آخرین مراجعه",
    ]
    return make_csv_response(data, headers, "patients_report.csv")
