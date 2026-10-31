import logging
from datetime import datetime, timedelta

import jdatetime
from flask import (
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from src.__version__ import __version__
from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.jalali import Gregorian
from src.common.utils import format_jalali_datetime, iran_now
from src.services import manager_finance as finance
from src.services import manager_insights as insights
from src.services.activity_logger import ActionCategory, ActionType, log_activity
from src.services.exports import table_response

from . import bp
from ._common import manager_api_required, manager_required

logger = logging.getLogger(__name__)


@bp.route("/")
@login_required
@manager_required(flash_message="دسترسی محدود: فقط مدیر می‌تواند به این بخش دسترسی داشته باشد.")
def index():
    """Manager dashboard with reports, staff management, settings."""

    db = get_db()

    # Today's date range (Iran time)
    from src.common.utils import get_work_date_for_datetime

    today_date = get_work_date_for_datetime()
    today_dt = datetime.strptime(today_date, "%Y-%m-%d")

    # Today's invoices count
    today_invoices = db.execute(
        """
        SELECT COUNT(*) as count FROM invoices 
        WHERE work_date = ? AND status != 'voided'
    """,
        (today_date,),
    ).fetchone()["count"]

    # Today's revenue (from closed invoices) - EXCLUDING consumables
    # درآمد = ویزیت + خدمات پرستاری + کارهای عملی (بدون مصرفی)
    visits_today = db.execute(
        """
        SELECT COALESCE(SUM(v.price), 0) as total 
        FROM visits v
        JOIN invoices i ON v.invoice_id = i.id
        WHERE i.work_date = ? AND i.status = 'closed'
    """,
        (today_date,),
    ).fetchone()["total"]

    injections_today = db.execute(
        """
        SELECT COALESCE(SUM(inj.total_price), 0) as total 
        FROM injections inj
        JOIN invoices i ON inj.invoice_id = i.id
        WHERE i.work_date = ? AND i.status = 'closed'
    """,
        (today_date,),
    ).fetchone()["total"]

    procedures_today = db.execute(
        """
        SELECT COALESCE(SUM(pr.price), 0) as total 
        FROM procedures pr
        JOIN invoices i ON pr.invoice_id = i.id
        WHERE i.work_date = ? AND i.status = 'closed'
    """,
        (today_date,),
    ).fetchone()["total"]

    today_revenue = visits_today + injections_today + procedures_today

    # Today's unique patients
    today_patients = db.execute(
        """
        SELECT COUNT(DISTINCT patient_id) as count FROM invoices 
        WHERE work_date = ? AND status != 'voided'
    """,
        (today_date,),
    ).fetchone()["count"]

    # Open invoices count
    open_invoices = db.execute("""
        SELECT COUNT(*) as count FROM invoices WHERE status = 'open'
    """).fetchone()["count"]

    # Recent invoices (last 10)
    recent_invoices = db.execute("""
        SELECT i.*, p.full_name as patient_name
        FROM invoices i
        LEFT JOIN patients p ON i.patient_id = p.id
        ORDER BY i.opened_at DESC
        LIMIT 10
    """).fetchall()

    # ========== آمارهای جدید برای کارت‌های پایین داشبورد ==========

    # تاریخ امروز ایران (برای جلوگیری از مشکل DATE('now') که UTC است)
    # با استفاده از تابع کمکی که تاریخ کاری دستی را برمی‌گرداند

    # خدمات امروز
    today_visits = db.execute(
        """
        SELECT COUNT(*) as count FROM visits 
        WHERE work_date = ?
    """,
        (today_date,),
    ).fetchone()["count"]

    today_injections = db.execute(
        """
        SELECT COUNT(*) as count FROM injections 
        WHERE work_date = ?
    """,
        (today_date,),
    ).fetchone()["count"]

    today_procedures = db.execute(
        """
        SELECT COUNT(*) as count FROM procedures 
        WHERE work_date = ?
    """,
        (today_date,),
    ).fetchone()["count"]

    # شمارش خدمات پرستاری (از جدول injections که nurse_id دارد)
    today_nursing = db.execute(
        """
        SELECT COUNT(*) as count FROM injections 
        WHERE work_date = ?
        AND nurse_id IS NOT NULL
    """,
        (today_date,),
    ).fetchone()["count"]

    # کادر درمان
    active_doctors = db.execute("""
        SELECT COUNT(*) as count FROM medical_staff 
        WHERE staff_type = 'doctor' AND is_active = 1
    """).fetchone()["count"]

    active_nurses = db.execute("""
        SELECT COUNT(*) as count FROM medical_staff 
        WHERE staff_type = 'nurse' AND is_active = 1
    """).fetchone()["count"]

    total_users = db.execute("""
        SELECT COUNT(*) as count FROM users WHERE is_active = 1
    """).fetchone()["count"]

    # تشخیص شیفت فعلی (شیفت به صورت دستی تعیین می‌شود)
    from src.common.utils import get_current_shift_name

    shift = get_current_shift_name()
    shift_names = {"morning": "صبح", "evening": "عصر", "night": "شب"}
    current_shift = shift_names.get(shift, shift)

    # آمار کلی
    total_patients = db.execute("""
        SELECT COUNT(*) as count FROM patients
    """).fetchone()["count"]

    # آمار این ماه شمسی (محاسبه دقیق اول ماه شمسی)
    import jdatetime

    j_today = jdatetime.date.fromgregorian(date=iran_now().date())
    j_month_start = jdatetime.date(j_today.year, j_today.month, 1)
    g_month_start = j_month_start.togregorian()
    month_start = datetime(g_month_start.year, g_month_start.month, g_month_start.day)
    month_start_date = month_start.strftime("%Y-%m-%d")

    month_invoices = db.execute(
        """
        SELECT COUNT(*) as count FROM invoices 
        WHERE work_date >= ? AND status != 'voided'
    """,
        (month_start_date,),
    ).fetchone()["count"]

    # Month revenue - EXCLUDING consumables
    # درآمد = ویزیت + خدمات پرستاری + کارهای عملی (بدون مصرفی)
    visits_month = db.execute(
        """
        SELECT COALESCE(SUM(v.price), 0) as total 
        FROM visits v
        JOIN invoices i ON v.invoice_id = i.id
        WHERE i.work_date >= ? AND i.status = 'closed'
    """,
        (month_start_date,),
    ).fetchone()["total"]

    injections_month = db.execute(
        """
        SELECT COALESCE(SUM(inj.total_price), 0) as total 
        FROM injections inj
        JOIN invoices i ON inj.invoice_id = i.id
        WHERE i.work_date >= ? AND i.status = 'closed'
    """,
        (month_start_date,),
    ).fetchone()["total"]

    procedures_month = db.execute(
        """
        SELECT COALESCE(SUM(pr.price), 0) as total 
        FROM procedures pr
        JOIN invoices i ON pr.invoice_id = i.id
        WHERE i.work_date >= ? AND i.status = 'closed'
    """,
        (month_start_date,),
    ).fetchone()["total"]

    month_revenue = visits_month + injections_month + procedures_month

    # BIZ-03: this used to divide by a fixed 30, understating the average early in the month.
    elapsed_days = max(1, jdatetime.date.fromgregorian(date=iran_now().date()).day)
    avg_daily = month_revenue / elapsed_days if month_revenue > 0 else 0

    # Jalali today and preset ranges using server-side converter
    g_today = iran_now().date()
    j_today = Gregorian(g_today).persian_tuple()  # (jy, jm, jd)

    def add_days_gregorian(d, days):
        return d + timedelta(days=days)

    # 7, 30, 90 days ranges (end = today, start = end - (n-1))
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
        "manager/index.html",
        today_invoices=today_invoices,
        today_revenue=today_revenue,
        today_patients=today_patients,
        open_invoices=open_invoices,
        recent_invoices=recent_invoices,
        jalali_ranges=ranges,
        # آمارهای جدید
        today_visits=today_visits,
        today_injections=today_injections,
        today_procedures=today_procedures,
        today_nursing=today_nursing,
        active_doctors=active_doctors,
        active_nurses=active_nurses,
        total_users=total_users,
        current_shift=current_shift,
        total_patients=total_patients,
        month_invoices=month_invoices,
        month_revenue=month_revenue,
        avg_daily=avg_daily,
        server_time=iran_now().isoformat(),
        attention=_safe_insight(insights.attention_items, db, current_app.config.get("BACKUP_FOLDER")),
        month=_safe_insight(finance.month_tiles, db, iran_now().date()) or None,
        shifts=_safe_insight(insights.recent_shifts, db, 3),
        health=insights.system_health(
            db, current_app.config.get("DATABASE_PATH"), current_app.config.get("BACKUP_FOLDER"), __version__
        ),
    )


def _safe_insight(fn, *args):
    """The exception panels must never take the whole dashboard down (bad data -> empty panel + log)."""
    try:
        return fn(*args)
    except Exception:
        current_app.logger.exception("dashboard insight %s failed", fn.__name__)
        return []


@bp.route("/invoice/<int:invoice_id>/history")
@login_required
@manager_required
def invoice_history(invoice_id):
    """Everything that happened to one invoice, in time order (ACC-2.8, A06 §3.3)."""
    db = get_db()
    inv = db.execute(
        """SELECT i.*, COALESCE(p.full_name, '—') AS patient_name,
                  COALESCE(uo.full_name, i.opened_by) AS opened_by_fa, COALESCE(uc.full_name, i.closed_by) AS closed_by_fa
           FROM invoices i LEFT JOIN patients p ON p.id = i.patient_id
           LEFT JOIN users uo ON uo.username = i.opened_by LEFT JOIN users uc ON uc.username = i.closed_by
           WHERE i.id = ?""",
        (invoice_id,),
    ).fetchone()
    if inv is None:
        abort(404)
    items = []
    for label, sql in (
        ("ویزیت", "SELECT doctor_name AS name, price AS amount FROM visits WHERE invoice_id = ?"),
        ("پرستاری", "SELECT injection_type AS name, total_price AS amount FROM injections WHERE invoice_id = ?"),
        ("کار عملی", "SELECT procedure_type AS name, price AS amount FROM procedures WHERE invoice_id = ?"),
        ("مصرفی", "SELECT item_name AS name, total_cost AS amount FROM consumables_ledger WHERE invoice_id = ?"),
    ):
        items += [
            {"kind": label, "name": r["name"], "amount": r["amount"] or 0} for r in db.execute(sql, (invoice_id,))
        ]
    grouped = {}
    for it in items:  # FB-28: identical lines once, ×N, summed
        g_ = grouped.setdefault((it["kind"], it["name"]), {"kind": it["kind"], "name": it["name"], "amount": 0, "count": 0})
        g_["amount"] += it["amount"]
        g_["count"] += 1
    items = list(grouped.values())
    events = [
        dict(r) | {"at_fa": format_jalali_datetime(r["created_at"], include_seconds=True)}
        for r in db.execute(
            """SELECT created_at, username, action_type, description, amount, old_value, new_value, target_name
           FROM activity_logs WHERE invoice_id = ? ORDER BY created_at, id""",
            (invoice_id,),
        )
    ]
    from src.services.activity_logger import ACTION_DESCRIPTIONS

    for e in events:
        e["action_fa"] = ACTION_DESCRIPTIONS.get(e["action_type"], e["action_type"])
    void = db.execute("SELECT * FROM invoice_voids WHERE invoice_id = ?", (invoice_id,)).fetchone()
    return render_template(
        "manager/invoice_history.html",
        inv=inv,
        void=void,
        items=items,
        events=events,
        opened_fa=format_jalali_datetime(inv["opened_at"]),
        closed_fa=format_jalali_datetime(inv["closed_at"]),
        date_fa=insights.jalali_date(inv["work_date"]),
    )


@bp.route("/invoice/<int:invoice_id>/void", methods=["POST"])
@login_required
@manager_required
def invoice_void(invoice_id):
    """Void or refund a closed invoice (FB-25). The invoice is kept (status 'voided') with the reason."""
    db = get_db()
    reason = " ".join(request.form.get("reason", "").split())[:300]
    kind = request.form.get("kind", "void")
    method = request.form.get("refund_method", "")
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if inv is None:
        abort(404)
    problem = None
    if inv["status"] != "closed":
        problem = "فقط فاکتور بسته‌شده را می‌توان باطل یا بازپرداخت کرد."
    elif not reason:
        problem = "دلیل را بنویسید."
    elif kind not in ("void", "refund"):
        problem = "نوع عملیات نامعتبر است."
    elif kind == "refund" and method not in ("cash", "card"):
        problem = "روش بازپرداخت را انتخاب کنید."
    if problem:
        flash(problem, "error")
        return redirect(url_for("manager.invoice_history", invoice_id=invoice_id))
    # The void record, the status change and the activity log are one transaction: either all
    # three are saved or none is (a money event must never exist without its audit trail).
    try:
        db.execute(
            "INSERT INTO invoice_voids (invoice_id, kind, amount, refund_method, reason, voided_by) VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_id, kind, inv["total_amount"] or 0, method if kind == "refund" else None, reason, g.user["username"]),
        )
        db.execute("UPDATE invoices SET status = 'voided' WHERE id = ? AND status = 'closed'", (invoice_id,))
        log_activity(
            ActionType.INVOICE_VOID,
            ActionCategory.INVOICE,
            invoice_id=invoice_id,
            patient_id=inv["patient_id"],
            amount=inv["total_amount"] or 0,
            old_value="closed",
            new_value=f"voided:{kind}",
            description=("بازپرداخت فاکتور: " if kind == "refund" else "ابطال فاکتور: ") + reason,
            atomic=True,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("invoice_void: rolled back")
        flash("ثبت ابطال انجام نشد و هیچ تغییری ذخیره نشد؛ دوباره تلاش کنید.", "error")
        return redirect(url_for("manager.invoice_history", invoice_id=invoice_id))
    flash("فاکتور باطل شد و از گزارش‌ها کم شد.", "success")
    return redirect(url_for("manager.invoice_history", invoice_id=invoice_id))


@bp.route("/invoice/<int:invoice_id>/reopen", methods=["POST"])
@login_required
@manager_required
def invoice_reopen(invoice_id):
    """Reopen a closed invoice of the shift that is running now, so reception can correct it (FB-25).

    Older invoices cannot be reopened: items added now would carry today's shift and date.
    For those the manager voids the invoice and reception enters a correct one.
    """
    from src.domain.shifts import scheduled_shift

    db = get_db()
    reason = " ".join(request.form.get("reason", "").split())[:300]
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if inv is None:
        abort(404)
    shift, work_date = scheduled_shift(iran_now())
    problem = None
    if inv["status"] != "closed":
        problem = "فقط فاکتور بسته‌شده را می‌توان دوباره باز کرد."
    elif not reason:
        problem = "دلیل را بنویسید."
    elif (inv["work_date"], inv["shift"]) != (work_date, shift):
        problem = "فقط فاکتور شیفتِ جاری قابل بازگشایی است؛ برای فاکتور قدیمی‌تر آن را باطل کنید تا پذیرش فاکتور درست را ثبت کند."
    if problem:
        flash(problem, "error")
        return redirect(url_for("manager.invoice_history", invoice_id=invoice_id))
    try:  # status change, payment lines and log: one transaction
        db.execute(
            "UPDATE invoices SET status = 'open', closed_at = NULL, closed_by = NULL, closed_by_name = NULL "
            "WHERE id = ? AND status = 'closed'",
            (invoice_id,),
        )
        db.execute("DELETE FROM invoice_payment_lines WHERE invoice_id = ?", (invoice_id,))  # rewritten when closed again
        log_activity(
            ActionType.INVOICE_REOPEN,
            ActionCategory.INVOICE,
            invoice_id=invoice_id,
            patient_id=inv["patient_id"],
            amount=inv["total_amount"] or 0,
            old_value=f"closed by {inv['closed_by']} at {inv['closed_at']}",
            new_value="open",
            description="بازگشایی برای اصلاح: " + reason,
            atomic=True,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("invoice_reopen: rolled back")
        flash("بازگشایی انجام نشد و هیچ تغییری ذخیره نشد؛ دوباره تلاش کنید.", "error")
        return redirect(url_for("manager.invoice_history", invoice_id=invoice_id))
    flash("فاکتور دوباره باز شد؛ پذیرش شیفت می‌تواند اصلاح و دوباره ببندد.", "success")
    return redirect(url_for("manager.invoice_history", invoice_id=invoice_id))


@bp.route("/attention/<key>")
@login_required
@manager_required
def attention_detail(key):
    """The rows behind one \"needs attention\" count; also CSV/Excel via ?format=."""
    found = insights.attention_detail(get_db(), key)
    if found is None:
        abort(404)
    title, note, headers, rows = found
    if request.args.get("format") in ("csv", "xlsx"):
        return table_response(rows, headers, f"attention_{key}", title)
    return render_template("manager/attention.html", title=title, note=note, headers=headers, rows=rows, key=key)


@bp.route("/api/chart-data")
@login_required
@manager_api_required
def chart_data():
    """API برای داده‌های نمودار با پشتیبانی تاریخ شمسی."""

    db = get_db()

    # دریافت پارامترها
    date_from = request.args.get("from", "")  # فرمت: 1404/09/01
    date_to = request.args.get("to", "")  # فرمت: 1404/09/06
    data_type = request.args.get("type", "revenue")

    # تبدیل تاریخ شمسی به میلادی
    def jalali_to_gregorian(jalali_str):
        try:
            parts = jalali_str.split("/")
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            return datetime(gd.year, gd.month, gd.day)
        except Exception:
            return iran_now()

    # تبدیل میلادی به شمسی برای لیبل‌ها
    def gregorian_to_jalali_label(dt):
        jd = jdatetime.date.fromgregorian(date=dt.date())
        return f"{jd.month:02d}/{jd.day:02d}"

    # پردازش تاریخ‌ها
    try:
        start_date = jalali_to_gregorian(date_from)
        end_date = jalali_to_gregorian(date_to)
    except Exception:
        # پیش‌فرض: 7 روز گذشته
        end_date = iran_now()
        start_date = end_date - timedelta(days=6)

    # اطمینان از ترتیب صحیح
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    # تولید لیست روزها
    labels = []
    values = []
    visits_data = []
    injections_data = []
    procedures_data = []
    current = start_date

    while current <= end_date:
        next_day = current + timedelta(days=1)
        day_key = current.strftime("%Y-%m-%d")

        # لیبل شمسی
        labels.append(gregorian_to_jalali_label(current))

        # کوئری بر اساس نوع داده
        if data_type == "revenue":
            # Revenue = visits + injections + procedures (NOT consumables)
            v_rev = db.execute(
                """
                SELECT COALESCE(SUM(v.price), 0) as val FROM visits v
                JOIN invoices i ON v.invoice_id = i.id
                WHERE i.work_date = ? AND i.status = 'closed'
            """,
                (day_key,),
            ).fetchone()["val"]
            inj_rev = db.execute(
                """
                SELECT COALESCE(SUM(inj.total_price), 0) as val FROM injections inj
                JOIN invoices i ON inj.invoice_id = i.id
                WHERE i.work_date = ? AND i.status = 'closed'
            """,
                (day_key,),
            ).fetchone()["val"]
            pr_rev = db.execute(
                """
                SELECT COALESCE(SUM(pr.price), 0) as val FROM procedures pr
                JOIN invoices i ON pr.invoice_id = i.id
                WHERE i.work_date = ? AND i.status = 'closed'
            """,
                (day_key,),
            ).fetchone()["val"]
            result = v_rev + inj_rev + pr_rev

        elif data_type == "invoices":
            result = db.execute(
                """
                SELECT COUNT(*) as val FROM invoices 
                WHERE work_date = ? AND status != 'voided'
            """,
                (day_key,),
            ).fetchone()["val"]

        elif data_type == "patients":
            result = db.execute(
                """
                SELECT COUNT(DISTINCT patient_id) as val FROM invoices 
                WHERE work_date = ? AND status != 'voided'
            """,
                (day_key,),
            ).fetchone()["val"]

        elif data_type == "visits":
            result = db.execute(
                """
                SELECT COUNT(*) as val FROM visits 
                WHERE work_date = ?
            """,
                (day_key,),
            ).fetchone()["val"]

        elif data_type == "injections":
            result = db.execute(
                """
                SELECT COUNT(*) as val FROM injections 
                WHERE work_date = ?
            """,
                (day_key,),
            ).fetchone()["val"]

        elif data_type == "procedures":
            result = db.execute(
                """
                SELECT COUNT(*) as val FROM procedures 
                WHERE work_date = ?
            """,
                (day_key,),
            ).fetchone()["val"]

        elif data_type == "consumables":
            # Count only consumables provided by the center and not exception items
            result = db.execute(
                """
                SELECT COUNT(*) as val FROM consumables_ledger 
                WHERE work_date = ? AND (COALESCE(patient_provided,0) = 0 AND COALESCE(is_exception,0) = 0)
            """,
                (day_key,),
            ).fetchone()["val"]

        elif data_type == "services":
            # FB-22: by amount (Toman) of closed invoices, not by count: 1 procedure vs 40 nursing
            # services made the old count chart meaningless.
            def _sum(sql):
                return db.execute(sql, (day_key,)).fetchone()["val"] or 0

            visits_count = _sum(
                "SELECT SUM(v.price) AS val FROM visits v JOIN invoices i ON i.id = v.invoice_id "
                "WHERE i.status = 'closed' AND i.work_date = ?"
            )
            injections_count = _sum(
                "SELECT SUM(j.total_price) AS val FROM injections j JOIN invoices i ON i.id = j.invoice_id "
                "WHERE i.status = 'closed' AND i.work_date = ?"
            )
            procedures_count = _sum(
                "SELECT SUM(p.price) AS val FROM procedures p JOIN invoices i ON i.id = p.invoice_id "
                "WHERE i.status = 'closed' AND i.work_date = ?"
            )
            visits_data.append(visits_count)
            injections_data.append(injections_count)
            procedures_data.append(procedures_count)
            result = visits_count + injections_count + procedures_count

        elif data_type == "transactions":
            # FB-21: what patients paid (closed invoices, consumables included)
            result = db.execute(
                "SELECT SUM(total_amount) AS val FROM invoices WHERE status = 'closed' AND work_date = ?", (day_key,)
            ).fetchone()["val"]

        elif data_type == "expenses":
            result = db.execute(
                "SELECT SUM(amount) AS val FROM expenses WHERE COALESCE(is_void, 0) = 0 AND spent_on = ?", (day_key,)
            ).fetchone()["val"]

        elif data_type == "salaries":
            from .payroll import compute_payroll

            result = sum(r["total_salary"] for r in compute_payroll(db, day_key, day_key))

        else:
            result = 0

        values.append(result or 0)
        current = next_day

    # اگر نوع services بود، داده‌های تفکیکی برگردان
    if data_type == "services":
        return jsonify(
            {
                "labels": labels,
                "values": values,
                "datasets": {"visits": visits_data, "injections": injections_data, "procedures": procedures_data},
            }
        )

    return jsonify({"labels": labels, "values": values})
