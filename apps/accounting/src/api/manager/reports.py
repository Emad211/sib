import logging
from datetime import timedelta

from flask import (
    render_template,
    request,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.jalali import Gregorian
from src.common.utils import iran_now
from src.services.activity_logger import ActionCategory, ActionType, log_activity

from . import bp
from ._common import jalali_to_start_end, manager_required

logger = logging.getLogger(__name__)


def log_report_access(action_type: str, report: str) -> None:
    """ثبت «چه کسی کدام گزارش را دید یا خروجی گرفت» (E02).

    فقط نام گزارش و بازهٔ درخواستی ثبت می‌شود؛ هرگز نام یا مشخصات بیمار، عبارت جستجو یا
    محتوای گزارش. لاگ هرگز نباید گزارش را بشکند: log_activity خطای خودش را می‌بلعد و
    این تابع هر خطای دیگری را هم می‌گیرد.
    """
    try:
        date_from = request.args.get("from", "") or "—"
        date_to = request.args.get("to", "") or "—"
        log_activity(
            action_type,
            ActionCategory.REPORT,
            target_type="report",
            target_name=report,
            new_value=f"{date_from}..{date_to}",
        )
    except Exception:
        logger.warning("report access log failed for %s", report, exc_info=True)


@bp.route("/reports")
@login_required
@manager_required
def reports():
    """Financial and operational reports."""
    return render_template("manager/reports.html")


@bp.route("/reports/invoices")
@login_required
@manager_required
def invoices_report():
    """گزارش فاکتورها با فیلترهای دقیق و تاریخ شمسی شش‌تایی."""

    log_report_access(ActionType.REPORT_VIEW, "invoices")
    db = get_db()

    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    status = request.args.get("status", "").strip() or None  # open/closed
    insurance_type = request.args.get("insurance_type", "").strip() or None
    reception_user = request.args.get("reception_user", "").strip() or None
    special = request.args.get("special") == "1"  # FB-25: exceptions only

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
    if special:
        where.append(
            """(i.status = 'voided'
               OR EXISTS (SELECT 1 FROM consumables_ledger c WHERE c.invoice_id = i.id AND COALESCE(c.is_exception, 0) = 1)
               OR EXISTS (SELECT 1 FROM activity_logs a WHERE a.invoice_id = i.id AND a.action_type = 'invoice_reopen'))"""
        )
    where_sql = " AND ".join(where)

    rows = db.execute(
        f"""
        SELECT i.id, i.opened_at, i.closed_at, i.status, i.total_amount,
               i.insurance_type, i.supplementary_insurance, i.opened_by, i.closed_by,
               COALESCE(i.opened_by_name, u_open.full_name, i.opened_by) AS opened_by_name,
               COALESCE(i.closed_by_name, u_close.full_name, i.closed_by) AS closed_by_name,
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
    invoices = [dict(r) for r in rows]

    total_count = len(invoices)
    total_closed = sum(1 for r in invoices if r["status"] == "closed")
    total_open = sum(1 for r in invoices if r["status"] == "open")
    total_voided = sum(1 for r in invoices if r["status"] == "voided")
    total_amount = sum((r["total_amount"] or 0) for r in invoices if r["status"] == "closed")  # voided/open excluded

    # فیلترها
    users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
    insurances = db.execute(
        "SELECT DISTINCT insurance_type FROM invoices WHERE insurance_type IS NOT NULL ORDER BY insurance_type"
    ).fetchall()

    # رنج‌های شمسی
    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    def jalali_range(days):
        g_end = g_today
        g_start = add_days_gregorian(g_end, -(days - 1))
        j_end = Gregorian(g_end).persian_tuple()
        j_start = Gregorian(g_start).persian_tuple()
        return {
            "from": {"y": j_start[0], "m": j_start[1], "d": j_start[2]},
            "to": {"y": j_end[0], "m": j_end[1], "d": j_end[2]},
        }

    ranges = {
        "today": {"y": j_today[0], "m": j_today[1], "d": j_today[2]},
        "r7": jalali_range(7),
        "r30": jalali_range(30),
        "r90": jalali_range(90),
    }

    return render_template(
        "manager/reports_invoices.html",
        invoices=invoices,
        total_count=total_count,
        total_closed=total_closed,
        total_open=total_open,
        total_voided=total_voided,
        total_amount=total_amount,
        users=users,
        insurances=insurances,
        jalali_ranges=ranges,
        active_filters={
            "from": date_from,
            "to": date_to,
            "status": status,
            "insurance_type": insurance_type,
            "reception_user": reception_user,
            "special": "1" if special else None,
        },
    )


@bp.route("/reports/visits")
@login_required
@manager_required
def visits_report():
    """گزارش مدیریتی ویزیت‌ها با فیلترهای تاریخ، پزشک، بیمه، پذیرش و شیفت."""

    log_report_access(ActionType.REPORT_VIEW, "visits")
    db = get_db()

    # دریافت فیلترها
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    doctor_id = request.args.get("doctor_id", type=int)
    insurance_type = request.args.get("insurance_type", "").strip() or None
    reception_user = request.args.get("reception_user", "").strip() or None
    shift = request.args.get("shift", "").strip() or None

    # تبدیل شمسی به میلادی (شروع/پایان روز)
    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)

    # پیش‌فرض: 7 روز گذشته در صورت عدم ارسال
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)

    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {"date_from": start_dt.strftime("%Y-%m-%d"), "date_to": end_dt.strftime("%Y-%m-%d")}

    # Only closed invoices count, like every other report and the dashboard.
    where_clauses = ["v.work_date BETWEEN :date_from AND :date_to", "inv.status = 'closed'"]

    if doctor_id:
        where_clauses.append("(v.doctor_id = :doctor_id)")
        params["doctor_id"] = doctor_id

    if insurance_type:
        where_clauses.append("v.insurance_type = :insurance_type")
        params["insurance_type"] = insurance_type

    if reception_user:
        # از فاکتور بازکننده به عنوان کاربر پذیرش استفاده می‌کنیم
        where_clauses.append("inv.opened_by = :reception_user")
        params["reception_user"] = reception_user

    if shift:
        where_clauses.append("v.shift = :shift")
        params["shift"] = shift

    where_sql = " AND ".join(where_clauses)

    # داده‌های ریز ویزیت‌ها
    visits = db.execute(
        f"""
        SELECT v.id, v.visit_date, v.insurance_type, v.supplementary_insurance,
               v.price AS total_amount, v.notes,
               p.full_name AS patient_name,
               v.doctor_name,
               v.shift,
               inv.opened_by AS reception_user
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        JOIN invoices inv ON inv.id = v.invoice_id
        WHERE {where_sql}
        ORDER BY v.visit_date DESC
    """,
        params,
    ).fetchall()

    visits_list = [dict(r) for r in visits]

    # خلاصه‌ها
    total_visits = len(visits_list)
    total_amount = sum(r["total_amount"] or 0 for r in visits_list)
    unique_patients = len({r["patient_name"] for r in visits_list})

    # لیست‌های کمکی برای فیلتر (پزشک، کاربر، بیمه)
    doctors = db.execute(
        "SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' AND is_active=1 ORDER BY full_name"
    ).fetchall()
    users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
    insurances = db.execute(
        "SELECT DISTINCT insurance_type FROM visits WHERE insurance_type IS NOT NULL ORDER BY insurance_type"
    ).fetchall()

    # آماده‌سازی رنج‌های شمسی برای UI (استفاده مجدد از منطق داشبورد)
    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    def jalali_range(days):
        g_end = g_today
        g_start = add_days_gregorian(g_end, -(days - 1))
        j_end = Gregorian(g_end).persian_tuple()
        j_start = Gregorian(g_start).persian_tuple()
        return {
            "from": {"y": j_start[0], "m": j_start[1], "d": j_start[2]},
            "to": {"y": j_end[0], "m": j_end[1], "d": j_end[2]},
        }

    ranges = {
        "today": {"y": j_today[0], "m": j_today[1], "d": j_today[2]},
        "r7": jalali_range(7),
        "r30": jalali_range(30),
        "r90": jalali_range(90),
    }

    return render_template(
        "manager/reports_visits.html",
        visits=visits_list,
        total_visits=total_visits,
        total_amount=total_amount,
        unique_patients=unique_patients,
        doctors=doctors,
        users=users,
        insurances=insurances,
        jalali_ranges=ranges,
        active_filters={
            "from": date_from,
            "to": date_to,
            "doctor_id": doctor_id,
            "insurance_type": insurance_type,
            "reception_user": reception_user,
            "shift": shift,
        },
    )


@bp.route("/reports/nursing")
@login_required
@manager_required
def nursing_report():
    """گزارش مدیریتی خدمات پرستاری (تزریقات/سرم‌ها) با فیلترهای غنی."""

    log_report_access(ActionType.REPORT_VIEW, "nursing")
    db = get_db()

    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    doctor_id = request.args.get("doctor_id", type=int)
    nurse_id = request.args.get("nurse_id", type=int)
    service_name = request.args.get("service_name", "").strip() or None
    shift = request.args.get("shift", "").strip() or None
    related_to_doctor = request.args.get("related_doctor") == "1"

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
    if related_to_doctor:
        where_clauses.append("EXISTS (SELECT 1 FROM visits v WHERE v.invoice_id = i.invoice_id)")

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(
        f"""
        SELECT i.id, i.injection_date, i.injection_type, i.count, i.unit_price, i.total_price, i.notes,
               i.shift,
               p.full_name AS patient_name,
               doc.full_name AS doctor_name,
               nurse.full_name AS nurse_name
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

    injections = [dict(r) for r in rows]
    total_services = len(injections)
    total_amount = sum(r["total_price"] or 0 for r in injections)

    doctors = db.execute(
        "SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' AND is_active=1 ORDER BY full_name"
    ).fetchall()
    nurses = db.execute(
        "SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' AND is_active=1 ORDER BY full_name"
    ).fetchall()
    service_names = db.execute("SELECT DISTINCT injection_type FROM injections ORDER BY injection_type").fetchall()

    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    def jalali_range(days):
        g_end = g_today
        g_start = add_days_gregorian(g_end, -(days - 1))
        j_end = Gregorian(g_end).persian_tuple()
        j_start = Gregorian(g_start).persian_tuple()
        return {
            "from": {"y": j_start[0], "m": j_start[1], "d": j_start[2]},
            "to": {"y": j_end[0], "m": j_end[1], "d": j_end[2]},
        }

    ranges = {
        "today": {"y": j_today[0], "m": j_today[1], "d": j_today[2]},
        "r7": jalali_range(7),
        "r30": jalali_range(30),
        "r90": jalali_range(90),
    }

    return render_template(
        "manager/reports_nursing.html",
        injections=injections,
        total_services=total_services,
        total_amount=total_amount,
        doctors=doctors,
        nurses=nurses,
        service_names=service_names,
        jalali_ranges=ranges,
        active_filters={
            "from": date_from,
            "to": date_to,
            "doctor_id": doctor_id,
            "nurse_id": nurse_id,
            "service_name": service_name,
            "shift": shift,
            "related_doctor": "1" if related_to_doctor else "0",
        },
    )


@bp.route("/reports/procedures")
@login_required
@manager_required
def procedures_report():
    """گزارش مدیریتی کارهای عملی با فیلترهای تاریخ، نوع کار، انجام‌دهنده، پزشک/پرستار و شیفت."""

    log_report_access(ActionType.REPORT_VIEW, "procedures")
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
        SELECT pr.id, pr.procedure_date, pr.procedure_type, pr.price, pr.notes,
               pr.performer_type, pr.shift,
               p.full_name AS patient_name,
               doc.full_name AS doctor_name,
               nurse.full_name AS nurse_name
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

    procedures = [dict(r) for r in rows]
    total_count = len(procedures)
    total_amount = sum(r["price"] or 0 for r in procedures)

    doctors = db.execute(
        "SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' AND is_active=1 ORDER BY full_name"
    ).fetchall()
    nurses = db.execute(
        "SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' AND is_active=1 ORDER BY full_name"
    ).fetchall()
    procedure_types = db.execute("SELECT DISTINCT procedure_type FROM procedures ORDER BY procedure_type").fetchall()

    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    def jalali_range(days):
        g_end = g_today
        g_start = add_days_gregorian(g_end, -(days - 1))
        j_end = Gregorian(g_end).persian_tuple()
        j_start = Gregorian(g_start).persian_tuple()
        return {
            "from": {"y": j_start[0], "m": j_start[1], "d": j_start[2]},
            "to": {"y": j_end[0], "m": j_end[1], "d": j_end[2]},
        }

    ranges = {
        "today": {"y": j_today[0], "m": j_today[1], "d": j_today[2]},
        "r7": jalali_range(7),
        "r30": jalali_range(30),
        "r90": jalali_range(90),
    }

    return render_template(
        "manager/reports_procedures.html",
        procedures=procedures,
        total_count=total_count,
        total_amount=total_amount,
        doctors=doctors,
        nurses=nurses,
        procedure_types=procedure_types,
        jalali_ranges=ranges,
        active_filters={
            "from": date_from,
            "to": date_to,
            "doctor_id": doctor_id,
            "nurse_id": nurse_id,
            "performer_type": performer_type,
            "procedure_type": procedure_type,
            "shift": shift,
        },
    )


@bp.route("/reports/users")
@login_required
@manager_required
def users_report():
    """گزارش عملکرد کاربران (پزشکان، پرستاران، پذیرش) با فیلترهای تاریخ، نقش و کاربر."""

    log_report_access(ActionType.REPORT_VIEW, "users")
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

    # ===== عملکرد کاربران پذیرش (reception) =====
    if not role_filter or role_filter == "reception":
        # only list users with role 'reception' here — avoid showing managers or other roles
        reception_users = db.execute(
            "SELECT username, full_name FROM users WHERE role = 'reception' ORDER BY full_name"
        ).fetchall()
        for u in reception_users:
            uname = u["username"]
            if user_filter and uname != user_filter:
                continue
            # فاکتورهای باز شده
            inv_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM invoices
                WHERE opened_by = ? AND work_date BETWEEN ? AND ? AND status != 'voided'
            """,
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            # ویزیت‌ها - بر اساس reception_user (کسی که ویزیت را ثبت کرده)
            visits_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM visits v
                WHERE v.reception_user = ? AND v.work_date BETWEEN ? AND ?
            """,
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            # خدمات پرستاری - بر اساس reception_user (کسی که تزریق را ثبت کرده)
            nursing_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM injections inj
                WHERE inj.reception_user = ? AND inj.work_date BETWEEN ? AND ?
            """,
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            # کارهای عملی - بر اساس reception_user
            proc_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM procedures pr
                WHERE pr.reception_user = ? AND pr.work_date BETWEEN ? AND ?
            """,
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            # مصرفی‌ها - بر اساس reception_user - جدا محاسبه کن: عمومی (supply) و دارو (drug)
            cons_supply_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM consumables_ledger cl
                WHERE cl.reception_user = ? AND cl.work_date BETWEEN ? AND ? AND cl.category = 'supply' AND (cl.patient_provided = 0 OR COALESCE(cl.is_exception,0) = 1)
            """,
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            cons_drug_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM consumables_ledger cl
                WHERE cl.reception_user = ? AND cl.work_date BETWEEN ? AND ? AND cl.category = 'drug' AND (cl.patient_provided = 0 OR COALESCE(cl.is_exception,0) = 1)
            """,
                (uname, start_date, end_date),
            ).fetchone()["cnt"]
            # درآمد کل (فاکتورهای بسته) - بر اساس reception_user - بدون مصرفی
            # Revenue = visits + injections + procedures (NOT consumables)
            visits_rev = db.execute(
                """
                SELECT COALESCE(SUM(v.price), 0) as total FROM visits v
                JOIN invoices i ON i.id = v.invoice_id
                WHERE v.reception_user = ? AND v.work_date BETWEEN ? AND ? AND i.status = 'closed'
            """,
                (uname, start_date, end_date),
            ).fetchone()["total"]
            injections_rev = db.execute(
                """
                SELECT COALESCE(SUM(inj.total_price), 0) as total FROM injections inj
                JOIN invoices i ON i.id = inj.invoice_id
                WHERE inj.reception_user = ? AND inj.work_date BETWEEN ? AND ? AND i.status = 'closed'
            """,
                (uname, start_date, end_date),
            ).fetchone()["total"]
            procedures_rev = db.execute(
                """
                SELECT COALESCE(SUM(pr.price), 0) as total FROM procedures pr
                JOIN invoices i ON i.id = pr.invoice_id
                WHERE pr.reception_user = ? AND pr.work_date BETWEEN ? AND ? AND i.status = 'closed'
            """,
                (uname, start_date, end_date),
            ).fetchone()["total"]
            revenue = visits_rev + injections_rev + procedures_rev

            results.append(
                {
                    "user": uname,
                    "full_name": u["full_name"],
                    "role": "reception",
                    "role_fa": "پذیرش",
                    "invoices": inv_count,
                    "visits": visits_count,
                    "nursing": nursing_count,
                    "procedures": proc_count,
                    "consumables_supply": cons_supply_count,
                    "consumables_drug": cons_drug_count,
                    "revenue": revenue,
                }
            )

    # ===== عملکرد پزشکان (doctor) =====
    if not role_filter or role_filter == "doctor":
        doctors = db.execute(
            "SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' ORDER BY full_name"
        ).fetchall()
        for doc in doctors:
            doc_id = doc["id"]
            if user_filter and str(doc_id) != user_filter:
                continue
            # ویزیت‌ها
            visits_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM visits
                WHERE doctor_id = ? AND work_date BETWEEN ? AND ?
            """,
                (doc_id, start_date, end_date),
            ).fetchone()["cnt"]
            visits_revenue = db.execute(
                """
                SELECT COALESCE(SUM(price), 0) as total FROM visits v
                JOIN invoices i ON i.id = v.invoice_id AND i.status = 'closed'
                WHERE v.doctor_id = ? AND v.work_date BETWEEN ? AND ?
            """,
                (doc_id, start_date, end_date),
            ).fetchone()["total"]
            # خدمات پرستاری تحت نظر این پزشک - فقط زمانی که در همان فاکتور ویزیت هم وجود دارد
            nursing_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM injections inj
                WHERE inj.doctor_id = ? AND inj.work_date BETWEEN ? AND ?
                AND EXISTS (SELECT 1 FROM visits v WHERE v.invoice_id = inj.invoice_id AND v.doctor_id = inj.doctor_id)
            """,
                (doc_id, start_date, end_date),
            ).fetchone()["cnt"]
            nursing_revenue = db.execute(
                """
                SELECT COALESCE(SUM(inj.total_price), 0) as total FROM injections inj
                JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed'
                WHERE inj.doctor_id = ? AND inj.work_date BETWEEN ? AND ?
                AND EXISTS (SELECT 1 FROM visits v WHERE v.invoice_id = inj.invoice_id AND v.doctor_id = inj.doctor_id)
            """,
                (doc_id, start_date, end_date),
            ).fetchone()["total"]
            # کارهای عملی - فقط کارهایی که پزشک انجام‌دهنده بوده (performer_type='doctor')
            proc_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM procedures
                WHERE doctor_id = ? AND performer_type = 'doctor' AND work_date BETWEEN ? AND ?
            """,
                (doc_id, start_date, end_date),
            ).fetchone()["cnt"]
            proc_revenue = db.execute(
                """
                SELECT COALESCE(SUM(price), 0) as total FROM procedures pr
                JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed'
                WHERE pr.doctor_id = ? AND pr.performer_type = 'doctor' AND pr.work_date BETWEEN ? AND ?
            """,
                (doc_id, start_date, end_date),
            ).fetchone()["total"]

            total_revenue = visits_revenue + nursing_revenue + proc_revenue

            results.append(
                {
                    "user": str(doc_id),
                    "full_name": doc["full_name"],
                    "role": "doctor",
                    "role_fa": "پزشک",
                    "invoices": 0,
                    "visits": visits_count,
                    "nursing": nursing_count,
                    "procedures": proc_count,
                    "consumables": 0,
                    "revenue": total_revenue,
                }
            )

    # ===== عملکرد پرستاران (nurse) =====
    if not role_filter or role_filter == "nurse":
        nurses = db.execute(
            "SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' ORDER BY full_name"
        ).fetchall()
        for nurse in nurses:
            nurse_id = nurse["id"]
            if user_filter and str(nurse_id) != user_filter:
                continue
            # خدمات پرستاری
            nursing_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM injections
                WHERE nurse_id = ? AND work_date BETWEEN ? AND ?
            """,
                (nurse_id, start_date, end_date),
            ).fetchone()["cnt"]
            nursing_revenue = db.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total FROM injections inj
                JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed'
                WHERE inj.nurse_id = ? AND inj.work_date BETWEEN ? AND ?
            """,
                (nurse_id, start_date, end_date),
            ).fetchone()["total"]
            # کارهای عملی - فقط کارهایی که پرستار انجام‌دهنده بوده (performer_type='nurse')
            proc_count = db.execute(
                """
                SELECT COUNT(*) as cnt FROM procedures
                WHERE nurse_id = ? AND performer_type = 'nurse' AND work_date BETWEEN ? AND ?
            """,
                (nurse_id, start_date, end_date),
            ).fetchone()["cnt"]
            proc_revenue = db.execute(
                """
                SELECT COALESCE(SUM(price), 0) as total FROM procedures pr
                JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed'
                WHERE pr.nurse_id = ? AND pr.performer_type = 'nurse' AND pr.work_date BETWEEN ? AND ?
            """,
                (nurse_id, start_date, end_date),
            ).fetchone()["total"]

            total_revenue = nursing_revenue + proc_revenue

            results.append(
                {
                    "user": str(nurse_id),
                    "full_name": nurse["full_name"],
                    "role": "nurse",
                    "role_fa": "پرستار",
                    "invoices": 0,
                    "visits": 0,
                    "nursing": nursing_count,
                    "procedures": proc_count,
                    "consumables": 0,
                    "revenue": total_revenue,
                }
            )

    # لیست کاربران و نقش‌ها برای فیلتر
    all_users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
    all_doctors = db.execute(
        "SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' ORDER BY full_name"
    ).fetchall()
    all_nurses = db.execute(
        "SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' ORDER BY full_name"
    ).fetchall()

    # رنج‌های شمسی
    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    def jalali_range(days):
        g_end = g_today
        g_start = add_days_gregorian(g_end, -(days - 1))
        j_end = Gregorian(g_end).persian_tuple()
        j_start = Gregorian(g_start).persian_tuple()
        return {
            "from": {"y": j_start[0], "m": j_start[1], "d": j_start[2]},
            "to": {"y": j_end[0], "m": j_end[1], "d": j_end[2]},
        }

    ranges = {
        "today": {"y": j_today[0], "m": j_today[1], "d": j_today[2]},
        "r7": jalali_range(7),
        "r30": jalali_range(30),
        "r90": jalali_range(90),
    }

    return render_template(
        "manager/reports_users.html",
        results=results,
        all_users=all_users,
        all_doctors=all_doctors,
        all_nurses=all_nurses,
        jalali_ranges=ranges,
        active_filters={
            "from": date_from,
            "to": date_to,
            "role": role_filter,
            "user": user_filter,
        },
    )


@bp.route("/reports/consumables")
@login_required
@manager_required
def consumables_report():
    """گزارش مدیریتی مصرفی‌ها (دارو و عمومی) با فیلترهای غنی."""

    log_report_access(ActionType.REPORT_VIEW, "consumables")
    db = get_db()

    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    item_name = request.args.get("item_name", "").strip() or None
    category = request.args.get("category", "").strip() or None  # drug/supply
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
    # Exclude patient-provided consumables from manager reports, but include those
    # explicitly marked as exceptions (is_exception=1)
    # Only show consumables that were provided by the center and not exceptions by default
    where_clauses = [
        "cl.work_date BETWEEN :date_from AND :date_to",
        "(COALESCE(cl.patient_provided,0) = 0 AND COALESCE(cl.is_exception,0) = 0)",
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
        SELECT cl.id, cl.usage_date, cl.item_name, cl.category, cl.quantity, cl.unit_price, cl.total_cost,
               cl.patient_provided, cl.shift, cl.notes, cl.reception_user,
               p.full_name AS patient_name
        FROM consumables_ledger cl
        JOIN patients p ON p.id = cl.patient_id
        JOIN invoices inv ON inv.id = cl.invoice_id AND inv.status = 'closed'
        WHERE {where_sql}
        ORDER BY cl.usage_date DESC
    """,
        params,
    ).fetchall()

    consumables = [dict(r) for r in rows]
    total_count = len(consumables)
    total_amount = sum(r["total_cost"] or 0 for r in consumables)
    drugs_count = sum(1 for r in consumables if r["category"] == "drug")
    supplies_count = sum(1 for r in consumables if r["category"] == "supply")

    # FB-27: one line per item x shift x receptionist (same item used many times = one row)
    agg = {}
    for r in consumables:
        key = (r["item_name"], r["shift"] or "", r["reception_user"] or "")
        a = agg.setdefault(
            key, {"item_name": key[0], "shift": key[1], "reception_user": key[2], "quantity": 0, "total": 0}
        )
        a["quantity"] += r["quantity"] or 0
        a["total"] += r["total_cost"] or 0
    summary = sorted(agg.values(), key=lambda a: (-a["quantity"], a["item_name"]))

    # لیست آیتم‌ها برای فیلتر
    item_names = db.execute(
        "SELECT DISTINCT item_name FROM consumables_ledger WHERE (COALESCE(patient_provided,0) = 0 AND COALESCE(is_exception,0) = 0) ORDER BY item_name"
    ).fetchall()

    # رنج‌های شمسی
    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    def jalali_range(days):
        g_end = g_today
        g_start = add_days_gregorian(g_end, -(days - 1))
        j_end = Gregorian(g_end).persian_tuple()
        j_start = Gregorian(g_start).persian_tuple()
        return {
            "from": {"y": j_start[0], "m": j_start[1], "d": j_start[2]},
            "to": {"y": j_end[0], "m": j_end[1], "d": j_end[2]},
        }

    ranges = {
        "today": {"y": j_today[0], "m": j_today[1], "d": j_today[2]},
        "r7": jalali_range(7),
        "r30": jalali_range(30),
        "r90": jalali_range(90),
    }

    return render_template(
        "manager/reports_consumables.html",
        consumables=consumables,
        summary=summary,
        total_count=total_count,
        total_amount=total_amount,
        drugs_count=drugs_count,
        supplies_count=supplies_count,
        item_names=item_names,
        jalali_ranges=ranges,
        active_filters={
            "from": date_from,
            "to": date_to,
            "item_name": item_name,
            "category": category,
            "brought_by_patient": brought_by_patient,
            "shift": shift,
        },
    )


def _patients_report_rows(db, start_date, end_date, search_name=None, insurance_type=None):
    """Rows of the patients report (page and CSV) — PERF-01 / ACC-1.10.

    Previously 8 queries per patient (~20 s for 8 months of production data); now 8 queries
    in total. Order and values are identical to the old loop (verified by the golden master):
    patients appear in order of their first invoice (work_date, id), then a stable sort by
    invoice count; totals keep the old int/float types (COALESCE(SUM(...), 0)).
    """
    rng = (start_date, end_date)
    # Order of first appearance by (work_date, id) — exactly what the old
    # `SELECT DISTINCT patient_id` returned through the work_date index, now explicit so a
    # future index or query-plan change cannot reorder patients with equal counts.
    patient_ids = [
        r["patient_id"]
        for r in db.execute(
            """SELECT patient_id FROM (
               SELECT patient_id, work_date, id,
                      ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY work_date, id) AS rn
               FROM invoices WHERE work_date BETWEEN ? AND ? AND status != 'voided')
           WHERE rn = 1 ORDER BY work_date, id""",
            rng,
        )
    ]
    in_range = "SELECT DISTINCT patient_id FROM invoices WHERE work_date BETWEEN ? AND ? AND status != 'voided'"

    patients = {
        r["id"]: r
        for r in db.execute(
            f"SELECT id, name, family_name, national_id, phone_number FROM patients WHERE id IN ({in_range})", rng
        )
    }
    inv = {
        r["patient_id"]: r
        for r in db.execute(
            """SELECT patient_id, COUNT(*) AS cnt, MAX(opened_at) AS last_date FROM invoices
           WHERE work_date BETWEEN ? AND ? AND status != 'voided' GROUP BY patient_id""",
            rng,
        )
    }

    def closed_sum(table, col):
        return {
            r["patient_id"]: r["total"]
            for r in db.execute(
                f"""SELECT i.patient_id, COALESCE(SUM(x.{col}), 0) AS total FROM {table} x
                JOIN invoices i ON i.id = x.invoice_id
                WHERE i.work_date BETWEEN ? AND ? AND i.status = 'closed' GROUP BY i.patient_id""",
                rng,
            )
        }

    v_paid = closed_sum("visits", "price")
    inj_paid = closed_sum("injections", "total_price")
    pr_paid = closed_sum("procedures", "price")
    visits_count = {
        r["patient_id"]: r["cnt"]
        for r in db.execute(
            f"""SELECT i.patient_id, COUNT(*) AS cnt FROM visits v JOIN invoices i ON i.id = v.invoice_id
            WHERE v.work_date BETWEEN ? AND ? AND i.patient_id IN ({in_range}) GROUP BY i.patient_id""",
            rng + rng,
        )
    }
    last_ins = {
        r["patient_id"]: r["insurance_type"]
        for r in db.execute(
            f"""SELECT patient_id, insurance_type FROM (
                SELECT i.patient_id, v.insurance_type,
                       ROW_NUMBER() OVER (PARTITION BY i.patient_id ORDER BY v.visit_date DESC, v.id DESC) AS rn
                FROM visits v JOIN invoices i ON i.id = v.invoice_id
                WHERE i.patient_id IN ({in_range}))
            WHERE rn = 1""",
            rng,
        )
    }

    rows = []
    for pid in patient_ids:
        patient = patients.get(pid)
        if not patient:
            continue
        full_name = f"{patient['name']} {patient['family_name']}"
        if search_name and search_name.lower() not in full_name.lower():
            continue
        ins_type = last_ins.get(pid)
        if insurance_type and ins_type != insurance_type:
            continue
        rows.append(
            {
                "id": pid,
                "full_name": full_name,
                "national_id": patient["national_id"] or "",
                "phone": patient["phone_number"] or "",
                "insurance_type": ins_type,
                "invoices_count": inv[pid]["cnt"],
                "visits_count": visits_count.get(pid, 0),
                "total_paid": v_paid.get(pid, 0) + inj_paid.get(pid, 0) + pr_paid.get(pid, 0),
                "last_visit": inv[pid]["last_date"],
            }
        )
    rows.sort(key=lambda x: x["invoices_count"], reverse=True)
    return rows


@bp.route("/reports/patients")
@login_required
@manager_required
def patients_report():
    """گزارش مدیریتی بیماران با آمار مراجعات و پرداخت‌ها."""

    log_report_access(ActionType.REPORT_VIEW, "patients")
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

    # گرفتن بیماران که در بازه زمانی فاکتور داشتند
    results = _patients_report_rows(db, start_date, end_date, search_name, insurance_type)

    total_patients = len(results)
    total_revenue = sum(r["total_paid"] for r in results)

    # لیست بیمه‌ها
    insurances = db.execute(
        "SELECT DISTINCT insurance_type FROM visits WHERE insurance_type IS NOT NULL ORDER BY insurance_type"
    ).fetchall()

    # رنج‌های شمسی
    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    def jalali_range(days):
        g_end = g_today
        g_start = add_days_gregorian(g_end, -(days - 1))
        j_end = Gregorian(g_end).persian_tuple()
        j_start = Gregorian(g_start).persian_tuple()
        return {
            "from": {"y": j_start[0], "m": j_start[1], "d": j_start[2]},
            "to": {"y": j_end[0], "m": j_end[1], "d": j_end[2]},
        }

    ranges = {
        "today": {"y": j_today[0], "m": j_today[1], "d": j_today[2]},
        "r7": jalali_range(7),
        "r30": jalali_range(30),
        "r90": jalali_range(90),
    }

    return render_template(
        "manager/reports_patients.html",
        patients=results,
        total_patients=total_patients,
        total_revenue=total_revenue,
        insurances=insurances,
        jalali_ranges=ranges,
        active_filters={
            "from": date_from,
            "to": date_to,
            "search_name": search_name,
            "insurance_type": insurance_type,
            "gender": gender,
        },
    )


@bp.route("/reports/staff")
@login_required
@manager_required
def staff_performance_report():
    """Per-person performance over a date range (FB-26): doctors, nurses, reception."""
    from src.services import staff_performance as perf

    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=29)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    d1, d2 = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    log_report_access(ActionType.REPORT_VIEW, "staff")
    db = get_db()
    j1 = Gregorian(start_dt.date()).persian_tuple()
    j2 = Gregorian(end_dt.date()).persian_tuple()
    return render_template(
        "manager/reports_staff.html",
        doctors=perf.doctors(db, d1, d2),
        nurses=perf.nurses(db, d1, d2),
        receptionists=perf.receptionists(db, d1, d2),
        date_from=f"{j1[0]}/{j1[1]:02d}/{j1[2]:02d}",
        date_to=f"{j2[0]}/{j2[1]:02d}/{j2[2]:02d}",
    )
