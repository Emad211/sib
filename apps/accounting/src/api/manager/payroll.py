import logging

from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.utils import iran_now

from . import bp
from ._common import manager_api_required, manager_required

logger = logging.getLogger(__name__)


def compute_payroll(db, work_date_from, work_date_to, staff_id=None, staff_type=None, shift_filter=None):
    """Salary of every active doctor/nurse for a work-date range (same rules as the payroll page).

    Used by /payroll/calculate and by the dashboard's "salaries since the start of the month" tile.
    """
    results = []

    # تعیین لیست پرسنل
    if staff_id and staff_id != "all":
        staff_list = db.execute(
            """
            SELECT m.*, p.base_morning, p.base_evening, p.base_night,
                   p.visit_fee, p.injection_percent, p.procedure_percent, p.tax_percent,
                   p.nursing_percent, p.nurse_procedure_percent
            FROM medical_staff m
            LEFT JOIN payroll_settings p ON m.id = p.staff_id
            WHERE m.id = ? AND m.is_active = 1
        """,
            (staff_id,),
        ).fetchall()
    else:
        query = """
            SELECT m.*, p.base_morning, p.base_evening, p.base_night,
                   p.visit_fee, p.injection_percent, p.procedure_percent, p.tax_percent,
                   p.nursing_percent, p.nurse_procedure_percent
            FROM medical_staff m
            LEFT JOIN payroll_settings p ON m.id = p.staff_id
            WHERE m.is_active = 1
        """
        params = []
        if staff_type and staff_type != "all":
            query += " AND m.staff_type = ?"  # was pasted into the SQL text (DEBT-05)
            params.append(staff_type)
        staff_list = db.execute(query, params).fetchall()

    for person in staff_list:
        person_id = person["id"]
        person_type = person["staff_type"]
        person_name = person["full_name"]

        # تنظیمات حقوق
        base_morning = person["base_morning"] or 0
        base_evening = person["base_evening"] or 0
        base_night = person["base_night"] or 0

        details = []
        total_salary = 0

        # ========== محاسبه شیفت‌ها از کار واقعی (تزریقات/ویزیت/کار عملی) ==========
        # توجه: جدول shift_staff فقط یک پزشک/پرستار در هر شیفت ذخیره می‌کند
        # اما ممکن است چند نفر در یک شیفت کار کنند، پس از کار واقعی می‌خوانیم
        # در نسخه جدید، تاریخ کاری از ساعت/شیفت مشتق نمی‌شود؛ از work_date استفاده می‌کنیم.

        # The date and shift filters sit inside the inner query as bound parameters (they used to be
        # pasted into the SQL text and applied after a scan of the whole visit history; same rows).
        inner_filter, filter_params = "", []
        if work_date_from and work_date_to:
            inner_filter += " AND v.work_date BETWEEN ? AND ?"
            filter_params += [work_date_from, work_date_to]
        if shift_filter and shift_filter != "all":
            inner_filter += " AND v.shift = ?"
            filter_params.append(shift_filter)

        if person_type == "doctor":
            # شیفت‌های پزشک - فقط از ویزیت‌ها (شیفت اصلی حضور پزشک)
            # تزریقات و کارهای عملی تحت نظر پزشک در محاسبه شیفت لحاظ نمی‌شوند
            # چون ممکن است توسط پذیرش دیگر در شیفت دیگر ثبت شوند
            shift_query = f"""
                SELECT shift, COUNT(*) as cnt FROM (
                    SELECT DISTINCT v.work_date as work_date, v.shift
                    FROM visits v WHERE v.doctor_id = ?{inner_filter}
                ) AS actual_shifts
                GROUP BY shift
            """
            shift_params = [person_id, *filter_params]
        else:
            # شیفت‌های پرستار - بر اساس شیفت ویزیت در فاکتور
            # منطق: پرستار حقوق پایه را فقط برای شیفتی دریافت می‌کند که ویزیت در آن شیفت ثبت شده
            # نه شیفتی که تزریق یا کار عملی ثبت شده (چون ممکن است پذیرش دیگری در شیفت دیگر ثبت کرده باشد)
            shift_query = f"""
                SELECT shift, COUNT(*) as cnt FROM (
                    SELECT DISTINCT v.work_date as work_date, v.shift
                    FROM visits v
                    JOIN invoices inv ON inv.id = v.invoice_id
                    WHERE (EXISTS (
                        SELECT 1 FROM injections i WHERE i.invoice_id = inv.id AND i.nurse_id = ?
                    ) OR EXISTS (
                        SELECT 1 FROM procedures p WHERE p.invoice_id = inv.id AND p.nurse_id = ?
                    )){inner_filter}
                ) AS actual_shifts
                GROUP BY shift
            """
            shift_params = [person_id, person_id, *filter_params]

        shifts = db.execute(shift_query, shift_params).fetchall()

        morning_count = sum(s["cnt"] for s in shifts if s["shift"] == "morning")
        evening_count = sum(s["cnt"] for s in shifts if s["shift"] == "evening")
        night_count = sum(s["cnt"] for s in shifts if s["shift"] == "night")

        shift_total = (morning_count * base_morning) + (evening_count * base_evening) + (night_count * base_night)

        if morning_count > 0:
            details.append(
                {
                    "type": "شیفت صبح",
                    "count": morning_count,
                    "unit_price": base_morning,
                    "total": morning_count * base_morning,
                }
            )
        if evening_count > 0:
            details.append(
                {
                    "type": "شیفت عصر",
                    "count": evening_count,
                    "unit_price": base_evening,
                    "total": evening_count * base_evening,
                }
            )
        if night_count > 0:
            details.append(
                {"type": "شیفت شب", "count": night_count, "unit_price": base_night, "total": night_count * base_night}
            )

        total_salary += shift_total

        if person_type == "doctor":
            # ========== ویزیت‌ها ==========
            visit_fee = person["visit_fee"] or 20000
            # فقط ویزیت‌های فاکتورهای بسته‌شده را بشمار
            visit_query = """SELECT COUNT(*) as cnt FROM visits v
                             JOIN invoices inv ON v.invoice_id = inv.id
                             WHERE inv.status = 'closed' AND v.doctor_id = ?"""
            visit_params = [person_id]

            if work_date_from and work_date_to:
                visit_query += " AND v.work_date BETWEEN ? AND ?"
                visit_params.extend([work_date_from, work_date_to])

            if shift_filter and shift_filter != "all":
                visit_query += " AND v.shift = ?"
                visit_params.append(shift_filter)

            visit_count = db.execute(visit_query, visit_params).fetchone()["cnt"]
            visit_total = visit_count * visit_fee

            if visit_count > 0:
                details.append({"type": "ویزیت", "count": visit_count, "unit_price": visit_fee, "total": visit_total})
            total_salary += visit_total

            # ========== تزریقات پزشک ==========
            # فقط تزریقاتی که در همان فاکتور ویزیت پزشک هم وجود دارد
            injection_percent = person["injection_percent"] or 30
            inj_query = """SELECT SUM(i.total_price) as total FROM injections i
                           JOIN invoices inv ON i.invoice_id = inv.id
                           WHERE inv.status = 'closed' AND i.doctor_id = ?
                           AND EXISTS (
                               SELECT 1 FROM visits v 
                               WHERE v.invoice_id = i.invoice_id 
                               AND v.doctor_id = i.doctor_id
                           )"""
            inj_params = [person_id]

            if work_date_from and work_date_to:
                inj_query += " AND i.work_date BETWEEN ? AND ?"
                inj_params.extend([work_date_from, work_date_to])

            if shift_filter and shift_filter != "all":
                inj_query += " AND i.shift = ?"
                inj_params.append(shift_filter)

            inj_result = db.execute(inj_query, inj_params).fetchone()
            inj_total = (inj_result["total"] or 0) * injection_percent / 100

            if inj_total > 0:
                details.append(
                    {
                        "type": f"سهم تزریقات ({injection_percent}%)",
                        "count": 1,
                        "unit_price": inj_result["total"] or 0,
                        "total": inj_total,
                    }
                )
            total_salary += inj_total

            # ========== کار عملی پزشک ==========
            procedure_percent = person["procedure_percent"] or 40
            proc_query = """SELECT SUM(p.price) as total FROM procedures p
                            JOIN invoices inv ON p.invoice_id = inv.id
                            WHERE inv.status = 'closed' AND p.doctor_id = ?"""
            proc_params = [person_id]

            if work_date_from and work_date_to:
                proc_query += " AND p.work_date BETWEEN ? AND ?"
                proc_params.extend([work_date_from, work_date_to])

            if shift_filter and shift_filter != "all":
                proc_query += " AND p.shift = ?"
                proc_params.append(shift_filter)

            proc_result = db.execute(proc_query, proc_params).fetchone()
            proc_total = (proc_result["total"] or 0) * procedure_percent / 100

            if proc_total > 0:
                details.append(
                    {
                        "type": f"سهم کار عملی ({procedure_percent}%)",
                        "count": 1,
                        "unit_price": proc_result["total"] or 0,
                        "total": proc_total,
                    }
                )
            total_salary += proc_total

            # ========== کسر مالیات ==========
            tax_percent = person["tax_percent"] or 10
            tax_amount = total_salary * tax_percent / 100

            details.append(
                {"type": f"کسر مالیات ({tax_percent}%)", "count": 1, "unit_price": total_salary, "total": -tax_amount}
            )
            total_salary -= tax_amount

        else:  # پرستار
            # ========== خدمات پرستاری ==========
            # Note: فعلاً از جدول injections استفاده می‌کنیم که nurse_id دارد
            nursing_percent = person["nursing_percent"] or 6

            # تزریقات پرستار
            nurse_inj_query = """SELECT SUM(i.total_price) as total FROM injections i
                                 JOIN invoices inv ON i.invoice_id = inv.id
                                 WHERE inv.status = 'closed' AND i.nurse_id = ?"""
            nurse_inj_params = [person_id]

            if work_date_from and work_date_to:
                nurse_inj_query += " AND i.work_date BETWEEN ? AND ?"
                nurse_inj_params.extend([work_date_from, work_date_to])

            if shift_filter and shift_filter != "all":
                nurse_inj_query += " AND i.shift = ?"
                nurse_inj_params.append(shift_filter)

            nurse_inj_result = db.execute(nurse_inj_query, nurse_inj_params).fetchone()
            nursing_total = (nurse_inj_result["total"] or 0) * nursing_percent / 100

            if nursing_total > 0:
                details.append(
                    {
                        "type": f"سهم خدمات پرستاری ({nursing_percent}%)",
                        "count": 1,
                        "unit_price": nurse_inj_result["total"] or 0,
                        "total": nursing_total,
                    }
                )
            total_salary += nursing_total

            # ========== کار عملی پرستار ==========
            nurse_procedure_percent = person["nurse_procedure_percent"] or 35
            nurse_proc_query = """SELECT SUM(p.price) as total FROM procedures p
                                  JOIN invoices inv ON p.invoice_id = inv.id
                                  WHERE inv.status = 'closed' AND p.nurse_id = ?"""
            nurse_proc_params = [person_id]

            if work_date_from and work_date_to:
                nurse_proc_query += " AND p.work_date BETWEEN ? AND ?"
                nurse_proc_params.extend([work_date_from, work_date_to])

            if shift_filter and shift_filter != "all":
                nurse_proc_query += " AND p.shift = ?"
                nurse_proc_params.append(shift_filter)

            nurse_proc_result = db.execute(nurse_proc_query, nurse_proc_params).fetchone()
            nurse_proc_total = (nurse_proc_result["total"] or 0) * nurse_procedure_percent / 100

            if nurse_proc_total > 0:
                details.append(
                    {
                        "type": f"سهم کار عملی پرستار ({nurse_procedure_percent}%)",
                        "count": 1,
                        "unit_price": nurse_proc_result["total"] or 0,
                        "total": nurse_proc_total,
                    }
                )
            total_salary += nurse_proc_total

        results.append(
            {
                "id": person_id,
                "name": person_name,
                "type": person_type,
                "type_label": "پزشک" if person_type == "doctor" else "پرستار",
                "details": details,
                "total_salary": total_salary,
            }
        )

    return results


@bp.route("/payroll", methods=["GET", "POST"])
@login_required
@manager_required(flash_message="دسترسی محدود")
def payroll():
    """صفحه مدیریت حقوق با سه بخش: محاسبه، تنظیمات ذخیره شده، افزودن تنظیمات"""

    db = get_db()

    # دریافت لیست کادر درمان
    staff = db.execute("""
        SELECT m.*, p.id as settings_id,
               p.base_morning, p.base_evening, p.base_night,
               p.visit_fee, p.injection_percent, p.procedure_percent, p.tax_percent,
               p.nursing_percent, p.nurse_procedure_percent
        FROM medical_staff m
        LEFT JOIN payroll_settings p ON m.id = p.staff_id
        WHERE m.is_active = 1
        ORDER BY m.staff_type, m.full_name
    """).fetchall()

    doctors = [s for s in staff if s["staff_type"] == "doctor"]
    nurses = [s for s in staff if s["staff_type"] == "nurse"]

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_settings":
            staff_id = request.form.get("staff_id")
            base_morning = float(request.form.get("base_morning", 0))
            base_evening = float(request.form.get("base_evening", 0))
            base_night = float(request.form.get("base_night", 0))
            visit_fee = float(request.form.get("visit_fee", 0))
            injection_percent = float(request.form.get("injection_percent", 0))
            procedure_percent = float(request.form.get("procedure_percent", 0))
            tax_percent = float(request.form.get("tax_percent", 0))
            nursing_percent = float(request.form.get("nursing_percent", 0))
            nurse_procedure_percent = float(request.form.get("nurse_procedure_percent", 0))

            # چک کردن وجود تنظیمات
            existing = db.execute("SELECT id FROM payroll_settings WHERE staff_id = ?", (staff_id,)).fetchone()

            if existing:
                db.execute(
                    """
                    UPDATE payroll_settings SET
                        base_morning = ?, base_evening = ?, base_night = ?,
                        visit_fee = ?, injection_percent = ?, procedure_percent = ?, tax_percent = ?,
                        nursing_percent = ?, nurse_procedure_percent = ?,
                        updated_at = datetime('now', '+3 hours', '+30 minutes')
                    WHERE staff_id = ?
                """,
                    (
                        base_morning,
                        base_evening,
                        base_night,
                        visit_fee,
                        injection_percent,
                        procedure_percent,
                        tax_percent,
                        nursing_percent,
                        nurse_procedure_percent,
                        staff_id,
                    ),
                )
            else:
                db.execute(
                    """
                    INSERT INTO payroll_settings 
                    (staff_id, base_morning, base_evening, base_night, visit_fee, injection_percent, 
                     procedure_percent, tax_percent, nursing_percent, nurse_procedure_percent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        staff_id,
                        base_morning,
                        base_evening,
                        base_night,
                        visit_fee,
                        injection_percent,
                        procedure_percent,
                        tax_percent,
                        nursing_percent,
                        nurse_procedure_percent,
                    ),
                )

            db.commit()
            flash("تنظیمات حقوق با موفقیت ذخیره شد.", "success")
            return redirect(url_for("manager.payroll"))

        elif action == "delete_settings":
            staff_id = request.form.get("staff_id")
            db.execute("DELETE FROM payroll_settings WHERE staff_id = ?", (staff_id,))
            db.commit()
            flash("تنظیمات حقوق حذف شد.", "success")
            return redirect(url_for("manager.payroll"))

    # تاریخ امروز شمسی
    from src.common.jalali import Gregorian

    today = iran_now().date()
    today_jalali = Gregorian(today).persian_string()  # مثلاً 1404-03-15

    return render_template(
        "manager/payroll.html", staff=staff, doctors=doctors, nurses=nurses, today_jalali=today_jalali
    )


@bp.route("/payroll/calculate", methods=["POST"])
@login_required
@manager_api_required(body={"error": "دسترسی محدود"})
def calculate_payroll():
    """محاسبه حقوق بر اساس فیلترها

    نکته مهم: محاسبه حقوق از ساعت 7:30 صبح روز شروع تا 7:30 صبح روز بعد از پایان انجام می‌شود.
    این باعث می‌شود شیفت شب آخرین روز هم کامل محاسبه شود.
    """

    db = get_db()

    # دریافت پارامترها
    staff_id = request.form.get("staff_id")
    staff_type = request.form.get("staff_type")  # doctor یا nurse یا all
    shift_filter = request.form.get("shift")  # morning/evening/night/all

    # دریافت تاریخ - حالت 1: تاریخ میلادی (از JavaScript)
    date_from = request.form.get("date_from")
    date_to = request.form.get("date_to")

    # حالت 2: اگر تاریخ‌های شمسی جداگانه ارسال شده، آن‌ها را تبدیل کن
    from_year = request.form.get("from_year")
    from_month = request.form.get("from_month")
    from_day = request.form.get("from_day")
    to_year = request.form.get("to_year")
    to_month = request.form.get("to_month")
    to_day = request.form.get("to_day")

    if from_year and from_month and from_day and not date_from:
        from src.common.jalali import Persian

        try:
            persian_from = Persian(int(from_year), int(from_month), int(from_day))
            gregorian_from = persian_from.gregorian_datetime()
            date_from = gregorian_from.strftime("%Y-%m-%d")
        except Exception as e:
            print(f"[Manager.calculate_payroll] Error parsing from date: {e}")

    if to_year and to_month and to_day and not date_to:
        from src.common.jalali import Persian

        try:
            persian_to = Persian(int(to_year), int(to_month), int(to_day))
            gregorian_to = persian_to.gregorian_datetime()
            date_to = gregorian_to.strftime("%Y-%m-%d")
        except Exception as e:
            print(f"[Manager.calculate_payroll] Error parsing to date: {e}")

    # بازه گزارش بر اساس work_date (روز کاری) است تا شیفت شب بین دو تاریخ نشکند.
    work_date_from = date_from
    work_date_to = date_to

    results = compute_payroll(db, work_date_from, work_date_to, staff_id, staff_type, shift_filter)

    return jsonify({"success": True, "results": results})
