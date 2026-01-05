"""
Activity Logger Service
Logs all user activities in the reception system
"""

from datetime import datetime

from src.common.utils import get_datetime_range_for_date_range, iran_now
from flask import request, g
from src.adapters.sqlite.core import get_db
from src.common.jalali import Persian


def jalali_to_gregorian(jalali_date: str) -> str:
    """
    تبدیل تاریخ شمسی به میلادی
    ورودی: '1404/09/05' یا '1404-09-05'
    خروجی: '2025-11-26'
    """
    if not jalali_date:
        return None
    try:
        # جایگزینی / با -
        jalali_date = jalali_date.replace('/', '-')
        p = Persian(jalali_date)
        gy, gm, gd = p.gregorian_tuple()
        return f"{gy}-{gm:02d}-{gd:02d}"
    except Exception as e:
        print(f"[ActivityLogger] Error converting date {jalali_date}: {e}")
        return None


# انواع عملیات (action_type)
class ActionType:
    # ورود/خروج
    LOGIN = 'login'
    LOGOUT = 'logout'
    
    # بیمار
    PATIENT_CREATE = 'patient_create'
    PATIENT_UPDATE = 'patient_update'
    PATIENT_SEARCH = 'patient_search'
    PATIENT_VIEW_HISTORY = 'patient_view_history'
    
    # فاکتور
    INVOICE_CREATE = 'invoice_create'
    INVOICE_OPEN = 'invoice_open'
    INVOICE_CLOSE = 'invoice_close'
    INVOICE_VIEW = 'invoice_view'
    
    # آیتم فاکتور
    ITEM_ADD = 'item_add'
    ITEM_DELETE = 'item_delete'
    ITEM_PAYMENT_SET = 'item_payment_set'
    
    # ویزیت
    VISIT_ADD = 'visit_add'
    VISIT_DELETE = 'visit_delete'
    
    # تزریق
    INJECTION_ADD = 'injection_add'
    INJECTION_DELETE = 'injection_delete'
    
    # کار عملی
    PROCEDURE_ADD = 'procedure_add'
    PROCEDURE_DELETE = 'procedure_delete'
    
    # خدمات پرستاری
    NURSING_ADD = 'nursing_add'
    NURSING_DELETE = 'nursing_delete'
    
    # مصرفی
    CONSUMABLE_USE = 'consumable_use'
    CONSUMABLE_DELETE = 'consumable_delete'
    
    # شیفت
    SHIFT_STAFF_SET = 'shift_staff_set'
    
    # چاپ
    PRINT_INVOICE = 'print_invoice'
    PRINT_RECEIPT = 'print_receipt'
    PRINT_REPORT = 'print_report'
    
    # گزارش
    REPORT_VIEW = 'report_view'
    REPORT_EXPORT = 'report_export'


# دسته‌بندی عملیات (action_category)
class ActionCategory:
    AUTH = 'auth'           # ورود/خروج
    PATIENT = 'patient'     # بیمار
    INVOICE = 'invoice'     # فاکتور
    VISIT = 'visit'         # ویزیت
    INJECTION = 'injection' # تزریق
    PROCEDURE = 'procedure' # کار عملی
    NURSING = 'nursing'     # پرستاری
    CONSUMABLE = 'consumable'  # مصرفی
    SHIFT = 'shift'         # شیفت
    PRINT = 'print'         # چاپ
    REPORT = 'report'       # گزارش


# توضیحات فارسی برای هر نوع عملیات
ACTION_DESCRIPTIONS = {
    ActionType.LOGIN: 'ورود به سیستم',
    ActionType.LOGOUT: 'خروج از سیستم',
    
    ActionType.PATIENT_CREATE: 'ثبت بیمار جدید',
    ActionType.PATIENT_UPDATE: 'ویرایش اطلاعات بیمار',
    ActionType.PATIENT_SEARCH: 'جستجوی بیمار',
    ActionType.PATIENT_VIEW_HISTORY: 'مشاهده سوابق بیمار',
    
    ActionType.INVOICE_CREATE: 'ایجاد فاکتور جدید',
    ActionType.INVOICE_OPEN: 'باز کردن فاکتور',
    ActionType.INVOICE_CLOSE: 'بستن فاکتور',
    ActionType.INVOICE_VIEW: 'مشاهده فاکتور',
    
    ActionType.ITEM_ADD: 'افزودن آیتم به فاکتور',
    ActionType.ITEM_DELETE: 'حذف آیتم از فاکتور',
    ActionType.ITEM_PAYMENT_SET: 'تنظیم نحوه پرداخت آیتم',
    
    ActionType.VISIT_ADD: 'ثبت ویزیت',
    ActionType.VISIT_DELETE: 'حذف ویزیت',
    
    ActionType.INJECTION_ADD: 'ثبت تزریق',
    ActionType.INJECTION_DELETE: 'حذف تزریق',
    
    ActionType.PROCEDURE_ADD: 'ثبت کار عملی',
    ActionType.PROCEDURE_DELETE: 'حذف کار عملی',
    
    ActionType.NURSING_ADD: 'ثبت خدمات پرستاری',
    ActionType.NURSING_DELETE: 'حذف خدمات پرستاری',
    
    ActionType.CONSUMABLE_USE: 'مصرف کالا',
    ActionType.CONSUMABLE_DELETE: 'حذف مصرف کالا',
    
    ActionType.SHIFT_STAFF_SET: 'تنظیم کادر شیفت',
    
    ActionType.PRINT_INVOICE: 'چاپ فاکتور',
    ActionType.PRINT_RECEIPT: 'چاپ رسید',
    ActionType.PRINT_REPORT: 'چاپ گزارش',
    
    ActionType.REPORT_VIEW: 'مشاهده گزارش',
    ActionType.REPORT_EXPORT: 'خروجی گزارش',
}


def log_activity(
    action_type: str,
    action_category: str,
    description: str = None,
    target_type: str = None,
    target_id: int = None,
    target_name: str = None,
    invoice_id: int = None,
    patient_id: int = None,
    patient_name: str = None,
    amount: int = 0,
    old_value: str = None,
    new_value: str = None,
    user_id: int = None,
    username: str = None
):
    """
    ثبت فعالیت در جدول لاگ
    
    Args:
        action_type: نوع عملیات (از ActionType)
        action_category: دسته‌بندی (از ActionCategory)
        description: توضیح سفارشی (اختیاری - اگر نباشد از ACTION_DESCRIPTIONS استفاده می‌شود)
        target_type: نوع هدف (مثل 'visit', 'injection', 'patient')
        target_id: شناسه هدف
        target_name: نام هدف
        invoice_id: شناسه فاکتور مرتبط
        patient_id: شناسه بیمار مرتبط
        patient_name: نام بیمار
        amount: مبلغ (اگر مالی باشد)
        old_value: مقدار قبلی (برای ویرایش)
        new_value: مقدار جدید (برای ویرایش)
        user_id: شناسه کاربر (اگر ندهید از g.user می‌گیرد)
        username: نام کاربر (اگر ندهید از g.user می‌گیرد)
    """
    try:
        db = get_db()
        
        # گرفتن اطلاعات کاربر
        if user_id is None and hasattr(g, 'user') and g.user:
            user_id = g.user['id']
            username = g.user['username']
        
        if user_id is None:
            user_id = 0
            username = 'system'
        
        # توضیح پیش‌فرض
        if description is None:
            description = ACTION_DESCRIPTIONS.get(action_type, action_type)
        
        # گرفتن IP و User-Agent
        ip_address = None
        user_agent = None
        try:
            ip_address = request.remote_addr
            user_agent = request.headers.get('User-Agent', '')[:200]  # محدود به 200 کاراکتر
        except Exception as e:
            print(f"[ActivityLogger] Error getting request info: {e}")
        
        # زمان فعلی تهران
        created_at = iran_now().strftime('%Y-%m-%d %H:%M:%S')
        
        db.execute("""
            INSERT INTO activity_logs (
                user_id, username, action_type, action_category, description,
                target_type, target_id, target_name, invoice_id, patient_id,
                patient_name, amount, old_value, new_value, ip_address,
                user_agent, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, username, action_type, action_category, description,
            target_type, target_id, target_name, invoice_id, patient_id,
            patient_name, amount, old_value, new_value, ip_address,
            user_agent, created_at
        ))
        db.commit()
        
    except Exception as e:
        # لاگ نباید خطا ایجاد کند - فقط چاپ می‌کنیم
        print(f"[ActivityLogger] Error logging activity: {e}")


def get_activity_logs(
    user_id: int = None,
    action_type: str = None,
    action_category: str = None,
    invoice_id: int = None,
    patient_id: int = None,
    date_from: str = None,
    date_to: str = None,
    search_text: str = None,
    limit: int = 100,
    offset: int = 0
) -> list:
    """
    دریافت لیست لاگ‌ها با فیلتر
    تاریخ‌ها می‌توانند شمسی باشند - به میلادی تبدیل می‌شوند
    """
    db = get_db()
    
    # تبدیل تاریخ شمسی به میلادی
    gregorian_date_from = jalali_to_gregorian(date_from) if date_from else None
    gregorian_date_to = jalali_to_gregorian(date_to) if date_to else None
    
    query = "SELECT * FROM activity_logs WHERE 1=1"
    params = []
    
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    
    if action_type:
        query += " AND action_type = ?"
        params.append(action_type)
    
    if action_category:
        query += " AND action_category = ?"
        params.append(action_category)
    
    if invoice_id:
        query += " AND invoice_id = ?"
        params.append(invoice_id)
    
    if patient_id:
        query += " AND patient_id = ?"
        params.append(patient_id)
    
    if gregorian_date_from and gregorian_date_to:
        datetime_from, datetime_to = get_datetime_range_for_date_range(gregorian_date_from, gregorian_date_to)
        query += " AND created_at >= ? AND created_at < ?"
        params.extend([datetime_from, datetime_to])
    elif gregorian_date_from:
        query += " AND created_at >= ?"
        params.append(f"{gregorian_date_from} 00:00:00")
    elif gregorian_date_to:
        _, datetime_to = get_datetime_range_for_date_range(gregorian_date_to, gregorian_date_to)
        query += " AND created_at < ?"
        params.append(datetime_to)
    
    if search_text:
        query += " AND (description LIKE ? OR patient_name LIKE ? OR target_name LIKE ?)"
        search_pattern = f"%{search_text}%"
        params.extend([search_pattern, search_pattern, search_pattern])
    
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_logs_count(
    user_id: int = None,
    action_type: str = None,
    action_category: str = None,
    invoice_id: int = None,
    patient_id: int = None,
    date_from: str = None,
    date_to: str = None,
    search_text: str = None
) -> int:
    """
    شمارش تعداد لاگ‌ها با فیلتر
    تاریخ‌ها می‌توانند شمسی باشند - به میلادی تبدیل می‌شوند
    """
    db = get_db()
    
    # تبدیل تاریخ شمسی به میلادی
    gregorian_date_from = jalali_to_gregorian(date_from) if date_from else None
    gregorian_date_to = jalali_to_gregorian(date_to) if date_to else None
    
    query = "SELECT COUNT(*) as count FROM activity_logs WHERE 1=1"
    params = []
    
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    
    if action_type:
        query += " AND action_type = ?"
        params.append(action_type)
    
    if action_category:
        query += " AND action_category = ?"
        params.append(action_category)
    
    if invoice_id:
        query += " AND invoice_id = ?"
        params.append(invoice_id)
    
    if patient_id:
        query += " AND patient_id = ?"
        params.append(patient_id)
    
    if gregorian_date_from and gregorian_date_to:
        datetime_from, datetime_to = get_datetime_range_for_date_range(gregorian_date_from, gregorian_date_to)
        query += " AND created_at >= ? AND created_at < ?"
        params.extend([datetime_from, datetime_to])
    elif gregorian_date_from:
        query += " AND created_at >= ?"
        params.append(f"{gregorian_date_from} 00:00:00")
    elif gregorian_date_to:
        _, datetime_to = get_datetime_range_for_date_range(gregorian_date_to, gregorian_date_to)
        query += " AND created_at < ?"
        params.append(datetime_to)
    
    if search_text:
        query += " AND (description LIKE ? OR patient_name LIKE ? OR target_name LIKE ?)"
        search_pattern = f"%{search_text}%"
        params.extend([search_pattern, search_pattern, search_pattern])
    
    return db.execute(query, params).fetchone()['count']


def get_user_sessions(user_id: int = None, date: str = None) -> list:
    """
    دریافت لیست جلسات کاری کاربران (ورود تا خروج)
    """
    db = get_db()
    
    query = """
        SELECT user_id, username, action_type, created_at
        FROM activity_logs 
        WHERE action_type IN ('login', 'logout')
    """
    params = []
    
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    
    if date:
        gregorian_date = jalali_to_gregorian(date) or date
        datetime_from, datetime_to = get_datetime_range_for_date_range(gregorian_date, gregorian_date)
        query += " AND created_at >= ? AND created_at < ?"
        params.extend([datetime_from, datetime_to])
    
    query += " ORDER BY created_at ASC"
    
    return [dict(row) for row in db.execute(query, params).fetchall()]


def get_action_stats(date_from: str = None, date_to: str = None) -> dict:
    """
    آمار عملیات‌ها برای گزارش
    """
    db = get_db()
    
    query = """
        SELECT action_category, action_type, COUNT(*) as count
        FROM activity_logs
        WHERE 1=1
    """
    params = []
    
    if date_from and date_to:
        datetime_from, datetime_to = get_datetime_range_for_date_range(date_from, date_to)
        query += " AND created_at >= ? AND created_at < ?"
        params.extend([datetime_from, datetime_to])
    elif date_from:
        query += " AND created_at >= ?"
        params.append(f"{date_from} 00:00:00")
    elif date_to:
        _, datetime_to = get_datetime_range_for_date_range(date_to, date_to)
        query += " AND created_at < ?"
        params.append(datetime_to)
    
    query += " GROUP BY action_category, action_type ORDER BY count DESC"
    
    rows = db.execute(query, params).fetchall()
    
    stats = {}
    for row in rows:
        cat = row['action_category']
        if cat not in stats:
            stats[cat] = []
        stats[cat].append({
            'action_type': row['action_type'],
            'count': row['count'],
            'description': ACTION_DESCRIPTIONS.get(row['action_type'], row['action_type'])
        })
    
    return stats
