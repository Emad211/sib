import logging
from datetime import timedelta

from flask import (
    g,
    render_template,
    request,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.jalali import Gregorian
from src.common.utils import iran_now

from . import bp
from ._common import manager_api_required, manager_required
from .exports import make_csv_response

logger = logging.getLogger(__name__)


def _nursing_exclusion_pairs(db):
    """{(insurance_type, nursing_service_id)} — the whole table is a few rows."""
    return {
        (r[0], r[1]) for r in db.execute("SELECT insurance_type, nursing_service_id FROM insurance_nursing_exclusions")
    }


@bp.route("/insurance_arrears")
@login_required
@manager_required(flash_message="دسترسی محدود")
def insurance_arrears():
    """صفحه معوقات بیمه - نمایش مطالبات از بیمه‌ها"""

    db = get_db()

    # Jalali ranges for date picker
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

    # دریافت فیلترها - تاریخ شمسی
    date_from_jalali = request.args.get("from", "")
    date_to_jalali = request.args.get("to", "")
    insurance_filter = request.args.get("insurance", "")

    # تبدیل تاریخ شمسی به میلادی
    date_from_gregorian = ""
    date_to_gregorian = ""

    if date_from_jalali:
        try:
            parts = date_from_jalali.split("/")
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian

            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_from_gregorian = gd.strftime("%Y-%m-%d")
        except Exception:
            logger.warning("insurance_arrears: ignored error", exc_info=True)

    if date_to_jalali:
        try:
            parts = date_to_jalali.split("/")
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian

            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_to_gregorian = gd.strftime("%Y-%m-%d")
        except Exception:
            logger.warning("insurance_arrears: ignored error", exc_info=True)

    # دریافت تعرفه پایه (آزاد)
    base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE is_base_tariff = 1").fetchone()
    if not base_tariff:
        # اگر تعرفه پایه تعریف نشده، از آزاد استفاده کن
        base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE insurance_type = 'آزاد'").fetchone()
    base_visit_price = base_tariff["tariff_price"] if base_tariff else 0

    # دریافت تمام تعرفه‌های بیمه و بررسی پوشش پرستاری
    insurance_tariffs = {}
    tariffs_rows = db.execute(
        "SELECT * FROM visit_tariffs WHERE insurance_type != 'آزاد' AND (is_base_tariff = 0 OR is_base_tariff IS NULL)"
    ).fetchall()
    for t in tariffs_rows:
        # بررسی ستون nursing_covers
        nursing_covers = False
        try:
            nursing_covers = bool(t["nursing_covers"])
        except Exception:
            logger.warning("insurance_arrears: ignored error", exc_info=True)
        insurance_tariffs[t["insurance_type"]] = {
            "visit_tariff": t["tariff_price"],
            "nursing_covers": nursing_covers,  # آیا بیمه پرستاری را پوشش می‌دهد؟
        }

    # دریافت تعرفه‌های بیمه تکمیلی
    supplementary_tariffs = {}
    supp_rows = db.execute("SELECT * FROM visit_tariffs WHERE is_supplementary = 1 AND is_active = 1").fetchall()
    for t in supp_rows:
        row = dict(t)
        supplementary_tariffs[row["insurance_type"]] = {
            "visit_tariff": row.get("tariff_price"),
            "nursing_covers": bool(row.get("nursing_covers", 0)),
        }

    # ساخت کوئری پایه برای ویزیت‌ها - شامل بیمه تکمیلی
    visit_query = """
        SELECT v.id, v.visit_date, v.insurance_type, v.supplementary_insurance, v.price as visit_price,
               p.full_name as patient_name, p.national_id,
               inv.id as invoice_id, inv.status as invoice_status
        FROM visits v
        JOIN patients p ON v.patient_id = p.id
        JOIN invoices inv ON v.invoice_id = inv.id
        WHERE v.insurance_type IS NOT NULL AND v.insurance_type != 'آزاد'
    """
    params = []

    if date_from_gregorian:
        visit_query += " AND v.work_date >= ?"
        params.append(date_from_gregorian)
    if date_to_gregorian:
        visit_query += " AND v.work_date <= ?"
        params.append(date_to_gregorian)
    if insurance_filter:
        visit_query += " AND v.insurance_type = ?"
        params.append(insurance_filter)

    visit_query += " ORDER BY v.visit_date DESC"
    visits = db.execute(visit_query, params).fetchall()

    # ساخت کوئری برای تزریقات (خدمات پرستاری) - شامل بیمه تکمیلی
    injection_query = """
        SELECT i.id, i.injection_date, i.injection_type, i.total_price,
               i.service_id,
               p.full_name as patient_name, p.national_id,
               inv.id as invoice_id, inv.status as invoice_status, inv.insurance_type,
               inv.supplementary_insurance
        FROM injections i
        JOIN patients p ON i.patient_id = p.id
        JOIN invoices inv ON i.invoice_id = inv.id
        WHERE inv.insurance_type IS NOT NULL AND inv.insurance_type != 'آزاد'
    """
    inj_params = []

    if date_from_gregorian:
        injection_query += " AND i.work_date >= ?"
        inj_params.append(date_from_gregorian)
    if date_to_gregorian:
        injection_query += " AND i.work_date <= ?"
        inj_params.append(date_to_gregorian)
    if insurance_filter:
        injection_query += " AND inv.insurance_type = ?"
        inj_params.append(insurance_filter)

    injection_query += " ORDER BY i.injection_date DESC"
    injections = db.execute(injection_query, inj_params).fetchall()

    # محاسبه معوقات برای هر ویزیت
    # معوقه ویزیت بیمه پایه = تعرفه پایه - سهم بیمار (تعرفه بیمه)
    # معوقه ویزیت بیمه تکمیلی = سهم بیمار از بیمه پایه (که تکمیلی باید بپردازد)
    visit_arrears = []
    supplementary_arrears = []  # لیست جداگانه برای معوقات بیمه تکمیلی

    for v in visits:
        ins_type = v["insurance_type"]
        supp_ins = v["supplementary_insurance"]

        # سهم بیمار = تعرفه ویزیت برای آن بیمه
        ins_tariff = insurance_tariffs.get(ins_type, {})
        patient_share = ins_tariff.get("visit_tariff", 0) or 0  # تعرفه بیمه = سهم بیمار
        base_insurance_debt = base_visit_price - patient_share  # معوقه از بیمه پایه

        # معوقه بیمه پایه
        if base_insurance_debt > 0:
            visit_arrears.append(
                {
                    "id": v["id"],
                    "date": v["visit_date"],
                    "type": "visit",
                    "type_fa": "ویزیت",
                    "insurance_type": ins_type,
                    "patient_name": v["patient_name"],
                    "national_id": v["national_id"],
                    "invoice_id": v["invoice_id"],
                    "base_price": base_visit_price,
                    "patient_paid": patient_share,
                    "insurance_debt": base_insurance_debt,
                }
            )

        # معوقه بیمه تکمیلی: اگر بیمه تکمیلی دارد، سهم بیمار از بیمه پایه را تکمیلی باید بپردازد
        if supp_ins and supp_ins in supplementary_tariffs and patient_share > 0:
            # تعرفه بیمه تکمیلی (سهم بیمار با تکمیلی) - معمولاً 0 یا خیلی کم
            supp_tariff = supplementary_tariffs.get(supp_ins, {})
            final_patient_share = supp_tariff.get("visit_tariff", 0) or 0
            supp_debt = patient_share - final_patient_share  # مقداری که تکمیلی باید بپردازد

            if supp_debt > 0:
                supplementary_arrears.append(
                    {
                        "id": v["id"],
                        "date": v["visit_date"],
                        "type": "visit",
                        "type_fa": "ویزیت",
                        "insurance_type": supp_ins,  # نام بیمه تکمیلی
                        "base_insurance": ins_type,  # نام بیمه پایه
                        "patient_name": v["patient_name"],
                        "national_id": v["national_id"],
                        "invoice_id": v["invoice_id"],
                        "base_price": patient_share,  # سهم بیمار از بیمه پایه
                        "patient_paid": final_patient_share,  # سهم نهایی بیمار با تکمیلی
                        "insurance_debt": supp_debt,  # معوقه از بیمه تکمیلی
                    }
                )

    # محاسبه معوقات برای خدمات پرستاری
    # فقط بیمه‌هایی که پرستاری را پوشش می‌دهند (nursing_covers = true)
    nursing_arrears = []

    excluded_pairs = _nursing_exclusion_pairs(db)  # once per request, not once per row (PERF-01)
    for i in injections:
        ins_type = i["insurance_type"]
        ins_tariff = insurance_tariffs.get(ins_type, {})
        # Check if this specific nursing service is excluded for this insurance (insurance_nursing_exclusions)
        excluded = False
        svc_id = None
        try:
            try:
                keys = list(i.keys())
            except Exception:
                keys = []
            if "service_id" in keys and i["service_id"] is not None:
                svc_id = int(i["service_id"])
            else:
                svc_id = None
        except Exception:
            svc_id = None
        if svc_id:
            if (ins_type, svc_id) in excluded_pairs:
                excluded = True

        include = bool(ins_tariff.get("nursing_covers", False) and not excluded)

        # (diagnostic logging removed)

        if include:
            service_price = i["total_price"] or 0
            nursing_arrears.append(
                {
                    "id": i["id"],
                    "date": i["injection_date"],
                    "type": "injection",
                    "type_fa": i["injection_type"],
                    "insurance_type": ins_type,
                    "patient_name": i["patient_name"],
                    "national_id": i["national_id"],
                    "invoice_id": i["invoice_id"],
                    "service_price": service_price,  # مبلغ خدمت که بیمه باید پرداخت کند
                }
            )

    # خلاصه معوقات به تفکیک بیمه (شامل بیمه‌های پایه و تکمیلی)
    summary_by_insurance = {}

    # معوقات بیمه پایه
    for arr in visit_arrears:
        ins = arr["insurance_type"]
        if ins not in summary_by_insurance:
            summary_by_insurance[ins] = {
                "visit_count": 0,
                "visit_debt": 0,
                "nursing_count": 0,
                "nursing_debt": 0,
                "supplementary_count": 0,
                "supplementary_debt": 0,
                "total_debt": 0,
                "is_supplementary": False,
            }
        summary_by_insurance[ins]["visit_count"] += 1
        summary_by_insurance[ins]["visit_debt"] += arr["insurance_debt"]
        summary_by_insurance[ins]["total_debt"] += arr["insurance_debt"]

    # معوقات بیمه تکمیلی
    for arr in supplementary_arrears:
        ins = arr["insurance_type"]
        if ins not in summary_by_insurance:
            summary_by_insurance[ins] = {
                "visit_count": 0,
                "visit_debt": 0,
                "nursing_count": 0,
                "nursing_debt": 0,
                "supplementary_count": 0,
                "supplementary_debt": 0,
                "total_debt": 0,
                "is_supplementary": True,
            }
        summary_by_insurance[ins]["supplementary_count"] += 1
        summary_by_insurance[ins]["supplementary_debt"] += arr["insurance_debt"]
        summary_by_insurance[ins]["total_debt"] += arr["insurance_debt"]
        summary_by_insurance[ins]["is_supplementary"] = True

    for arr in nursing_arrears:
        ins = arr["insurance_type"]
        if ins not in summary_by_insurance:
            summary_by_insurance[ins] = {
                "visit_count": 0,
                "visit_debt": 0,
                "nursing_count": 0,
                "nursing_debt": 0,
                "supplementary_count": 0,
                "supplementary_debt": 0,
                "total_debt": 0,
                "is_supplementary": False,
            }
        summary_by_insurance[ins]["nursing_count"] += 1
        summary_by_insurance[ins]["nursing_debt"] += arr["service_price"]
        summary_by_insurance[ins]["total_debt"] += arr["service_price"]

    # لیست بیمه‌ها برای فیلتر (شامل پایه و تکمیلی)
    all_insurances = db.execute(
        "SELECT DISTINCT insurance_type FROM visit_tariffs WHERE insurance_type != 'آزاد' AND (is_base_tariff = 0 OR is_base_tariff IS NULL) ORDER BY insurance_type"
    ).fetchall()

    # جمع کل معوقات
    total_visit_debt = sum(a["insurance_debt"] for a in visit_arrears)
    total_supplementary_debt = sum(a["insurance_debt"] for a in supplementary_arrears)
    total_nursing_debt = sum(a["service_price"] for a in nursing_arrears)
    total_debt = total_visit_debt + total_supplementary_debt + total_nursing_debt
    # Diagnostic: return raw data when requested by manager for debugging
    try:
        if (
            request.args.get("_diag")
            and g.user
            and (
                g.user["role"]
                if isinstance(g.user, dict) or hasattr(g.user, "__getitem__")
                else getattr(g.user, "role", None)
            )
            == "manager"
        ):
            from flask import jsonify

            return jsonify(
                {
                    "visit_arrears": visit_arrears,
                    "supplementary_arrears": supplementary_arrears,
                    "nursing_arrears": nursing_arrears,
                    "summary_by_insurance": summary_by_insurance,
                    "total_visit_debt": total_visit_debt,
                    "total_supplementary_debt": total_supplementary_debt,
                    "total_nursing_debt": total_nursing_debt,
                    "total_debt": total_debt,
                }
            )
    except Exception:
        logger.warning("insurance_arrears: ignored error", exc_info=True)

    return render_template(
        "manager/insurance_arrears.html",
        visit_arrears=visit_arrears,
        supplementary_arrears=supplementary_arrears,
        nursing_arrears=nursing_arrears,
        summary_by_insurance=summary_by_insurance,
        all_insurances=[i["insurance_type"] for i in all_insurances],
        base_visit_price=base_visit_price,
        total_visit_debt=total_visit_debt,
        total_supplementary_debt=total_supplementary_debt,
        total_nursing_debt=total_nursing_debt,
        total_debt=total_debt,
        active_filters={"from": date_from_jalali, "to": date_to_jalali},
        insurance_filter=insurance_filter,
        jalali_ranges=ranges,
    )


@bp.route("/insurance_arrears/export")
@login_required
@manager_api_required(body={"error": "دسترسی محدود"})
def export_insurance_arrears():
    """خروجی CSV معوقات بیمه"""

    db = get_db()

    # دریافت فیلترها - تاریخ شمسی
    date_from_jalali = request.args.get("from", "")
    date_to_jalali = request.args.get("to", "")
    insurance_filter = request.args.get("insurance", "")

    # تبدیل تاریخ شمسی به میلادی
    date_from_gregorian = ""
    date_to_gregorian = ""

    if date_from_jalali:
        try:
            parts = date_from_jalali.split("/")
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian

            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_from_gregorian = gd.strftime("%Y-%m-%d")
        except Exception:
            logger.warning("export_insurance_arrears: ignored error", exc_info=True)

    if date_to_jalali:
        try:
            parts = date_to_jalali.split("/")
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian

            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_to_gregorian = gd.strftime("%Y-%m-%d")
        except Exception:
            logger.warning("export_insurance_arrears: ignored error", exc_info=True)

    base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE is_base_tariff = 1").fetchone()
    if not base_tariff:
        base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE insurance_type = 'آزاد'").fetchone()
    base_visit_price = base_tariff["tariff_price"] if base_tariff else 0

    # دریافت تعرفه‌های بیمه با پوشش پرستاری
    insurance_tariffs = {}
    tariffs_rows = db.execute(
        "SELECT * FROM visit_tariffs WHERE insurance_type != 'آزاد' AND (is_base_tariff = 0 OR is_base_tariff IS NULL)"
    ).fetchall()
    for t in tariffs_rows:
        nursing_covers = False
        try:
            nursing_covers = bool(t["nursing_covers"])
        except Exception:
            logger.warning("export_insurance_arrears: ignored error", exc_info=True)
        insurance_tariffs[t["insurance_type"]] = {"nursing_covers": nursing_covers}

    # ویزیت‌ها
    visit_query = """
        SELECT v.visit_date, v.insurance_type, v.price as visit_price,
               p.full_name as patient_name, p.national_id
        FROM visits v
        JOIN patients p ON v.patient_id = p.id
        JOIN invoices inv ON v.invoice_id = inv.id
        WHERE v.insurance_type IS NOT NULL AND v.insurance_type != 'آزاد'
    """
    params = []
    if date_from_gregorian:
        visit_query += " AND v.work_date >= ?"
        params.append(date_from_gregorian)
    if date_to_gregorian:
        visit_query += " AND v.work_date <= ?"
        params.append(date_to_gregorian)
    if insurance_filter:
        visit_query += " AND v.insurance_type = ?"
        params.append(insurance_filter)

    visits = db.execute(visit_query, params).fetchall()

    # تزریقات
    injection_query = """
        SELECT i.injection_date, i.injection_type, i.total_price, i.service_id,
               p.full_name as patient_name, p.national_id, inv.insurance_type
        FROM injections i
        JOIN patients p ON i.patient_id = p.id
        JOIN invoices inv ON i.invoice_id = inv.id
        WHERE inv.insurance_type IS NOT NULL AND inv.insurance_type != 'آزاد'
    """
    inj_params = []
    if date_from_gregorian:
        injection_query += " AND i.work_date >= ?"
        inj_params.append(date_from_gregorian)
    if date_to_gregorian:
        injection_query += " AND i.work_date <= ?"
        inj_params.append(date_to_gregorian)
    if insurance_filter:
        injection_query += " AND inv.insurance_type = ?"
        inj_params.append(insurance_filter)

    injections = db.execute(injection_query, inj_params).fetchall()

    data = []
    for v in visits:
        ins_type = v["insurance_type"]
        ins_tariff = insurance_tariffs.get(ins_type, {})
        patient_paid = ins_tariff.get("visit_tariff", 0) or 0  # تعرفه بیمه = سهم بیمار
        insurance_debt = base_visit_price - patient_paid
        if insurance_debt > 0:
            data.append(
                [
                    v["visit_date"],
                    v["patient_name"],
                    v["national_id"] or "",
                    v["insurance_type"],
                    "ویزیت",
                    base_visit_price,
                    patient_paid,
                    insurance_debt,
                ]
            )

    excluded_pairs = _nursing_exclusion_pairs(db)  # once per request, not once per row (PERF-01)
    for i in injections:
        ins_type = i["insurance_type"]
        ins_tariff = insurance_tariffs.get(ins_type, {})
        # Skip if insurance does not cover nursing
        if not ins_tariff.get("nursing_covers", False):
            continue
        # If this injection's service is excluded for this insurance, skip it
        svc_id = i["service_id"]  # BUG-01: sqlite3.Row has no .get()
        excluded = False
        try:
            if svc_id is not None and (ins_type, svc_id) in excluded_pairs:
                excluded = True
        except Exception:
            excluded = False
        if excluded:
            continue
        service_price = i["total_price"] or 0
        data.append(
            [
                i["injection_date"],
                i["patient_name"],
                i["national_id"] or "",
                i["insurance_type"],
                i["injection_type"],
                None,
                0,
                service_price,
            ]
        )

    headers = ["تاریخ", "نام بیمار", "کد ملی", "نوع بیمه", "نوع خدمت", "تعرفه پایه", "پرداخت بیمار", "معوقه بیمه"]
    return make_csv_response(data, headers, "insurance_arrears.csv")
