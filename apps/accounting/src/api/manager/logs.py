import logging

import jdatetime
from flask import (
    jsonify,
    render_template,
    request,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.utils import iran_now

from . import bp
from ._common import manager_api_required, manager_required
from .exports import make_csv_response

logger = logging.getLogger(__name__)


@bp.route("/logs")
@login_required
@manager_required
def activity_logs():
    """صفحه نمایش لاگ فعالیت‌های کاربران."""

    from src.services.activity_logger import get_activity_logs, get_logs_count

    # فیلترها
    page = request.args.get("page", 1, type=int)
    per_page = 50
    user_id = request.args.get("user_id", type=int)
    action_type = request.args.get("action_type", "")
    action_category = request.args.get("action_category", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    search_text = request.args.get("patient_name", "")  # جستجو در نام بیمار و توضیحات
    invoice_id = request.args.get("invoice_id", type=int)

    # دریافت لاگ‌ها با پارامترهای صحیح
    logs = get_activity_logs(
        user_id=user_id if user_id else None,
        action_type=action_type if action_type else None,
        action_category=action_category if action_category else None,
        invoice_id=invoice_id if invoice_id else None,
        date_from=date_from if date_from else None,
        date_to=date_to if date_to else None,
        search_text=search_text if search_text else None,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    total = get_logs_count(
        user_id=user_id if user_id else None,
        action_type=action_type if action_type else None,
        action_category=action_category if action_category else None,
        invoice_id=invoice_id if invoice_id else None,
        date_from=date_from if date_from else None,
        date_to=date_to if date_to else None,
        search_text=search_text if search_text else None,
    )
    total_pages = (total + per_page - 1) // per_page

    # لیست کاربران برای فیلتر
    db = get_db()
    users = db.execute("SELECT id, username, full_name FROM users WHERE is_active = 1 ORDER BY username").fetchall()

    # ترجمه نوع عملیات
    action_types = [
        ("login", "ورود"),
        ("logout", "خروج"),
        ("patient_create", "ایجاد بیمار"),
        ("patient_update", "ویرایش بیمار"),
        ("patient_search", "جستجوی بیمار"),
        ("patient_history", "مشاهده سابقه"),
        ("invoice_create", "ایجاد فاکتور"),
        ("invoice_open", "باز کردن فاکتور"),
        ("invoice_close", "بستن فاکتور"),
        ("invoice_view", "مشاهده فاکتور"),
        ("visit_add", "ثبت ویزیت"),
        ("visit_delete", "حذف ویزیت"),
        ("injection_add", "ثبت تزریق"),
        ("injection_delete", "حذف تزریق"),
        ("procedure_add", "ثبت کار عملی"),
        ("procedure_delete", "حذف کار عملی"),
        ("consumable_use", "ثبت مصرفی"),
        ("consumable_delete", "حذف مصرفی"),
        ("item_payment_set", "تغییر وضعیت پرداخت"),
        ("shift_staff_set", "تنظیم کادر درمان"),
        ("print_invoice", "چاپ فاکتور"),
        ("print_receipt", "چاپ رسید"),
        ("print_report", "چاپ گزارش"),
    ]

    # ترجمه دسته‌بندی
    action_categories = [
        ("auth", "احراز هویت"),
        ("patient", "بیمار"),
        ("invoice", "فاکتور"),
        ("visit", "ویزیت"),
        ("injection", "تزریق"),
        ("procedure", "کار عملی"),
        ("consumable", "مصرفی"),
        ("shift", "شیفت"),
        ("print", "چاپ"),
        ("report", "گزارش"),
    ]

    # ساخت دیکشنری فیلترها برای نمایش در تمپلیت
    current_filters = {
        "user_id": user_id,
        "action_type": action_type,
        "action_category": action_category,
        "date_from": date_from,
        "date_to": date_to,
        "patient_name": search_text,
        "invoice_id": invoice_id,
    }

    # تاریخ امروز (تهران) برای دکمه‌های سریع سمت UI
    j_today = jdatetime.date.fromgregorian(date=iran_now().date())

    return render_template(
        "manager/logs.html",
        logs=logs,
        page=page,
        total_pages=total_pages,
        total=total,
        users=users,
        action_types=action_types,
        action_categories=action_categories,
        filters=current_filters,
        j_today=j_today,
    )


@bp.route("/logs/export")
@login_required
@manager_api_required(body={"error": "دسترسی غیرمجاز"})
def export_logs():
    """خروجی CSV از لاگ‌ها."""

    from src.services.activity_logger import get_activity_logs

    # فیلترها
    user_id = request.args.get("user_id", type=int)
    action_type = request.args.get("action_type", "")
    action_category = request.args.get("action_category", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    search_text = request.args.get("patient_name", "")

    # دریافت همه لاگ‌ها (بدون محدودیت)
    logs = get_activity_logs(
        user_id=user_id if user_id else None,
        action_type=action_type if action_type else None,
        action_category=action_category if action_category else None,
        date_from=date_from if date_from else None,
        date_to=date_to if date_to else None,
        search_text=search_text if search_text else None,
        limit=1000000,
    )

    # ساخت CSV با BOM برای پشتیبانی فارسی در Excel
    data = [
        [
            log.get("created_at", ""),
            log.get("username", ""),
            log.get("action_type", ""),
            log.get("action_category", ""),
            log.get("description", ""),
            log.get("patient_name", ""),
            log.get("invoice_id", ""),
            log.get("amount", ""),
        ]
        for log in logs
    ]
    headers = ["تاریخ", "کاربر", "نوع عملیات", "دسته", "توضیحات", "بیمار", "فاکتور", "مبلغ"]
    return make_csv_response(data, headers, "activity_logs", plain_cols=(6,))


@bp.route("/logs/stats")
@login_required
@manager_api_required(body={"error": "دسترسی غیرمجاز"})
def logs_stats():
    """آمار لاگ‌ها."""

    from src.services.activity_logger import get_action_stats

    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    stats = get_action_stats(date_from, date_to)
    return jsonify({"success": True, "stats": stats})
