from flask import (
    Blueprint, render_template, request, flash, redirect, url_for, g, jsonify, Response, make_response
)
from src.api.auth import login_required
from src.adapters.sqlite.core import get_db
from datetime import datetime, timedelta, date
from src.common.jalali import Gregorian
from src.common.utils import iran_now
import jdatetime
import csv
import io

bp = Blueprint('manager', __name__, url_prefix='/manager')


@bp.route('/')
@login_required
def index():
    """Manager dashboard with reports, staff management, settings."""
    # Check if user is actually a manager
    if g.user['role'] != 'manager':
        flash('دسترسی محدود: فقط مدیر می‌تواند به این بخش دسترسی داشته باشد.', 'error')
        return redirect(url_for('reception.index'))
    
    db = get_db()
    
    # Today's date range (Iran time)
    from src.common.utils import get_work_date_for_datetime
    today_date = get_work_date_for_datetime()
    today_dt = datetime.strptime(today_date, '%Y-%m-%d')
    
    # Today's invoices count
    today_invoices = db.execute("""
        SELECT COUNT(*) as count FROM invoices 
        WHERE work_date = ?
    """, (today_date,)).fetchone()['count']
    
    # Today's revenue (from closed invoices) - EXCLUDING consumables
    # درآمد = ویزیت + خدمات پرستاری + کارهای عملی (بدون مصرفی)
    visits_today = db.execute("""
        SELECT COALESCE(SUM(v.price), 0) as total 
        FROM visits v
        JOIN invoices i ON v.invoice_id = i.id
        WHERE i.work_date = ? AND i.status = 'closed'
    """, (today_date,)).fetchone()['total']
    
    injections_today = db.execute("""
        SELECT COALESCE(SUM(inj.total_price), 0) as total 
        FROM injections inj
        JOIN invoices i ON inj.invoice_id = i.id
        WHERE i.work_date = ? AND i.status = 'closed'
    """, (today_date,)).fetchone()['total']
    
    procedures_today = db.execute("""
        SELECT COALESCE(SUM(pr.price), 0) as total 
        FROM procedures pr
        JOIN invoices i ON pr.invoice_id = i.id
        WHERE i.work_date = ? AND i.status = 'closed'
    """, (today_date,)).fetchone()['total']
    
    today_revenue = visits_today + injections_today + procedures_today
    
    # Today's unique patients
    today_patients = db.execute("""
        SELECT COUNT(DISTINCT patient_id) as count FROM invoices 
        WHERE work_date = ?
    """, (today_date,)).fetchone()['count']
    
    # Open invoices count
    open_invoices = db.execute("""
        SELECT COUNT(*) as count FROM invoices WHERE status = 'open'
    """).fetchone()['count']
    
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
    today_visits = db.execute("""
        SELECT COUNT(*) as count FROM visits 
        WHERE work_date = ?
    """, (today_date,)).fetchone()['count']
    
    today_injections = db.execute("""
        SELECT COUNT(*) as count FROM injections 
        WHERE work_date = ?
    """, (today_date,)).fetchone()['count']
    
    today_procedures = db.execute("""
        SELECT COUNT(*) as count FROM procedures 
        WHERE work_date = ?
    """, (today_date,)).fetchone()['count']
    
    # شمارش خدمات پرستاری (از جدول injections که nurse_id دارد)
    today_nursing = db.execute("""
        SELECT COUNT(*) as count FROM injections 
        WHERE work_date = ?
        AND nurse_id IS NOT NULL
    """, (today_date,)).fetchone()['count']
    
    # کادر درمان
    active_doctors = db.execute("""
        SELECT COUNT(*) as count FROM medical_staff 
        WHERE staff_type = 'doctor' AND is_active = 1
    """).fetchone()['count']
    
    active_nurses = db.execute("""
        SELECT COUNT(*) as count FROM medical_staff 
        WHERE staff_type = 'nurse' AND is_active = 1
    """).fetchone()['count']
    
    total_users = db.execute("""
        SELECT COUNT(*) as count FROM users WHERE is_active = 1
    """).fetchone()['count']
    
    # تشخیص شیفت فعلی (شیفت به صورت دستی تعیین می‌شود)
    from src.common.utils import get_current_shift_name
    shift = get_current_shift_name()
    shift_names = {'morning': 'صبح', 'evening': 'عصر', 'night': 'شب'}
    current_shift = shift_names.get(shift, shift)
    
    # آمار کلی
    total_patients = db.execute("""
        SELECT COUNT(*) as count FROM patients
    """).fetchone()['count']
    
    # آمار این ماه شمسی (محاسبه دقیق اول ماه شمسی)
    import jdatetime
    j_today = jdatetime.date.fromgregorian(date=iran_now().date())
    j_month_start = jdatetime.date(j_today.year, j_today.month, 1)
    g_month_start = j_month_start.togregorian()
    month_start = datetime(g_month_start.year, g_month_start.month, g_month_start.day)
    month_start_date = month_start.strftime('%Y-%m-%d')
    
    month_invoices = db.execute("""
        SELECT COUNT(*) as count FROM invoices 
        WHERE work_date >= ?
    """, (month_start_date,)).fetchone()['count']
    
    # Month revenue - EXCLUDING consumables
    # درآمد = ویزیت + خدمات پرستاری + کارهای عملی (بدون مصرفی)
    visits_month = db.execute("""
        SELECT COALESCE(SUM(v.price), 0) as total 
        FROM visits v
        JOIN invoices i ON v.invoice_id = i.id
        WHERE i.work_date >= ? AND i.status = 'closed'
    """, (month_start_date,)).fetchone()['total']
    
    injections_month = db.execute("""
        SELECT COALESCE(SUM(inj.total_price), 0) as total 
        FROM injections inj
        JOIN invoices i ON inj.invoice_id = i.id
        WHERE i.work_date >= ? AND i.status = 'closed'
    """, (month_start_date,)).fetchone()['total']
    
    procedures_month = db.execute("""
        SELECT COALESCE(SUM(pr.price), 0) as total 
        FROM procedures pr
        JOIN invoices i ON pr.invoice_id = i.id
        WHERE i.work_date >= ? AND i.status = 'closed'
    """, (month_start_date,)).fetchone()['total']
    
    month_revenue = visits_month + injections_month + procedures_month
    
    avg_daily = month_revenue / 30 if month_revenue > 0 else 0

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
            'from': {
                'y': j_start[0], 'm': j_start[1], 'd': j_start[2]
            },
            'to': {
                'y': j_end[0], 'm': j_end[1], 'd': j_end[2]
            }
        }

    ranges = {
        'today': {
            'y': j_today[0], 'm': j_today[1], 'd': j_today[2]
        },
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }
    
    return render_template(
        'manager/index.html',
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
        server_time=iran_now().isoformat()
    )


@bp.route('/reports')
@login_required
def reports():
    """Financial and operational reports."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))
    return render_template('manager/reports.html')


@bp.route('/reports/invoices')
@login_required
def invoices_report():
    """گزارش فاکتورها با فیلترهای دقیق و تاریخ شمسی شش‌تایی."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))

    db = get_db()

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    status = request.args.get('status', '').strip() or None  # open/closed
    insurance_type = request.args.get('insurance_type', '').strip() or None
    reception_user = request.args.get('reception_user', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {
        'date_from': start_dt.strftime('%Y-%m-%d'),
        'date_to': end_dt.strftime('%Y-%m-%d')
    }
    where = ["i.work_date BETWEEN :date_from AND :date_to"]
    if status:
        where.append("i.status = :status"); params['status'] = status
    if insurance_type:
        where.append("i.insurance_type = :insurance_type"); params['insurance_type'] = insurance_type
    if reception_user:
        where.append("i.opened_by = :reception_user"); params['reception_user'] = reception_user
    where_sql = " AND ".join(where)

    rows = db.execute(f'''
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
    ''', params).fetchall()
    invoices = [dict(r) for r in rows]

    total_count = len(invoices)
    total_closed = sum(1 for r in invoices if r['status'] == 'closed')
    total_open = total_count - total_closed
    total_amount = sum((r['total_amount'] or 0) for r in invoices)

    # فیلترها
    users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
    insurances = db.execute("SELECT DISTINCT insurance_type FROM invoices WHERE insurance_type IS NOT NULL ORDER BY insurance_type").fetchall()

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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }

    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }

    return render_template(
        'manager/reports_invoices.html',
        invoices=invoices,
        total_count=total_count,
        total_closed=total_closed,
        total_open=total_open,
        total_amount=total_amount,
        users=users,
        insurances=insurances,
        jalali_ranges=ranges,
        active_filters={
            'from': date_from,
            'to': date_to,
            'status': status,
            'insurance_type': insurance_type,
            'reception_user': reception_user,
        }
    )


@bp.route('/export/invoices/csv')
@login_required
def export_invoices_csv():
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403

    db = get_db()
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    status = request.args.get('status', '').strip() or None
    insurance_type = request.args.get('insurance_type', '').strip() or None
    reception_user = request.args.get('reception_user', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {
        'date_from': start_dt.strftime('%Y-%m-%d'),
        'date_to': end_dt.strftime('%Y-%m-%d')
    }
    where = ["i.work_date BETWEEN :date_from AND :date_to"]
    if status:
        where.append("i.status = :status"); params['status'] = status
    if insurance_type:
        where.append("i.insurance_type = :insurance_type"); params['insurance_type'] = insurance_type
    if reception_user:
        where.append("i.opened_by = :reception_user"); params['reception_user'] = reception_user
    where_sql = " AND ".join(where)

    rows = db.execute(f'''
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
    ''', params).fetchall()

    output = io.StringIO(); output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(['شماره فاکتور', 'زمان باز شدن', 'زمان بستن', 'وضعیت', 'مبلغ کل', 'بیمه', 'بیمه تکمیلی', 'پذیرش', 'بستن توسط', 'بیمار'])
    for r in rows:
        writer.writerow([
            r['id'], r['opened_at'] or '', r['closed_at'] or '', r['status'] or '',
            int(r['total_amount'] or 0), r['insurance_type'] or '', r['supplementary_insurance'] or '',
            (r.get('opened_by_name') or r.get('opened_by') or ''), (r.get('closed_by_name') or r.get('closed_by') or ''), r['patient_name'] or ''
        ])
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename="invoices_report.csv"'
    return resp


@bp.route('/staff')
@login_required
def staff():
    """Staff management."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))
    return render_template('manager/staff.html')


def get_network_info():
    """Get network information for the server."""
    import socket
    from flask import request
    
    # Get actual port from request or default to 8080
    try:
        current_port = request.host.split(':')[1] if ':' in request.host else '8080'
    except Exception:
        current_port = '8080'
    
    network_info = {
        'hostname': socket.gethostname(),
        'local_ips': [],
        'port': current_port,
        'access_urls': [],
        'wifi_name': None,
        'connection_type': None
    }
    
    try:
        # Get all network interfaces
        hostname = socket.gethostname()
        
        # Method 1: Get local IPs
        try:
            # Get primary IP by connecting to external server (doesn't actually connect)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
            s.close()
            if primary_ip and primary_ip not in network_info['local_ips']:
                network_info['local_ips'].append(primary_ip)
        except Exception:
            pass
        
        # Method 2: Get all IPs from hostname
        try:
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if ip not in network_info['local_ips'] and not ip.startswith('127.'):
                    network_info['local_ips'].append(ip)
        except Exception:
            pass
        
        # Method 3: Using getaddrinfo
        try:
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if ip not in network_info['local_ips'] and not ip.startswith('127.'):
                    network_info['local_ips'].append(ip)
        except Exception:
            pass
        
        # Add localhost as fallback
        network_info['local_ips'].append('127.0.0.1')
        
        # Generate access URLs
        for ip in network_info['local_ips']:
            network_info['access_urls'].append(f"http://{ip}:{network_info['port']}")
        
    except Exception as e:
        network_info['error'] = str(e)
    
    return network_info


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """System settings and configuration with backup management."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))
    
    import os
    import shutil
    from pathlib import Path
    
    db = get_db()
    backup_dir = Path(__file__).parent.parent.parent / 'backups'
    backup_dir.mkdir(exist_ok=True)
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create_backup':
            # ایجاد بکاپ دستی
            try:
                db_path = Path(__file__).parent.parent.parent / 'clinic_new.db'
                timestamp = iran_now().strftime('%Y%m%d_%H%M%S')
                backup_name = f"backup_{timestamp}.db"
                backup_path = backup_dir / backup_name
                
                shutil.copy2(db_path, backup_path)
                flash(f'بکاپ با موفقیت ایجاد شد: {backup_name}', 'success')
            except Exception as e:
                flash(f'خطا در ایجاد بکاپ: {str(e)}', 'error')
        
        elif action == 'restore_backup':
            # بازگردانی بکاپ
            backup_name = request.form.get('backup_name')
            if backup_name:
                try:
                    backup_path = backup_dir / backup_name
                    db_path = Path(__file__).parent.parent.parent / 'clinic_new.db'
                    
                    if backup_path.exists():
                        # ابتدا از دیتابیس فعلی بکاپ می‌گیریم
                        timestamp = iran_now().strftime('%Y%m%d_%H%M%S')
                        pre_restore_backup = backup_dir / f"pre_restore_{timestamp}.db"
                        shutil.copy2(db_path, pre_restore_backup)
                        
                        # بازگردانی
                        shutil.copy2(backup_path, db_path)
                        flash(f'دیتابیس با موفقیت بازگردانی شد از: {backup_name}', 'success')
                    else:
                        flash('فایل بکاپ یافت نشد', 'error')
                except Exception as e:
                    flash(f'خطا در بازگردانی: {str(e)}', 'error')
        
        elif action == 'delete_backup':
            # حذف بکاپ
            backup_name = request.form.get('backup_name')
            if backup_name:
                try:
                    backup_path = backup_dir / backup_name
                    if backup_path.exists():
                        backup_path.unlink()
                        flash(f'بکاپ حذف شد: {backup_name}', 'success')
                    else:
                        flash('فایل بکاپ یافت نشد', 'error')
                except Exception as e:
                    flash(f'خطا در حذف بکاپ: {str(e)}', 'error')
        
        elif action == 'save_settings':
            # ذخیره تنظیمات کلینیک
            clinic_name = request.form.get('clinic_name', '')
            clinic_phone = request.form.get('clinic_phone', '')
            clinic_address = request.form.get('clinic_address', '')
            auto_backup = request.form.get('auto_backup', '0')
            
            # ذخیره در جدول settings
            db.execute("DELETE FROM settings WHERE key IN ('clinic_name', 'clinic_phone', 'clinic_address', 'auto_backup')")
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('clinic_name', clinic_name))
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('clinic_phone', clinic_phone))
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('clinic_address', clinic_address))
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('auto_backup', auto_backup))
            db.commit()
            flash('تنظیمات ذخیره شد', 'success')
        
        return redirect(url_for('manager.settings'))
    
    # دریافت لیست بکاپ‌ها
    backups = []
    if backup_dir.exists():
        for f in sorted(backup_dir.glob('*.db'), key=lambda x: x.stat().st_mtime, reverse=True):
            stat = f.stat()
            # تبدیل تاریخ به شمسی
            mtime = datetime.fromtimestamp(stat.st_mtime)
            jalali_date = Gregorian(mtime.date()).persian_string()
            jalali_time = mtime.strftime('%H:%M:%S')
            
            backups.append({
                'name': f.name,
                'size': round(stat.st_size / 1024, 1),  # KB
                'date': f"{jalali_date} - {jalali_time}",
                'timestamp': stat.st_mtime
            })
    
    # دریافت تنظیمات فعلی
    settings_data = {}
    try:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            settings_data[row['key']] = row['value']
    except Exception as e:
        print(f"[Manager.settings] Error loading settings: {e}")
    
    # آمار دیتابیس
    db_stats = {
        'patients': db.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        'invoices': db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
        'visits': db.execute("SELECT COUNT(*) FROM visits").fetchone()[0],
        'injections': db.execute("SELECT COUNT(*) FROM injections").fetchone()[0],
        'procedures': db.execute("SELECT COUNT(*) FROM procedures").fetchone()[0],
        'users': db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
    }
    
    # اندازه دیتابیس
    db_path = Path(__file__).parent.parent.parent / 'clinic_new.db'
    db_size = round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.exists() else 0
    
    # اطلاعات شبکه
    network_info = get_network_info()
    
    return render_template(
        'manager/settings.html',
        backups=backups,
        settings=settings_data,
        db_stats=db_stats,
        db_size=db_size,
        network_info=network_info
    )


@bp.route('/settings/download/<backup_name>')
@login_required
def download_backup(backup_name):
    """دانلود فایل بکاپ"""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))
    
    from pathlib import Path
    from flask import send_file
    
    backup_dir = Path(__file__).parent.parent.parent / 'backups'
    backup_path = backup_dir / backup_name
    
    if backup_path.exists() and backup_name.endswith('.db'):
        return send_file(
            backup_path,
            as_attachment=True,
            download_name=backup_name
        )
    else:
        flash('فایل بکاپ یافت نشد', 'error')
        return redirect(url_for('manager.settings'))


@bp.route('/settings/upload', methods=['POST'])
@login_required
def upload_backup():
    """آپلود فایل بکاپ"""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))
    
    from pathlib import Path
    
    if 'backup_file' not in request.files:
        flash('فایلی انتخاب نشده', 'error')
        return redirect(url_for('manager.settings'))
    
    file = request.files['backup_file']
    if file.filename == '':
        flash('فایلی انتخاب نشده', 'error')
        return redirect(url_for('manager.settings'))
    
    if file and file.filename.endswith('.db'):
        backup_dir = Path(__file__).parent.parent.parent / 'backups'
        backup_dir.mkdir(exist_ok=True)
        
        # نام با timestamp برای جلوگیری از overwrite
        timestamp = iran_now().strftime('%Y%m%d_%H%M%S')
        safe_name = f"uploaded_{timestamp}.db"
        backup_path = backup_dir / safe_name
        
        file.save(backup_path)
        flash(f'فایل بکاپ آپلود شد: {safe_name}', 'success')
    else:
        flash('فقط فایل‌های .db مجاز هستند', 'error')
    
    return redirect(url_for('manager.settings'))


@bp.route('/api/chart-data')
@login_required
def chart_data():
    """API برای داده‌های نمودار با پشتیبانی تاریخ شمسی."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    
    # دریافت پارامترها
    date_from = request.args.get('from', '')  # فرمت: 1404/09/01
    date_to = request.args.get('to', '')      # فرمت: 1404/09/06
    data_type = request.args.get('type', 'revenue')
    
    # تبدیل تاریخ شمسی به میلادی
    def jalali_to_gregorian(jalali_str):
        try:
            parts = jalali_str.split('/')
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
        day_key = current.strftime('%Y-%m-%d')
        
        # لیبل شمسی
        labels.append(gregorian_to_jalali_label(current))
        
        # کوئری بر اساس نوع داده
        if data_type == 'revenue':
            # Revenue = visits + injections + procedures (NOT consumables)
            v_rev = db.execute("""
                SELECT COALESCE(SUM(v.price), 0) as val FROM visits v
                JOIN invoices i ON v.invoice_id = i.id
                WHERE i.work_date = ? AND i.status = 'closed'
            """, (day_key,)).fetchone()['val']
            inj_rev = db.execute("""
                SELECT COALESCE(SUM(inj.total_price), 0) as val FROM injections inj
                JOIN invoices i ON inj.invoice_id = i.id
                WHERE i.work_date = ? AND i.status = 'closed'
            """, (day_key,)).fetchone()['val']
            pr_rev = db.execute("""
                SELECT COALESCE(SUM(pr.price), 0) as val FROM procedures pr
                JOIN invoices i ON pr.invoice_id = i.id
                WHERE i.work_date = ? AND i.status = 'closed'
            """, (day_key,)).fetchone()['val']
            result = v_rev + inj_rev + pr_rev
        
        elif data_type == 'invoices':
            result = db.execute("""
                SELECT COUNT(*) as val FROM invoices 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val']
        
        elif data_type == 'patients':
            result = db.execute("""
                SELECT COUNT(DISTINCT patient_id) as val FROM invoices 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val']
        
        elif data_type == 'visits':
            result = db.execute("""
                SELECT COUNT(*) as val FROM visits 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val']
        
        elif data_type == 'injections':
            result = db.execute("""
                SELECT COUNT(*) as val FROM injections 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val']
        
        elif data_type == 'procedures':
            result = db.execute("""
                SELECT COUNT(*) as val FROM procedures 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val']
        
        elif data_type == 'consumables':
            # Count only consumables provided by the center and not exception items
            result = db.execute("""
                SELECT COUNT(*) as val FROM consumables_ledger 
                WHERE work_date = ? AND (COALESCE(patient_provided,0) = 0 AND COALESCE(is_exception,0) = 0)
            """, (day_key,)).fetchone()['val']
        
        elif data_type == 'services':
            # خدمات تفکیکی - برگرداندن هر سه نوع
            visits_count = db.execute("""
                SELECT COUNT(*) as val FROM visits 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val'] or 0
            
            injections_count = db.execute("""
                SELECT COUNT(*) as val FROM injections 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val'] or 0
            
            procedures_count = db.execute("""
                SELECT COUNT(*) as val FROM procedures 
                WHERE work_date = ?
            """, (day_key,)).fetchone()['val'] or 0
            
            visits_data.append(visits_count)
            injections_data.append(injections_count)
            procedures_data.append(procedures_count)
            result = visits_count + injections_count + procedures_count
        
        else:
            result = 0
        
        values.append(result or 0)
        current = next_day
    
    # اگر نوع services بود، داده‌های تفکیکی برگردان
    if data_type == 'services':
        return jsonify({
            'labels': labels,
            'values': values,
            'datasets': {
                'visits': visits_data,
                'injections': injections_data,
                'procedures': procedures_data
            }
        })
    
    return jsonify({
        'labels': labels,
        'values': values
    })


@bp.route('/reports/visits')
@login_required
def visits_report():
    """گزارش مدیریتی ویزیت‌ها با فیلترهای تاریخ، پزشک، بیمه، پذیرش و شیفت."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))

    db = get_db()

    # دریافت فیلترها
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    doctor_id = request.args.get('doctor_id', type=int)
    insurance_type = request.args.get('insurance_type', '').strip() or None
    reception_user = request.args.get('reception_user', '').strip() or None
    shift = request.args.get('shift', '').strip() or None

    # تبدیل شمسی به میلادی (شروع/پایان روز)
    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)

    # پیش‌فرض: 7 روز گذشته در صورت عدم ارسال
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)

    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {
        'date_from': start_dt.strftime('%Y-%m-%d'),
        'date_to': end_dt.strftime('%Y-%m-%d')
    }

    where_clauses = ["v.work_date BETWEEN :date_from AND :date_to"]

    if doctor_id:
        where_clauses.append("(v.doctor_id = :doctor_id)")
        params['doctor_id'] = doctor_id

    if insurance_type:
        where_clauses.append("v.insurance_type = :insurance_type")
        params['insurance_type'] = insurance_type

    if reception_user:
        # از فاکتور بازکننده به عنوان کاربر پذیرش استفاده می‌کنیم
        where_clauses.append("inv.opened_by = :reception_user")
        params['reception_user'] = reception_user

    if shift:
        where_clauses.append("v.shift = :shift")
        params['shift'] = shift

    where_sql = " AND ".join(where_clauses)

    # داده‌های ریز ویزیت‌ها
    visits = db.execute(f'''
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
    ''', params).fetchall()

    visits_list = [dict(r) for r in visits]

    # خلاصه‌ها
    total_visits = len(visits_list)
    total_amount = sum(r['total_amount'] or 0 for r in visits_list)
    unique_patients = len({r['patient_name'] for r in visits_list})

    # لیست‌های کمکی برای فیلتر (پزشک، کاربر، بیمه)
    doctors = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' AND is_active=1 ORDER BY full_name").fetchall()
    users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
    insurances = db.execute("SELECT DISTINCT insurance_type FROM visits WHERE insurance_type IS NOT NULL ORDER BY insurance_type").fetchall()

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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }

    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }

    return render_template(
        'manager/reports_visits.html',
        visits=visits_list,
        total_visits=total_visits,
        total_amount=total_amount,
        unique_patients=unique_patients,
        doctors=doctors,
        users=users,
        insurances=insurances,
        jalali_ranges=ranges,
        active_filters={
            'from': date_from,
            'to': date_to,
            'doctor_id': doctor_id,
            'insurance_type': insurance_type,
            'reception_user': reception_user,
            'shift': shift,
        }
    )


@bp.route('/reports/nursing')
@login_required
def nursing_report():
    """گزارش مدیریتی خدمات پرستاری (تزریقات/سرم‌ها) با فیلترهای غنی."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))

    db = get_db()

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    doctor_id = request.args.get('doctor_id', type=int)
    nurse_id = request.args.get('nurse_id', type=int)
    service_name = request.args.get('service_name', '').strip() or None
    shift = request.args.get('shift', '').strip() or None
    related_to_doctor = request.args.get('related_doctor') == '1'

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {
        'date_from': start_dt.strftime('%Y-%m-%d'),
        'date_to': end_dt.strftime('%Y-%m-%d')
    }
    where_clauses = ["i.work_date BETWEEN :date_from AND :date_to"]

    if doctor_id:
        where_clauses.append("i.doctor_id = :doctor_id")
        params['doctor_id'] = doctor_id
    if nurse_id:
        where_clauses.append("i.nurse_id = :nurse_id")
        params['nurse_id'] = nurse_id
    if service_name:
        where_clauses.append("i.injection_type = :stype")
        params['stype'] = service_name
    if shift:
        where_clauses.append("i.shift = :shift")
        params['shift'] = shift
    if related_to_doctor:
        where_clauses.append("EXISTS (SELECT 1 FROM visits v WHERE v.invoice_id = i.invoice_id)")

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(f'''
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
    ''', params).fetchall()

    injections = [dict(r) for r in rows]
    total_services = len(injections)
    total_amount = sum(r['total_price'] or 0 for r in injections)

    doctors = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' AND is_active=1 ORDER BY full_name").fetchall()
    nurses = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' AND is_active=1 ORDER BY full_name").fetchall()
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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }

    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }

    return render_template(
        'manager/reports_nursing.html',
        injections=injections,
        total_services=total_services,
        total_amount=total_amount,
        doctors=doctors,
        nurses=nurses,
        service_names=service_names,
        jalali_ranges=ranges,
        active_filters={
            'from': date_from,
            'to': date_to,
            'doctor_id': doctor_id,
            'nurse_id': nurse_id,
            'service_name': service_name,
            'shift': shift,
            'related_doctor': '1' if related_to_doctor else '0',
        }
    )


@bp.route('/reports/procedures')
@login_required
def procedures_report():
    """گزارش مدیریتی کارهای عملی با فیلترهای تاریخ، نوع کار، انجام‌دهنده، پزشک/پرستار و شیفت."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))

    db = get_db()

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    doctor_id = request.args.get('doctor_id', type=int)
    nurse_id = request.args.get('nurse_id', type=int)
    performer_type = request.args.get('performer_type', '').strip() or None
    procedure_type = request.args.get('procedure_type', '').strip() or None
    shift = request.args.get('shift', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {
        'date_from': start_dt.strftime('%Y-%m-%d'),
        'date_to': end_dt.strftime('%Y-%m-%d')
    }
    where_clauses = ["pr.work_date BETWEEN :date_from AND :date_to"]

    if doctor_id:
        where_clauses.append("pr.doctor_id = :doctor_id")
        params['doctor_id'] = doctor_id
    if nurse_id:
        where_clauses.append("pr.nurse_id = :nurse_id")
        params['nurse_id'] = nurse_id
    if performer_type:
        where_clauses.append("pr.performer_type = :ptype")
        params['ptype'] = performer_type
    if procedure_type:
        where_clauses.append("pr.procedure_type = :prtype")
        params['prtype'] = procedure_type
    if shift:
        where_clauses.append("pr.shift = :shift")
        params['shift'] = shift

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(f'''
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
    ''', params).fetchall()

    procedures = [dict(r) for r in rows]
    total_count = len(procedures)
    total_amount = sum(r['price'] or 0 for r in procedures)

    doctors = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' AND is_active=1 ORDER BY full_name").fetchall()
    nurses = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' AND is_active=1 ORDER BY full_name").fetchall()
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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }

    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }

    return render_template(
        'manager/reports_procedures.html',
        procedures=procedures,
        total_count=total_count,
        total_amount=total_amount,
        doctors=doctors,
        nurses=nurses,
        procedure_types=procedure_types,
        jalali_ranges=ranges,
        active_filters={
            'from': date_from,
            'to': date_to,
            'doctor_id': doctor_id,
            'nurse_id': nurse_id,
            'performer_type': performer_type,
            'procedure_type': procedure_type,
            'shift': shift,
        }
    )


@bp.route('/reports/users')
@login_required
def users_report():
    """گزارش عملکرد کاربران (پزشکان، پرستاران، پذیرش) با فیلترهای تاریخ، نقش و کاربر."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))

    db = get_db()

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    role_filter = request.args.get('role', '').strip() or None
    user_filter = request.args.get('user', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    start_date = start_dt.strftime('%Y-%m-%d')
    end_date = end_dt.strftime('%Y-%m-%d')

    results = []

    # ===== عملکرد کاربران پذیرش (reception) =====
    if not role_filter or role_filter == 'reception':
        # only list users with role 'reception' here — avoid showing managers or other roles
        reception_users = db.execute("SELECT username, full_name FROM users WHERE role = 'reception' ORDER BY full_name").fetchall()
        for u in reception_users:
            uname = u['username']
            if user_filter and uname != user_filter:
                continue
            # فاکتورهای باز شده
            inv_count = db.execute("""
                SELECT COUNT(*) as cnt FROM invoices
                WHERE opened_by = ? AND work_date BETWEEN ? AND ?
            """, (uname, start_date, end_date)).fetchone()['cnt']
            # ویزیت‌ها (از طریق invoice opened_by)
            visits_count = db.execute("""
                SELECT COUNT(*) as cnt FROM visits v
                JOIN invoices i ON i.id = v.invoice_id
                WHERE i.opened_by = ? AND v.work_date BETWEEN ? AND ?
            """, (uname, start_date, end_date)).fetchone()['cnt']
            # خدمات پرستاری
            nursing_count = db.execute("""
                SELECT COUNT(*) as cnt FROM injections inj
                JOIN invoices i ON i.id = inj.invoice_id
                WHERE i.opened_by = ? AND inj.work_date BETWEEN ? AND ?
            """, (uname, start_date, end_date)).fetchone()['cnt']
            # کارهای عملی
            proc_count = db.execute("""
                SELECT COUNT(*) as cnt FROM procedures pr
                JOIN invoices i ON i.id = pr.invoice_id
                WHERE i.opened_by = ? AND pr.work_date BETWEEN ? AND ?
            """, (uname, start_date, end_date)).fetchone()['cnt']
            # مصرفی‌ها - جدا محاسبه کن: عمومی (supply) و دارو (drug)
            cons_supply_count = db.execute("""
                SELECT COUNT(*) as cnt FROM consumables_ledger cl
                JOIN invoices i ON i.id = cl.invoice_id
                WHERE i.opened_by = ? AND cl.work_date BETWEEN ? AND ? AND cl.category = 'supply' AND (cl.patient_provided = 0 OR COALESCE(cl.is_exception,0) = 1)
            """, (uname, start_date, end_date)).fetchone()['cnt']
            cons_drug_count = db.execute("""
                SELECT COUNT(*) as cnt FROM consumables_ledger cl
                JOIN invoices i ON i.id = cl.invoice_id
                WHERE i.opened_by = ? AND cl.work_date BETWEEN ? AND ? AND cl.category = 'drug' AND (cl.patient_provided = 0 OR COALESCE(cl.is_exception,0) = 1)
            """, (uname, start_date, end_date)).fetchone()['cnt']
            # درآمد کل (فاکتورهای بسته) - بدون مصرفی
            # Revenue = visits + injections + procedures (NOT consumables)
            visits_rev = db.execute("""
                SELECT COALESCE(SUM(v.price), 0) as total FROM visits v
                JOIN invoices i ON i.id = v.invoice_id
                WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'
            """, (uname, start_date, end_date)).fetchone()['total']
            injections_rev = db.execute("""
                SELECT COALESCE(SUM(inj.total_price), 0) as total FROM injections inj
                JOIN invoices i ON i.id = inj.invoice_id
                WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'
            """, (uname, start_date, end_date)).fetchone()['total']
            procedures_rev = db.execute("""
                SELECT COALESCE(SUM(pr.price), 0) as total FROM procedures pr
                JOIN invoices i ON i.id = pr.invoice_id
                WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'
            """, (uname, start_date, end_date)).fetchone()['total']
            revenue = visits_rev + injections_rev + procedures_rev

            results.append({
                'user': uname,
                'full_name': u['full_name'],
                'role': 'reception',
                'role_fa': 'پذیرش',
                'invoices': inv_count,
                'visits': visits_count,
                'nursing': nursing_count,
                'procedures': proc_count,
                'consumables_supply': cons_supply_count,
                'consumables_drug': cons_drug_count,
                'revenue': revenue,
            })

    # ===== عملکرد پزشکان (doctor) =====
    if not role_filter or role_filter == 'doctor':
        doctors = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' ORDER BY full_name").fetchall()
        for doc in doctors:
            doc_id = doc['id']
            if user_filter and str(doc_id) != user_filter:
                continue
            # ویزیت‌ها
            visits_count = db.execute("""
                SELECT COUNT(*) as cnt FROM visits
                WHERE doctor_id = ? AND work_date BETWEEN ? AND ?
            """, (doc_id, start_date, end_date)).fetchone()['cnt']
            visits_revenue = db.execute("""
                SELECT COALESCE(SUM(price), 0) as total FROM visits v
                JOIN invoices i ON i.id = v.invoice_id AND i.status = 'closed'
                WHERE v.doctor_id = ? AND v.work_date BETWEEN ? AND ?
            """, (doc_id, start_date, end_date)).fetchone()['total']
            # خدمات پرستاری تحت نظر این پزشک
            nursing_count = db.execute("""
                SELECT COUNT(*) as cnt FROM injections
                WHERE doctor_id = ? AND work_date BETWEEN ? AND ?
            """, (doc_id, start_date, end_date)).fetchone()['cnt']
            nursing_revenue = db.execute("""
                SELECT COALESCE(SUM(total_price), 0) as total FROM injections inj
                JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed'
                WHERE inj.doctor_id = ? AND inj.work_date BETWEEN ? AND ?
            """, (doc_id, start_date, end_date)).fetchone()['total']
            # کارهای عملی - فقط کارهایی که پزشک انجام‌دهنده بوده (performer_type='doctor')
            proc_count = db.execute("""
                SELECT COUNT(*) as cnt FROM procedures
                WHERE doctor_id = ? AND performer_type = 'doctor' AND work_date BETWEEN ? AND ?
            """, (doc_id, start_date, end_date)).fetchone()['cnt']
            proc_revenue = db.execute("""
                SELECT COALESCE(SUM(price), 0) as total FROM procedures pr
                JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed'
                WHERE pr.doctor_id = ? AND pr.performer_type = 'doctor' AND pr.work_date BETWEEN ? AND ?
            """, (doc_id, start_date, end_date)).fetchone()['total']
            
            total_revenue = visits_revenue + nursing_revenue + proc_revenue

            results.append({
                'user': str(doc_id),
                'full_name': doc['full_name'],
                'role': 'doctor',
                'role_fa': 'پزشک',
                'invoices': 0,
                'visits': visits_count,
                'nursing': nursing_count,
                'procedures': proc_count,
                'consumables': 0,
                'revenue': total_revenue,
            })

    # ===== عملکرد پرستاران (nurse) =====
    if not role_filter or role_filter == 'nurse':
        nurses = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' ORDER BY full_name").fetchall()
        for nurse in nurses:
            nurse_id = nurse['id']
            if user_filter and str(nurse_id) != user_filter:
                continue
            # خدمات پرستاری
            nursing_count = db.execute("""
                SELECT COUNT(*) as cnt FROM injections
                WHERE nurse_id = ? AND work_date BETWEEN ? AND ?
            """, (nurse_id, start_date, end_date)).fetchone()['cnt']
            nursing_revenue = db.execute("""
                SELECT COALESCE(SUM(total_price), 0) as total FROM injections inj
                JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed'
                WHERE inj.nurse_id = ? AND inj.work_date BETWEEN ? AND ?
            """, (nurse_id, start_date, end_date)).fetchone()['total']
            # کارهای عملی - فقط کارهایی که پرستار انجام‌دهنده بوده (performer_type='nurse')
            proc_count = db.execute("""
                SELECT COUNT(*) as cnt FROM procedures
                WHERE nurse_id = ? AND performer_type = 'nurse' AND work_date BETWEEN ? AND ?
            """, (nurse_id, start_date, end_date)).fetchone()['cnt']
            proc_revenue = db.execute("""
                SELECT COALESCE(SUM(price), 0) as total FROM procedures pr
                JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed'
                WHERE pr.nurse_id = ? AND pr.performer_type = 'nurse' AND pr.work_date BETWEEN ? AND ?
            """, (nurse_id, start_date, end_date)).fetchone()['total']
            
            total_revenue = nursing_revenue + proc_revenue

            results.append({
                'user': str(nurse_id),
                'full_name': nurse['full_name'],
                'role': 'nurse',
                'role_fa': 'پرستار',
                'invoices': 0,
                'visits': 0,
                'nursing': nursing_count,
                'procedures': proc_count,
                'consumables': 0,
                'revenue': total_revenue,
            })

    # لیست کاربران و نقش‌ها برای فیلتر
    all_users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
    all_doctors = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' ORDER BY full_name").fetchall()
    all_nurses = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' ORDER BY full_name").fetchall()

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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }

    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }

    return render_template(
        'manager/reports_users.html',
        results=results,
        all_users=all_users,
        all_doctors=all_doctors,
        all_nurses=all_nurses,
        jalali_ranges=ranges,
        active_filters={
            'from': date_from,
            'to': date_to,
            'role': role_filter,
            'user': user_filter,
        }
    )


# ===================== Export APIs =====================

def make_csv_response(data, headers, filename):
    """ساخت پاسخ CSV با پشتیبانی کامل فارسی (UTF-8 BOM)."""
    output = io.StringIO()
    # اضافه کردن BOM برای Excel
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in data:
        writer.writerow(row)
    
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@bp.route('/export/users/csv')
@login_required
def export_users_csv():
    """خروجی CSV گزارش عملکرد کاربران."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403

    db = get_db()
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    role_filter = request.args.get('role', '').strip() or None
    user_filter = request.args.get('user', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    start_date = start_dt.strftime('%Y-%m-%d')
    end_date = end_dt.strftime('%Y-%m-%d')

    results = []

    # پذیرش
    if not role_filter or role_filter == 'reception':
        reception_users = db.execute("SELECT username, full_name FROM users ORDER BY full_name").fetchall()
        for u in reception_users:
            uname = u['username']
            if user_filter and uname != user_filter:
                continue
            inv_count = db.execute("SELECT COUNT(*) as cnt FROM invoices WHERE opened_by = ? AND work_date BETWEEN ? AND ?", (uname, start_date, end_date)).fetchone()['cnt']
            visits_count = db.execute("SELECT COUNT(*) as cnt FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.opened_by = ? AND v.work_date BETWEEN ? AND ?", (uname, start_date, end_date)).fetchone()['cnt']
            nursing_count = db.execute("SELECT COUNT(*) as cnt FROM injections inj JOIN invoices i ON i.id = inj.invoice_id WHERE i.opened_by = ? AND inj.work_date BETWEEN ? AND ?", (uname, start_date, end_date)).fetchone()['cnt']
            proc_count = db.execute("SELECT COUNT(*) as cnt FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id WHERE i.opened_by = ? AND pr.work_date BETWEEN ? AND ?", (uname, start_date, end_date)).fetchone()['cnt']
            cons_count = db.execute("SELECT COUNT(*) as cnt FROM consumables_ledger cl JOIN invoices i ON i.id = cl.invoice_id WHERE i.opened_by = ? AND cl.work_date BETWEEN ? AND ? AND (cl.patient_provided = 0 OR COALESCE(cl.is_exception,0) = 1)", (uname, start_date, end_date)).fetchone()['cnt']
            # Revenue = visits + injections + procedures (NOT consumables)
            v_rev = db.execute("SELECT COALESCE(SUM(v.price), 0) as total FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'", (uname, start_date, end_date)).fetchone()['total']
            inj_rev = db.execute("SELECT COALESCE(SUM(inj.total_price), 0) as total FROM injections inj JOIN invoices i ON i.id = inj.invoice_id WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'", (uname, start_date, end_date)).fetchone()['total']
            pr_rev = db.execute("SELECT COALESCE(SUM(pr.price), 0) as total FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id WHERE i.opened_by = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'", (uname, start_date, end_date)).fetchone()['total']
            revenue = v_rev + inj_rev + pr_rev
            results.append([uname, u['full_name'], 'پذیرش', inv_count, visits_count, nursing_count, proc_count, cons_count, revenue])

    # پزشکان
    if not role_filter or role_filter == 'doctor':
        doctors = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='doctor' ORDER BY full_name").fetchall()
        for doc in doctors:
            doc_id = doc['id']
            if user_filter and str(doc_id) != user_filter:
                continue
            visits_count = db.execute("SELECT COUNT(*) as cnt FROM visits WHERE doctor_id = ? AND work_date BETWEEN ? AND ?", (doc_id, start_date, end_date)).fetchone()['cnt']
            visits_revenue = db.execute("SELECT COALESCE(SUM(price), 0) as total FROM visits v JOIN invoices i ON i.id = v.invoice_id AND i.status = 'closed' WHERE v.doctor_id = ? AND v.work_date BETWEEN ? AND ?", (doc_id, start_date, end_date)).fetchone()['total']
            nursing_count = db.execute("SELECT COUNT(*) as cnt FROM injections WHERE doctor_id = ? AND work_date BETWEEN ? AND ?", (doc_id, start_date, end_date)).fetchone()['cnt']
            nursing_revenue = db.execute("SELECT COALESCE(SUM(total_price), 0) as total FROM injections inj JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed' WHERE inj.doctor_id = ? AND inj.work_date BETWEEN ? AND ?", (doc_id, start_date, end_date)).fetchone()['total']
            proc_count = db.execute("SELECT COUNT(*) as cnt FROM procedures WHERE doctor_id = ? AND performer_type = 'doctor' AND work_date BETWEEN ? AND ?", (doc_id, start_date, end_date)).fetchone()['cnt']
            proc_revenue = db.execute("SELECT COALESCE(SUM(price), 0) as total FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed' WHERE pr.doctor_id = ? AND pr.performer_type = 'doctor' AND pr.work_date BETWEEN ? AND ?", (doc_id, start_date, end_date)).fetchone()['total']
            results.append([str(doc_id), doc['full_name'], 'پزشک', 0, visits_count, nursing_count, proc_count, 0, visits_revenue + nursing_revenue + proc_revenue])

    # پرستاران
    if not role_filter or role_filter == 'nurse':
        nurses = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type='nurse' ORDER BY full_name").fetchall()
        for nurse in nurses:
            nurse_id = nurse['id']
            if user_filter and str(nurse_id) != user_filter:
                continue
            nursing_count = db.execute("SELECT COUNT(*) as cnt FROM injections WHERE nurse_id = ? AND work_date BETWEEN ? AND ?", (nurse_id, start_date, end_date)).fetchone()['cnt']
            nursing_revenue = db.execute("SELECT COALESCE(SUM(total_price), 0) as total FROM injections inj JOIN invoices i ON i.id = inj.invoice_id AND i.status = 'closed' WHERE inj.nurse_id = ? AND inj.work_date BETWEEN ? AND ?", (nurse_id, start_date, end_date)).fetchone()['total']
            proc_count = db.execute("SELECT COUNT(*) as cnt FROM procedures WHERE nurse_id = ? AND performer_type = 'nurse' AND work_date BETWEEN ? AND ?", (nurse_id, start_date, end_date)).fetchone()['cnt']
            proc_revenue = db.execute("SELECT COALESCE(SUM(price), 0) as total FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id AND i.status = 'closed' WHERE pr.nurse_id = ? AND pr.performer_type = 'nurse' AND pr.work_date BETWEEN ? AND ?", (nurse_id, start_date, end_date)).fetchone()['total']
            results.append([str(nurse_id), nurse['full_name'], 'پرستار', 0, 0, nursing_count, proc_count, 0, nursing_revenue + proc_revenue])

    headers = ['کاربر', 'نام کامل', 'نقش', 'فاکتورهای بازشده', 'ویزیت‌ها', 'خدمات پرستاری', 'کارهای عملی', 'مصرفی‌ها', 'درآمد کل (تومان)']
    return make_csv_response(results, headers, 'users_report.csv')


@bp.route('/export/visits/csv')
@login_required
def export_visits_csv():
    """خروجی CSV گزارش ویزیت‌ها."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403

    db = get_db()
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    doctor_id = request.args.get('doctor_id', type=int)
    insurance_type = request.args.get('insurance_type', '').strip() or None
    reception_user = request.args.get('reception_user', '').strip() or None
    shift = request.args.get('shift', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {'date_from': start_dt.strftime('%Y-%m-%d'), 'date_to': end_dt.strftime('%Y-%m-%d')}
    where_clauses = ["v.work_date BETWEEN :date_from AND :date_to"]
    if doctor_id:
        where_clauses.append("v.doctor_id = :doctor_id")
        params['doctor_id'] = doctor_id
    if insurance_type:
        where_clauses.append("v.insurance_type = :insurance_type")
        params['insurance_type'] = insurance_type
    if reception_user:
        where_clauses.append("inv.opened_by = :reception_user")
        params['reception_user'] = reception_user
    if shift:
        where_clauses.append("v.shift = :shift")
        params['shift'] = shift

    where_sql = " AND ".join(where_clauses)
    visits = db.execute(f'''
        SELECT v.visit_date, p.full_name AS patient_name, v.doctor_name, v.insurance_type, v.supplementary_insurance, v.shift, v.price AS total_amount, v.notes
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        JOIN invoices inv ON inv.id = v.invoice_id
        WHERE {where_sql}
        ORDER BY v.visit_date DESC
    ''', params).fetchall()

    data = []
    for v in visits:
        data.append([v['visit_date'], v['patient_name'], v['doctor_name'] or '', v['insurance_type'] or '', v['supplementary_insurance'] or '', v['shift'] or '', v['total_amount'] or 0, v['notes'] or ''])

    headers = ['تاریخ ویزیت', 'بیمار', 'پزشک', 'بیمه', 'بیمه تکمیلی', 'شیفت', 'مبلغ (تومان)', 'یادداشت']
    return make_csv_response(data, headers, 'visits_report.csv')


@bp.route('/export/nursing/csv')
@login_required
def export_nursing_csv():
    """خروجی CSV گزارش خدمات پرستاری."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403

    db = get_db()
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    doctor_id = request.args.get('doctor_id', type=int)
    nurse_id = request.args.get('nurse_id', type=int)
    service_name = request.args.get('service_name', '').strip() or None
    shift = request.args.get('shift', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {'date_from': start_dt.strftime('%Y-%m-%d'), 'date_to': end_dt.strftime('%Y-%m-%d')}
    where_clauses = ["i.work_date BETWEEN :date_from AND :date_to"]
    if doctor_id:
        where_clauses.append("i.doctor_id = :doctor_id")
        params['doctor_id'] = doctor_id
    if nurse_id:
        where_clauses.append("i.nurse_id = :nurse_id")
        params['nurse_id'] = nurse_id
    if service_name:
        where_clauses.append("i.injection_type = :stype")
        params['stype'] = service_name
    if shift:
        where_clauses.append("i.shift = :shift")
        params['shift'] = shift

    where_sql = " AND ".join(where_clauses)
    rows = db.execute(f'''
        SELECT i.injection_date, p.full_name AS patient_name, i.injection_type, i.count, i.unit_price, i.total_price, doc.full_name AS doctor_name, nurse.full_name AS nurse_name, i.shift, i.notes
        FROM injections i
        JOIN patients p ON p.id = i.patient_id
        JOIN invoices inv ON inv.id = i.invoice_id AND inv.status = 'closed'
        LEFT JOIN medical_staff doc ON doc.id = i.doctor_id
        LEFT JOIN medical_staff nurse ON nurse.id = i.nurse_id
        WHERE {where_sql}
        ORDER BY i.injection_date DESC
    ''', params).fetchall()

    data = []
    for r in rows:
        data.append([r['injection_date'], r['patient_name'], r['injection_type'] or '', r['count'] or 1, r['unit_price'] or 0, r['total_price'] or 0, r['doctor_name'] or '', r['nurse_name'] or '', r['shift'] or '', r['notes'] or ''])

    headers = ['تاریخ', 'بیمار', 'نوع خدمت', 'تعداد', 'مبلغ واحد', 'مبلغ کل', 'پزشک', 'پرستار', 'شیفت', 'یادداشت']
    return make_csv_response(data, headers, 'nursing_report.csv')


@bp.route('/export/procedures/csv')
@login_required
def export_procedures_csv():
    """خروجی CSV گزارش کارهای عملی."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403

    db = get_db()
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    doctor_id = request.args.get('doctor_id', type=int)
    nurse_id = request.args.get('nurse_id', type=int)
    performer_type = request.args.get('performer_type', '').strip() or None
    procedure_type = request.args.get('procedure_type', '').strip() or None
    shift = request.args.get('shift', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {'date_from': start_dt.strftime('%Y-%m-%d'), 'date_to': end_dt.strftime('%Y-%m-%d')}
    where_clauses = ["pr.work_date BETWEEN :date_from AND :date_to"]
    if doctor_id:
        where_clauses.append("pr.doctor_id = :doctor_id")
        params['doctor_id'] = doctor_id
    if nurse_id:
        where_clauses.append("pr.nurse_id = :nurse_id")
        params['nurse_id'] = nurse_id
    if performer_type:
        where_clauses.append("pr.performer_type = :ptype")
        params['ptype'] = performer_type
    if procedure_type:
        where_clauses.append("pr.procedure_type = :prtype")
        params['prtype'] = procedure_type
    if shift:
        where_clauses.append("pr.shift = :shift")
        params['shift'] = shift

    where_sql = " AND ".join(where_clauses)
    rows = db.execute(f'''
        SELECT pr.procedure_date, p.full_name AS patient_name, pr.procedure_type, pr.performer_type, doc.full_name AS doctor_name, nurse.full_name AS nurse_name, pr.shift, pr.price, pr.notes
        FROM procedures pr
        JOIN patients p ON p.id = pr.patient_id
        JOIN invoices inv ON inv.id = pr.invoice_id AND inv.status = 'closed'
        LEFT JOIN medical_staff doc ON doc.id = pr.doctor_id
        LEFT JOIN medical_staff nurse ON nurse.id = pr.nurse_id
        WHERE {where_sql}
        ORDER BY pr.procedure_date DESC
    ''', params).fetchall()

    data = []
    for r in rows:
        # تبدیل performer_type به فارسی
        performer_fa = 'پزشک' if r['performer_type'] == 'doctor' else ('پرستار' if r['performer_type'] == 'nurse' else '')
        data.append([r['procedure_date'], r['patient_name'], r['procedure_type'] or '', performer_fa, r['doctor_name'] or '', r['nurse_name'] or '', r['shift'] or '', r['price'] or 0, r['notes'] or ''])

    headers = ['تاریخ', 'بیمار', 'نوع کار', 'انجام‌دهنده', 'پزشک', 'پرستار', 'شیفت', 'مبلغ (تومان)', 'یادداشت']
    return make_csv_response(data, headers, 'procedures_report.csv')


# ===================== گزارش مصرفی‌ها =====================

@bp.route('/reports/consumables')
@login_required
def consumables_report():
    """گزارش مدیریتی مصرفی‌ها (دارو و عمومی) با فیلترهای غنی."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))

    db = get_db()

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    item_name = request.args.get('item_name', '').strip() or None
    category = request.args.get('category', '').strip() or None  # drug/supply
    brought_by_patient = request.args.get('brought_by_patient', '').strip() or None
    shift = request.args.get('shift', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {
        'date_from': start_dt.strftime('%Y-%m-%d'),
        'date_to': end_dt.strftime('%Y-%m-%d')
    }
    # Exclude patient-provided consumables from manager reports, but include those
    # explicitly marked as exceptions (is_exception=1)
    # Only show consumables that were provided by the center and not exceptions by default
    where_clauses = ["cl.work_date BETWEEN :date_from AND :date_to", "(COALESCE(cl.patient_provided,0) = 0 AND COALESCE(cl.is_exception,0) = 0)"]

    if item_name:
        where_clauses.append("cl.item_name LIKE :item_name")
        params['item_name'] = f"%{item_name}%"
    if category:
        where_clauses.append("cl.category = :category")
        params['category'] = category
    if brought_by_patient:
        where_clauses.append("cl.patient_provided = :bbp")
        params['bbp'] = int(brought_by_patient)
    if shift:
        where_clauses.append("cl.shift = :shift")
        params['shift'] = shift

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(f'''
        SELECT cl.id, cl.usage_date, cl.item_name, cl.category, cl.quantity, cl.unit_price, cl.total_cost,
               cl.patient_provided, cl.shift, cl.notes,
               p.full_name AS patient_name
        FROM consumables_ledger cl
        JOIN patients p ON p.id = cl.patient_id
        JOIN invoices inv ON inv.id = cl.invoice_id AND inv.status = 'closed'
        WHERE {where_sql}
        ORDER BY cl.usage_date DESC
    ''', params).fetchall()

    consumables = [dict(r) for r in rows]
    total_count = len(consumables)
    total_amount = sum(r['total_cost'] or 0 for r in consumables)
    drugs_count = sum(1 for r in consumables if r['category'] == 'drug')
    supplies_count = sum(1 for r in consumables if r['category'] == 'supply')

    # لیست آیتم‌ها برای فیلتر
    item_names = db.execute("SELECT DISTINCT item_name FROM consumables_ledger WHERE (COALESCE(patient_provided,0) = 0 AND COALESCE(is_exception,0) = 0) ORDER BY item_name").fetchall()

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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }

    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }

    return render_template(
        'manager/reports_consumables.html',
        consumables=consumables,
        total_count=total_count,
        total_amount=total_amount,
        drugs_count=drugs_count,
        supplies_count=supplies_count,
        item_names=item_names,
        jalali_ranges=ranges,
        active_filters={
            'from': date_from,
            'to': date_to,
            'item_name': item_name,
            'category': category,
            'brought_by_patient': brought_by_patient,
            'shift': shift,
        }
    )


@bp.route('/export/consumables/csv')
@login_required
def export_consumables_csv():
    """خروجی CSV گزارش مصرفی‌ها."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403

    db = get_db()
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    item_name = request.args.get('item_name', '').strip() or None
    category = request.args.get('category', '').strip() or None
    brought_by_patient = request.args.get('brought_by_patient', '').strip() or None
    shift = request.args.get('shift', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=6)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    params = {'date_from': start_dt.strftime('%Y-%m-%d'), 'date_to': end_dt.strftime('%Y-%m-%d')}
    # Exclude patient-provided consumables from manager CSV export, but include exceptions
    where_clauses = ["cl.work_date BETWEEN :date_from AND :date_to", "(COALESCE(cl.patient_provided,0) = 0 OR COALESCE(cl.is_exception,0) = 1)"]

    if item_name:
        where_clauses.append("cl.item_name LIKE :item_name")
        params['item_name'] = f"%{item_name}%"
    if category:
        where_clauses.append("cl.category = :category")
        params['category'] = category
    if brought_by_patient:
        where_clauses.append("cl.patient_provided = :bbp")
        params['bbp'] = int(brought_by_patient)
    if shift:
        where_clauses.append("cl.shift = :shift")
        params['shift'] = shift

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(f'''
        SELECT cl.usage_date, p.full_name AS patient_name, cl.item_name, cl.category, cl.quantity, cl.unit_price, cl.total_cost, cl.patient_provided, cl.shift, cl.notes
        FROM consumables_ledger cl
        JOIN patients p ON p.id = cl.patient_id
        JOIN invoices inv ON inv.id = cl.invoice_id AND inv.status = 'closed'
        WHERE {where_sql}
        ORDER BY cl.usage_date DESC
    ''', params).fetchall()

    data = []
    for r in rows:
        cat_fa = 'دارو' if r['category'] == 'drug' else 'عمومی'
        bbp_fa = 'بله' if r['patient_provided'] else 'خیر'
        data.append([r['usage_date'], r['patient_name'], r['item_name'], cat_fa, r['quantity'] or 1, r['unit_price'] or 0, r['total_cost'] or 0, bbp_fa, r['shift'] or '', r['notes'] or ''])

    headers = ['تاریخ', 'بیمار', 'نام قلم', 'دسته', 'تعداد', 'مبلغ واحد', 'مبلغ کل', 'آورده بیمار', 'شیفت', 'یادداشت']
    return make_csv_response(data, headers, 'consumables_report.csv')


# ===================== گزارش بیماران =====================

@bp.route('/reports/patients')
@login_required
def patients_report():
    """گزارش مدیریتی بیماران با آمار مراجعات و پرداخت‌ها."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))

    db = get_db()

    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    search_name = request.args.get('search_name', '').strip() or None
    insurance_type = request.args.get('insurance_type', '').strip() or None
    gender = request.args.get('gender', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=29)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    start_date = start_dt.strftime('%Y-%m-%d')
    end_date = end_dt.strftime('%Y-%m-%d')

    # گرفتن بیماران که در بازه زمانی فاکتور داشتند
    patient_ids_sql = """
        SELECT DISTINCT patient_id FROM invoices
        WHERE work_date BETWEEN ? AND ?
    """
    patient_ids = [r['patient_id'] for r in db.execute(patient_ids_sql, (start_date, end_date)).fetchall()]

    results = []
    for pid in patient_ids:
        patient = db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
        if not patient:
            continue

        # ساخت نام کامل
        full_name = f"{patient['name']} {patient['family_name']}"

        # فیلترها
        if search_name and search_name.lower() not in full_name.lower():
            continue

        # آمار مراجعات
        invoices_count = db.execute("""
            SELECT COUNT(*) as cnt FROM invoices
            WHERE patient_id = ? AND work_date BETWEEN ? AND ?
        """, (pid, start_date, end_date)).fetchone()['cnt']

        # جمع پرداخت‌ها - بدون مصرفی
        # Revenue = visits + injections + procedures (NOT consumables)
        v_paid = db.execute("""
            SELECT COALESCE(SUM(v.price), 0) as total FROM visits v
            JOIN invoices i ON i.id = v.invoice_id
            WHERE i.patient_id = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'
        """, (pid, start_date, end_date)).fetchone()['total']
        inj_paid = db.execute("""
            SELECT COALESCE(SUM(inj.total_price), 0) as total FROM injections inj
            JOIN invoices i ON i.id = inj.invoice_id
            WHERE i.patient_id = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'
        """, (pid, start_date, end_date)).fetchone()['total']
        pr_paid = db.execute("""
            SELECT COALESCE(SUM(pr.price), 0) as total FROM procedures pr
            JOIN invoices i ON i.id = pr.invoice_id
            WHERE i.patient_id = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'
        """, (pid, start_date, end_date)).fetchone()['total']
        total_paid = v_paid + inj_paid + pr_paid

        # ویزیت‌ها
        visits_count = db.execute("""
            SELECT COUNT(*) as cnt FROM visits v
            JOIN invoices i ON i.id = v.invoice_id
            WHERE i.patient_id = ? AND v.work_date BETWEEN ? AND ?
        """, (pid, start_date, end_date)).fetchone()['cnt']

        # آخرین مراجعه
        last_visit = db.execute("""
            SELECT MAX(opened_at) as last_date FROM invoices
            WHERE patient_id = ? AND work_date BETWEEN ? AND ?
        """, (pid, start_date, end_date)).fetchone()['last_date']

        # بیمه (از آخرین ویزیت)
        last_ins = db.execute("""
            SELECT v.insurance_type FROM visits v
            JOIN invoices i ON i.id = v.invoice_id
            WHERE i.patient_id = ?
            ORDER BY v.visit_date DESC LIMIT 1
        """, (pid,)).fetchone()
        ins_type = last_ins['insurance_type'] if last_ins else None

        if insurance_type and ins_type != insurance_type:
            continue

        results.append({
            'id': pid,
            'full_name': full_name,
            'national_id': patient['national_id'] if patient['national_id'] else '',
            'phone': patient['phone_number'] if patient['phone_number'] else '',
            'insurance_type': ins_type,
            'invoices_count': invoices_count,
            'visits_count': visits_count,
            'total_paid': total_paid,
            'last_visit': last_visit,
        })

    # مرتب‌سازی بر اساس تعداد مراجعات
    results.sort(key=lambda x: x['invoices_count'], reverse=True)

    total_patients = len(results)
    total_revenue = sum(r['total_paid'] for r in results)

    # لیست بیمه‌ها
    insurances = db.execute("SELECT DISTINCT insurance_type FROM visits WHERE insurance_type IS NOT NULL ORDER BY insurance_type").fetchall()

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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }

    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }

    return render_template(
        'manager/reports_patients.html',
        patients=results,
        total_patients=total_patients,
        total_revenue=total_revenue,
        insurances=insurances,
        jalali_ranges=ranges,
        active_filters={
            'from': date_from,
            'to': date_to,
            'search_name': search_name,
            'insurance_type': insurance_type,
            'gender': gender,
        }
    )


@bp.route('/export/patients/csv')
@login_required
def export_patients_csv():
    """خروجی CSV گزارش بیماران."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403

    db = get_db()
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    search_name = request.args.get('search_name', '').strip() or None
    insurance_type = request.args.get('insurance_type', '').strip() or None
    gender = request.args.get('gender', '').strip() or None

    def jalali_to_start_end(jalali_str, is_start=True):
        if not jalali_str:
            return None
        try:
            parts = jalali_str.split('/')
            jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            gd = jd.togregorian()
            if is_start:
                return datetime(gd.year, gd.month, gd.day, 0, 0, 0)
            else:
                return datetime(gd.year, gd.month, gd.day, 23, 59, 59)
        except Exception:
            return None

    start_dt = jalali_to_start_end(date_from, True)
    end_dt = jalali_to_start_end(date_to, False)
    if not start_dt or not end_dt:
        end_dt = iran_now()
        start_dt = end_dt - timedelta(days=29)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    start_date = start_dt.strftime('%Y-%m-%d')
    end_date = end_dt.strftime('%Y-%m-%d')

    patient_ids = [r['patient_id'] for r in db.execute("SELECT DISTINCT patient_id FROM invoices WHERE work_date BETWEEN ? AND ?", (start_date, end_date)).fetchall()]

    data = []
    for pid in patient_ids:
        patient = db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
        if not patient:
            continue
        full_name = f"{patient['name']} {patient['family_name']}"
        if search_name and search_name.lower() not in full_name.lower():
            continue

        invoices_count = db.execute("SELECT COUNT(*) as cnt FROM invoices WHERE patient_id = ? AND work_date BETWEEN ? AND ?", (pid, start_date, end_date)).fetchone()['cnt']
        # Revenue = visits + injections + procedures (NOT consumables)
        v_paid = db.execute("SELECT COALESCE(SUM(v.price), 0) as total FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.patient_id = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'", (pid, start_date, end_date)).fetchone()['total']
        inj_paid = db.execute("SELECT COALESCE(SUM(inj.total_price), 0) as total FROM injections inj JOIN invoices i ON i.id = inj.invoice_id WHERE i.patient_id = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'", (pid, start_date, end_date)).fetchone()['total']
        pr_paid = db.execute("SELECT COALESCE(SUM(pr.price), 0) as total FROM procedures pr JOIN invoices i ON i.id = pr.invoice_id WHERE i.patient_id = ? AND i.work_date BETWEEN ? AND ? AND i.status = 'closed'", (pid, start_date, end_date)).fetchone()['total']
        total_paid = v_paid + inj_paid + pr_paid
        visits_count = db.execute("SELECT COUNT(*) as cnt FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.patient_id = ? AND v.work_date BETWEEN ? AND ?", (pid, start_date, end_date)).fetchone()['cnt']
        last_visit = db.execute("SELECT MAX(opened_at) as last_date FROM invoices WHERE patient_id = ? AND work_date BETWEEN ? AND ?", (pid, start_date, end_date)).fetchone()['last_date']
        last_ins = db.execute("SELECT v.insurance_type FROM visits v JOIN invoices i ON i.id = v.invoice_id WHERE i.patient_id = ? ORDER BY v.visit_date DESC LIMIT 1", (pid,)).fetchone()
        ins_type = last_ins['insurance_type'] if last_ins else ''
        if insurance_type and ins_type != insurance_type:
            continue

        phone = patient['phone_number'] if patient['phone_number'] else ''
        national_id = patient['national_id'] if patient['national_id'] else ''
        data.append([full_name, national_id, phone, ins_type or '', invoices_count, visits_count, total_paid, last_visit or ''])

    data.sort(key=lambda x: x[4], reverse=True)
    headers = ['نام بیمار', 'کد ملی', 'تلفن', 'بیمه', 'تعداد فاکتور', 'تعداد ویزیت', 'جمع پرداخت (تومان)', 'آخرین مراجعه']
    return make_csv_response(data, headers, 'patients_report.csv')


# ==================== معوقات بیمه ====================

@bp.route('/insurance_arrears')
@login_required
def insurance_arrears():
    """صفحه معوقات بیمه - نمایش مطالبات از بیمه‌ها"""
    if g.user['role'] != 'manager':
        flash('دسترسی محدود', 'error')
        return redirect(url_for('reception.index'))
    
    db = get_db()
    
    # اطمینان از وجود ستون‌های جدید
    try:
        db.execute("ALTER TABLE visit_tariffs ADD COLUMN nursing_covers INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE visit_tariffs ADD COLUMN is_base_tariff INTEGER DEFAULT 0")
    except Exception:
        pass
    
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
            'from': {'y': j_start[0], 'm': j_start[1], 'd': j_start[2]},
            'to': {'y': j_end[0], 'm': j_end[1], 'd': j_end[2]}
        }
    
    ranges = {
        'today': {'y': j_today[0], 'm': j_today[1], 'd': j_today[2]},
        'r7': jalali_range(7),
        'r30': jalali_range(30),
        'r90': jalali_range(90),
    }
    
    # دریافت فیلترها - تاریخ شمسی
    date_from_jalali = request.args.get('from', '')
    date_to_jalali = request.args.get('to', '')
    insurance_filter = request.args.get('insurance', '')
    
    # تبدیل تاریخ شمسی به میلادی
    date_from_gregorian = ''
    date_to_gregorian = ''
    
    if date_from_jalali:
        try:
            parts = date_from_jalali.split('/')
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian
            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_from_gregorian = gd.strftime('%Y-%m-%d')
        except Exception:
            pass
    
    if date_to_jalali:
        try:
            parts = date_to_jalali.split('/')
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian
            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_to_gregorian = gd.strftime('%Y-%m-%d')
        except Exception:
            pass
    
    # دریافت تعرفه پایه (آزاد)
    base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE is_base_tariff = 1").fetchone()
    if not base_tariff:
        # اگر تعرفه پایه تعریف نشده، از آزاد استفاده کن
        base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE insurance_type = 'آزاد'").fetchone()
    base_visit_price = base_tariff['tariff_price'] if base_tariff else 0
    
    # دریافت تمام تعرفه‌های بیمه و بررسی پوشش پرستاری
    insurance_tariffs = {}
    tariffs_rows = db.execute("SELECT * FROM visit_tariffs WHERE insurance_type != 'آزاد' AND (is_base_tariff = 0 OR is_base_tariff IS NULL)").fetchall()
    for t in tariffs_rows:
        # بررسی ستون nursing_covers
        nursing_covers = False
        try:
            nursing_covers = bool(t['nursing_covers'])
        except Exception:
            pass
        insurance_tariffs[t['insurance_type']] = {
            'visit_tariff': t['tariff_price'],
            'nursing_covers': nursing_covers  # آیا بیمه پرستاری را پوشش می‌دهد؟
        }
    
    # دریافت تعرفه‌های بیمه تکمیلی
    supplementary_tariffs = {}
    supp_rows = db.execute("SELECT * FROM visit_tariffs WHERE is_supplementary = 1 AND is_active = 1").fetchall()
    for t in supp_rows:
        row = dict(t)
        supplementary_tariffs[row['insurance_type']] = {
            'visit_tariff': row.get('tariff_price'),
            'nursing_covers': bool(row.get('nursing_covers', 0))
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
        ins_type = v['insurance_type']
        supp_ins = v['supplementary_insurance']
        
        # سهم بیمار = تعرفه ویزیت برای آن بیمه
        ins_tariff = insurance_tariffs.get(ins_type, {})
        patient_share = ins_tariff.get('visit_tariff', 0) or 0  # تعرفه بیمه = سهم بیمار
        base_insurance_debt = base_visit_price - patient_share  # معوقه از بیمه پایه
        
        # معوقه بیمه پایه
        if base_insurance_debt > 0:
            visit_arrears.append({
                'id': v['id'],
                'date': v['visit_date'],
                'type': 'visit',
                'type_fa': 'ویزیت',
                'insurance_type': ins_type,
                'patient_name': v['patient_name'],
                'national_id': v['national_id'],
                'invoice_id': v['invoice_id'],
                'base_price': base_visit_price,
                'patient_paid': patient_share,
                'insurance_debt': base_insurance_debt
            })
        
        # معوقه بیمه تکمیلی: اگر بیمه تکمیلی دارد، سهم بیمار از بیمه پایه را تکمیلی باید بپردازد
        if supp_ins and supp_ins in supplementary_tariffs and patient_share > 0:
            # تعرفه بیمه تکمیلی (سهم بیمار با تکمیلی) - معمولاً 0 یا خیلی کم
            supp_tariff = supplementary_tariffs.get(supp_ins, {})
            final_patient_share = supp_tariff.get('visit_tariff', 0) or 0
            supp_debt = patient_share - final_patient_share  # مقداری که تکمیلی باید بپردازد
            
            if supp_debt > 0:
                supplementary_arrears.append({
                    'id': v['id'],
                    'date': v['visit_date'],
                    'type': 'visit',
                    'type_fa': 'ویزیت',
                    'insurance_type': supp_ins,  # نام بیمه تکمیلی
                    'base_insurance': ins_type,  # نام بیمه پایه
                    'patient_name': v['patient_name'],
                    'national_id': v['national_id'],
                    'invoice_id': v['invoice_id'],
                    'base_price': patient_share,  # سهم بیمار از بیمه پایه
                    'patient_paid': final_patient_share,  # سهم نهایی بیمار با تکمیلی
                    'insurance_debt': supp_debt  # معوقه از بیمه تکمیلی
                })
    
    # محاسبه معوقات برای خدمات پرستاری
    # فقط بیمه‌هایی که پرستاری را پوشش می‌دهند (nursing_covers = true)
    nursing_arrears = []

    for i in injections:
        ins_type = i['insurance_type']
        ins_tariff = insurance_tariffs.get(ins_type, {})
        # Check if this specific nursing service is excluded for this insurance (insurance_nursing_exclusions)
        excluded = False
        svc_id = None
        try:
            try:
                keys = list(i.keys())
            except Exception:
                keys = []
            if 'service_id' in keys and i['service_id'] is not None:
                svc_id = int(i['service_id'])
            else:
                svc_id = None
        except Exception:
            svc_id = None
        if svc_id:
            row = db.execute("SELECT 1 FROM insurance_nursing_exclusions WHERE insurance_type = ? AND nursing_service_id = ? LIMIT 1", (ins_type, svc_id)).fetchone()
            if row:
                excluded = True

        include = bool(ins_tariff.get('nursing_covers', False) and not excluded)

        # (diagnostic logging removed)

        if include:
            service_price = i['total_price'] or 0
            nursing_arrears.append({
                'id': i['id'],
                'date': i['injection_date'],
                'type': 'injection',
                'type_fa': i['injection_type'],
                'insurance_type': ins_type,
                'patient_name': i['patient_name'],
                'national_id': i['national_id'],
                'invoice_id': i['invoice_id'],
                'service_price': service_price,  # مبلغ خدمت که بیمه باید پرداخت کند
            })
    
    # خلاصه معوقات به تفکیک بیمه (شامل بیمه‌های پایه و تکمیلی)
    summary_by_insurance = {}
    
    # معوقات بیمه پایه
    for arr in visit_arrears:
        ins = arr['insurance_type']
        if ins not in summary_by_insurance:
            summary_by_insurance[ins] = {
                'visit_count': 0,
                'visit_debt': 0,
                'nursing_count': 0,
                'nursing_debt': 0,
                'supplementary_count': 0,
                'supplementary_debt': 0,
                'total_debt': 0,
                'is_supplementary': False
            }
        summary_by_insurance[ins]['visit_count'] += 1
        summary_by_insurance[ins]['visit_debt'] += arr['insurance_debt']
        summary_by_insurance[ins]['total_debt'] += arr['insurance_debt']
    
    # معوقات بیمه تکمیلی
    for arr in supplementary_arrears:
        ins = arr['insurance_type']
        if ins not in summary_by_insurance:
            summary_by_insurance[ins] = {
                'visit_count': 0,
                'visit_debt': 0,
                'nursing_count': 0,
                'nursing_debt': 0,
                'supplementary_count': 0,
                'supplementary_debt': 0,
                'total_debt': 0,
                'is_supplementary': True
            }
        summary_by_insurance[ins]['supplementary_count'] += 1
        summary_by_insurance[ins]['supplementary_debt'] += arr['insurance_debt']
        summary_by_insurance[ins]['total_debt'] += arr['insurance_debt']
        summary_by_insurance[ins]['is_supplementary'] = True
    
    for arr in nursing_arrears:
        ins = arr['insurance_type']
        if ins not in summary_by_insurance:
            summary_by_insurance[ins] = {
                'visit_count': 0,
                'visit_debt': 0,
                'nursing_count': 0,
                'nursing_debt': 0,
                'supplementary_count': 0,
                'supplementary_debt': 0,
                'total_debt': 0,
                'is_supplementary': False
            }
        summary_by_insurance[ins]['nursing_count'] += 1
        summary_by_insurance[ins]['nursing_debt'] += arr['service_price']
        summary_by_insurance[ins]['total_debt'] += arr['service_price']
    
    # لیست بیمه‌ها برای فیلتر (شامل پایه و تکمیلی)
    all_insurances = db.execute(
        "SELECT DISTINCT insurance_type FROM visit_tariffs WHERE insurance_type != 'آزاد' AND (is_base_tariff = 0 OR is_base_tariff IS NULL) ORDER BY insurance_type"
    ).fetchall()
    
    # جمع کل معوقات
    total_visit_debt = sum(a['insurance_debt'] for a in visit_arrears)
    total_supplementary_debt = sum(a['insurance_debt'] for a in supplementary_arrears)
    total_nursing_debt = sum(a['service_price'] for a in nursing_arrears)
    total_debt = total_visit_debt + total_supplementary_debt + total_nursing_debt
    # Diagnostic: return raw data when requested by manager for debugging
    try:
        if request.args.get('_diag') and g.user and (g.user['role'] if isinstance(g.user, dict) or hasattr(g.user, '__getitem__') else getattr(g.user, 'role', None)) == 'manager':
            from flask import jsonify
            return jsonify({
                'visit_arrears': visit_arrears,
                'supplementary_arrears': supplementary_arrears,
                'nursing_arrears': nursing_arrears,
                'summary_by_insurance': summary_by_insurance,
                'total_visit_debt': total_visit_debt,
                'total_supplementary_debt': total_supplementary_debt,
                'total_nursing_debt': total_nursing_debt,
                'total_debt': total_debt
            })
    except Exception:
        pass
    
    return render_template(
        'manager/insurance_arrears.html',
        visit_arrears=visit_arrears,
        supplementary_arrears=supplementary_arrears,
        nursing_arrears=nursing_arrears,
        summary_by_insurance=summary_by_insurance,
        all_insurances=[i['insurance_type'] for i in all_insurances],
        base_visit_price=base_visit_price,
        total_visit_debt=total_visit_debt,
        total_supplementary_debt=total_supplementary_debt,
        total_nursing_debt=total_nursing_debt,
        total_debt=total_debt,
        active_filters={'from': date_from_jalali, 'to': date_to_jalali},
        insurance_filter=insurance_filter,
        jalali_ranges=ranges
    )


@bp.route('/insurance_arrears/export')
@login_required
def export_insurance_arrears():
    """خروجی CSV معوقات بیمه"""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'دسترسی محدود'}), 403
    
    db = get_db()
    
    # دریافت فیلترها - تاریخ شمسی
    date_from_jalali = request.args.get('from', '')
    date_to_jalali = request.args.get('to', '')
    insurance_filter = request.args.get('insurance', '')
    
    # تبدیل تاریخ شمسی به میلادی
    date_from_gregorian = ''
    date_to_gregorian = ''
    
    if date_from_jalali:
        try:
            parts = date_from_jalali.split('/')
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian
            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_from_gregorian = gd.strftime('%Y-%m-%d')
        except Exception:
            pass
    
    if date_to_jalali:
        try:
            parts = date_to_jalali.split('/')
            jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
            from src.common.jalali import Persian
            gd = Persian(jy, jm, jd).gregorian_datetime()
            date_to_gregorian = gd.strftime('%Y-%m-%d')
        except Exception:
            pass
    
    base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE is_base_tariff = 1").fetchone()
    if not base_tariff:
        base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE insurance_type = 'آزاد'").fetchone()
    base_visit_price = base_tariff['tariff_price'] if base_tariff else 0
    
    # دریافت تعرفه‌های بیمه با پوشش پرستاری
    insurance_tariffs = {}
    tariffs_rows = db.execute("SELECT * FROM visit_tariffs WHERE insurance_type != 'آزاد' AND (is_base_tariff = 0 OR is_base_tariff IS NULL)").fetchall()
    for t in tariffs_rows:
        nursing_covers = False
        try:
            nursing_covers = bool(t['nursing_covers'])
        except Exception:
            pass
        insurance_tariffs[t['insurance_type']] = {'nursing_covers': nursing_covers}
    
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
        ins_type = v['insurance_type']
        ins_tariff = insurance_tariffs.get(ins_type, {})
        patient_paid = ins_tariff.get('visit_tariff', 0) or 0  # تعرفه بیمه = سهم بیمار
        insurance_debt = base_visit_price - patient_paid
        if insurance_debt > 0:
            data.append([
                v['visit_date'],
                v['patient_name'],
                v['national_id'] or '',
                v['insurance_type'],
                'ویزیت',
                base_visit_price,
                patient_paid,
                insurance_debt
            ])
    
    for i in injections:
        ins_type = i['insurance_type']
        ins_tariff = insurance_tariffs.get(ins_type, {})
        # Skip if insurance does not cover nursing
        if not ins_tariff.get('nursing_covers', False):
            continue
        # If this injection's service is excluded for this insurance, skip it
        svc_id = i.get('service_id')
        excluded = False
        try:
            if svc_id is not None:
                row = db.execute("SELECT 1 FROM insurance_nursing_exclusions WHERE insurance_type = ? AND nursing_service_id = ? LIMIT 1", (ins_type, svc_id)).fetchone()
                if row:
                    excluded = True
        except Exception:
            excluded = False
        if excluded:
            continue
        service_price = i['total_price'] or 0
        data.append([
            i['injection_date'],
            i['patient_name'],
            i['national_id'] or '',
            i['insurance_type'],
            i['injection_type'],
            '-',
            '0',
            service_price
        ])
    
    headers = ['تاریخ', 'نام بیمار', 'کد ملی', 'نوع بیمه', 'نوع خدمت', 'تعرفه پایه', 'پرداخت بیمار', 'معوقه بیمه']
    return make_csv_response(data, headers, 'insurance_arrears.csv')


# ==================== تعرفه‌ها ====================

@bp.route('/tariffs')
@login_required
def tariffs_index():
    """صفحه اصلی تعرفه‌ها - هاب"""
    if g.user['role'] != 'manager':
        flash('دسترسی محدود: فقط مدیر می‌تواند به این بخش دسترسی داشته باشد.', 'error')
        return redirect(url_for('reception.index'))
    
    return render_template('manager/tariffs.html')


@bp.route('/tariffs/nursing', methods=['GET', 'POST'])
@login_required
def nursing_tariffs():
    """تعرفه خدمات پرستاری"""
    if g.user['role'] != 'manager':
        flash('دسترسی محدود', 'error')
        return redirect(url_for('reception.index'))
    
    db = get_db()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            service_name = request.form.get('service_name', '').strip()
            unit_price = request.form.get('unit_price', '0')
            try:
                unit_price = float(unit_price.replace(',', ''))
            except Exception:
                unit_price = 0
            
            if service_name:
                db.execute(
                    "INSERT INTO nursing_services (service_name, unit_price, is_active) VALUES (?, ?, 1)",
                    (service_name, unit_price)
                )
                db.commit()
                flash(f'خدمت "{service_name}" با موفقیت اضافه شد.', 'success')
        
        elif action == 'update':
            service_id = request.form.get('service_id')
            service_name = request.form.get('service_name', '').strip()
            unit_price = request.form.get('unit_price', '0')
            is_active = request.form.get('is_active') == '1'
            try:
                unit_price = float(unit_price.replace(',', ''))
            except Exception:
                unit_price = 0
            
            if service_id and service_name:
                db.execute(
                    "UPDATE nursing_services SET service_name = ?, unit_price = ?, is_active = ? WHERE id = ?",
                    (service_name, unit_price, 1 if is_active else 0, service_id)
                )
                db.commit()
                flash('تعرفه با موفقیت بروزرسانی شد.', 'success')
        
        elif action == 'delete':
            service_id = request.form.get('service_id')
            if service_id:
                db.execute("DELETE FROM nursing_services WHERE id = ?", (service_id,))
                db.commit()
                flash('خدمت با موفقیت حذف شد.', 'success')
        
        return redirect(url_for('manager.nursing_tariffs'))
    
    services = db.execute(
        "SELECT * FROM nursing_services ORDER BY is_active DESC, service_name"
    ).fetchall()
    
    return render_template('manager/tariffs_nursing.html', services=services)


@bp.route('/tariffs/insurance', methods=['GET', 'POST'])
@login_required
def insurance_tariffs():
    """تعرفه بیمه‌ها و تعریف انواع بیمه"""
    if g.user['role'] != 'manager':
        flash('دسترسی محدود', 'error')
        return redirect(url_for('reception.index'))
    
    db = get_db()
    
    # اطمینان از وجود ستون‌های جدید
    try:
        db.execute("ALTER TABLE visit_tariffs ADD COLUMN nursing_covers INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE visit_tariffs ADD COLUMN is_base_tariff INTEGER DEFAULT 0")
    except Exception:
        pass
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            insurance_type = request.form.get('insurance_type', '').strip()
            tariff_price = request.form.get('tariff_price', '0')
            nursing_covers = request.form.get('nursing_covers') == '1'
            is_supplementary = request.form.get('is_supplementary') == '1'
            try:
                tariff_price = float(tariff_price.replace(',', ''))
            except Exception:
                tariff_price = 0
            
            if insurance_type:
                # چک کردن تکراری نبودن
                existing = db.execute(
                    "SELECT id FROM visit_tariffs WHERE insurance_type = ?",
                    (insurance_type,)
                ).fetchone()
                if existing:
                    flash(f'بیمه "{insurance_type}" قبلاً تعریف شده است.', 'error')
                else:
                    db.execute(
                        "INSERT INTO visit_tariffs (insurance_type, tariff_price, nursing_covers, is_active, is_supplementary) VALUES (?, ?, ?, 1, ?)",
                        (insurance_type, tariff_price, 1 if nursing_covers else 0, 1 if is_supplementary else 0)
                    )
                    db.commit()
                    flash(f'بیمه "{insurance_type}" با موفقیت اضافه شد.', 'success')
        
        elif action == 'update':
            tariff_id = request.form.get('tariff_id')
            insurance_type = request.form.get('insurance_type', '').strip()
            tariff_price = request.form.get('tariff_price', '0')
            nursing_covers = request.form.get('nursing_covers') == '1'
            is_active = request.form.get('is_active') == '1'
            is_supplementary = request.form.get('is_supplementary') == '1'
            try:
                tariff_price = float(tariff_price.replace(',', ''))
            except Exception:
                tariff_price = 0
            
            if tariff_id and insurance_type:
                db.execute(
                    "UPDATE visit_tariffs SET insurance_type = ?, tariff_price = ?, nursing_covers = ?, is_active = ?, is_supplementary = ? WHERE id = ?",
                    (insurance_type, tariff_price, 1 if nursing_covers else 0, 1 if is_active else 0, 1 if is_supplementary else 0, tariff_id)
                )
                db.commit()
                flash('تعرفه بیمه با موفقیت بروزرسانی شد.', 'success')
        
        elif action == 'delete':
            tariff_id = request.form.get('tariff_id')
            if tariff_id:
                # اجازه حذف تعرفه پایه را ندهیم
                base_check = db.execute("SELECT is_base_tariff FROM visit_tariffs WHERE id = ?", (tariff_id,)).fetchone()
                if base_check and base_check['is_base_tariff']:
                    flash('تعرفه پایه (آزاد) قابل حذف نیست.', 'error')
                else:
                    db.execute("DELETE FROM visit_tariffs WHERE id = ?", (tariff_id,))
                    db.commit()
                    flash('بیمه با موفقیت حذف شد.', 'success')
        
        elif action == 'set_base':
            # تنظیم تعرفه پایه (ویزیت آزاد)
            base_price = request.form.get('base_visit_price', '0')
            try:
                base_price = float(base_price.replace(',', ''))
            except Exception:
                base_price = 0
            
            # بررسی وجود تعرفه پایه
            existing_base = db.execute("SELECT id FROM visit_tariffs WHERE is_base_tariff = 1").fetchone()
            if existing_base:
                db.execute(
                    "UPDATE visit_tariffs SET tariff_price = ? WHERE is_base_tariff = 1",
                    (base_price,)
                )
            else:
                db.execute(
                    "INSERT INTO visit_tariffs (insurance_type, tariff_price, nursing_covers, is_active, is_supplementary, is_base_tariff) VALUES ('آزاد', ?, 0, 1, 0, 1)",
                    (base_price,)
                )
            db.commit()
            flash('تعرفه پایه (آزاد) با موفقیت ذخیره شد.', 'success')
        
        return redirect(url_for('manager.insurance_tariffs'))
    
    # دریافت تعرفه پایه
    base_tariff = db.execute("SELECT * FROM visit_tariffs WHERE is_base_tariff = 1").fetchone()
    
    # دریافت سایر تعرفه‌ها (بدون تعرفه پایه)
    tariffs = db.execute(
        "SELECT * FROM visit_tariffs WHERE is_base_tariff = 0 OR is_base_tariff IS NULL ORDER BY is_active DESC, insurance_type"
    ).fetchall()
    
    return render_template('manager/tariffs_insurance.html', tariffs=tariffs, base_tariff=base_tariff)


@bp.route('/insurance/<string:insurance_type>/nursing_exclusions', methods=['GET'])
@login_required
def get_nursing_exclusions(insurance_type: str):
    """Return list of nursing services with exclusion flag for a given insurance."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403
    db = get_db()
    # Load all nursing services
    services = db.execute("SELECT id, service_name, unit_price, is_active FROM nursing_services ORDER BY service_name").fetchall()
    # Load exclusions for this insurance
    rows = db.execute("SELECT nursing_service_id FROM insurance_nursing_exclusions WHERE insurance_type = ?", (insurance_type,)).fetchall()
    excluded = {r['nursing_service_id'] for r in rows}
    result = []
    for s in services:
        result.append({
            'id': s['id'],
            'service_name': s['service_name'],
            'unit_price': s['unit_price'],
            'is_active': s['is_active'],
            'excluded': s['id'] in excluded
        })
    return jsonify({'insurance_type': insurance_type, 'services': result})


@bp.route('/insurance/<string:insurance_type>/nursing_exclusions', methods=['POST'])
@login_required
def set_nursing_exclusions(insurance_type: str):
    """Replace nursing exclusions for an insurance. Accepts JSON: { service_ids: [1,2,3] }"""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    service_ids = data.get('service_ids')
    if service_ids is None:
        return jsonify({'error': 'service_ids required'}), 400
    try:
        service_ids = [int(x) for x in service_ids]
    except Exception:
        return jsonify({'error': 'service_ids must be integers list'}), 400
    db = get_db()
    # Replace: delete existing for this insurance, then insert provided ones
    db.execute("DELETE FROM insurance_nursing_exclusions WHERE insurance_type = ?", (insurance_type,))
    for sid in service_ids:
        db.execute("INSERT INTO insurance_nursing_exclusions (insurance_type, nursing_service_id) VALUES (?, ?)", (insurance_type, sid))
    db.commit()
    return jsonify({'success': True, 'insurance_type': insurance_type, 'service_ids': service_ids})


@bp.route('/tariffs/consumables', methods=['GET', 'POST'])
@login_required
def consumables_tariffs():
    """تعرفه مصرفی‌ها - دارو و عمومی"""
    if g.user['role'] != 'manager':
        flash('دسترسی محدود', 'error')
        return redirect(url_for('reception.index'))
    
    db = get_db()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            name = request.form.get('name', '').strip()
            default_price = request.form.get('default_price', '0')
            category = request.form.get('category', 'supply')
            try:
                default_price = float(default_price.replace(',', ''))
            except Exception:
                default_price = 0
            
            if name:
                # چک کردن تکراری نبودن
                existing = db.execute(
                    "SELECT id FROM consumable_tariffs WHERE name = ?",
                    (name,)
                ).fetchone()
                if existing:
                    flash(f'مصرفی "{name}" قبلاً تعریف شده است.', 'error')
                else:
                    db.execute(
                        "INSERT INTO consumable_tariffs (name, default_price, category, is_active) VALUES (?, ?, ?, 1)",
                        (name, default_price, category)
                    )
                    db.commit()
                    flash(f'مصرفی "{name}" با موفقیت اضافه شد.', 'success')
        
        elif action == 'update':
            item_id = request.form.get('item_id')
            name = request.form.get('name', '').strip()
            default_price = request.form.get('default_price', '0')
            category = request.form.get('category', 'supply')
            is_active = request.form.get('is_active') == '1'
            try:
                default_price = float(default_price.replace(',', ''))
            except Exception:
                default_price = 0
            
            if item_id and name:
                db.execute(
                    "UPDATE consumable_tariffs SET name = ?, default_price = ?, category = ?, is_active = ? WHERE id = ?",
                    (name, default_price, category, 1 if is_active else 0, item_id)
                )
                db.commit()
                flash('مصرفی با موفقیت بروزرسانی شد.', 'success')
        
        elif action == 'delete':
            item_id = request.form.get('item_id')
            if item_id:
                db.execute("DELETE FROM consumable_tariffs WHERE id = ?", (item_id,))
                db.commit()
                flash('مصرفی با موفقیت حذف شد.', 'success')
        
        return redirect(url_for('manager.consumables_tariffs'))
    
    # فیلتر بر اساس دسته
    category_filter = request.args.get('category', '')
    
    if category_filter:
        items = db.execute(
            "SELECT * FROM consumable_tariffs WHERE category = ? ORDER BY is_active DESC, name",
            (category_filter,)
        ).fetchall()
    else:
        items = db.execute(
            "SELECT * FROM consumable_tariffs ORDER BY is_active DESC, category, name"
        ).fetchall()
    
    drugs = [i for i in items if i['category'] == 'drug']
    supplies = [i for i in items if i['category'] == 'supply']
    
    return render_template(
        'manager/tariffs_consumables.html',
        items=items,
        drugs=drugs,
        supplies=supplies,
        category_filter=category_filter
    )


# ==================== مدیریت کاربران و کادر درمان ====================

@bp.route('/users', methods=['GET', 'POST'])
@login_required
def users_management():
    """مدیریت کاربران سیستم (مدیر و پذیرش) و کادر درمان (پزشک و پرستار)"""
    if g.user['role'] != 'manager':
        flash('دسترسی محدود', 'error')
        return redirect(url_for('reception.index'))
    
    db = get_db()
    
    if request.method == 'POST':
        action = request.form.get('action')
        entity_type = request.form.get('entity_type')  # 'user' or 'staff'
        
        if entity_type == 'user':
            if action == 'add':
                username = request.form.get('username', '').strip()
                password = request.form.get('password', '').strip()
                full_name = request.form.get('full_name', '').strip()
                role = request.form.get('role', 'reception')
                
                if username and password and full_name:
                    # چک کردن تکراری نبودن نام کاربری
                    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                    if existing:
                        flash(f'نام کاربری "{username}" قبلاً استفاده شده است.', 'error')
                    else:
                        # Use AuthService.register_user to create the user with bcrypt
                        from src.services.auth_service import AuthService
                        auth_service = AuthService()
                        created = auth_service.register_user(username, password, role, full_name)
                        if created:
                            flash(f'کاربر "{full_name}" با موفقیت اضافه شد.', 'success')
                        else:
                            flash(f'خطا در ایجاد کاربر "{full_name}".', 'error')
            
            elif action == 'update':
                user_id = request.form.get('user_id')
                username = request.form.get('username', '').strip()
                full_name = request.form.get('full_name', '').strip()
                role = request.form.get('role', 'reception')
                is_active = request.form.get('is_active') == '1'
                new_password = request.form.get('new_password', '').strip()
                
                if user_id and username and full_name:
                    # چک کردن تکراری نبودن نام کاربری برای کاربر دیگر
                    existing = db.execute("SELECT id FROM users WHERE username = ? AND id != ?", (username, user_id)).fetchone()
                    if existing:
                        flash(f'نام کاربری "{username}" قبلاً استفاده شده است.', 'error')
                    else:
                        if new_password:
                            from werkzeug.security import generate_password_hash
                            password_hash = generate_password_hash(new_password)
                            db.execute(
                                "UPDATE users SET username = ?, password_hash = ?, role = ?, full_name = ?, is_active = ? WHERE id = ?",
                                (username, password_hash, role, full_name, 1 if is_active else 0, user_id)
                            )
                        else:
                            db.execute(
                                "UPDATE users SET username = ?, role = ?, full_name = ?, is_active = ? WHERE id = ?",
                                (username, role, full_name, 1 if is_active else 0, user_id)
                            )
                        db.commit()
                        flash('کاربر با موفقیت بروزرسانی شد.', 'success')
            
            elif action == 'delete':
                user_id = request.form.get('user_id')
                # نباید کاربر فعلی را حذف کند
                if user_id and int(user_id) != g.user['id']:
                    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
                    db.commit()
                    flash('کاربر با موفقیت حذف شد.', 'success')
                else:
                    flash('نمی‌توانید حساب کاربری خودتان را حذف کنید.', 'error')
        
        elif entity_type == 'staff':
            if action == 'add':
                full_name = request.form.get('full_name', '').strip()
                staff_type = request.form.get('staff_type', 'doctor')
                
                if full_name:
                    db.execute(
                        "INSERT INTO medical_staff (full_name, staff_type, is_active) VALUES (?, ?, 1)",
                        (full_name, staff_type)
                    )
                    db.commit()
                    flash(f'{"پزشک" if staff_type == "doctor" else "پرستار"} "{full_name}" با موفقیت اضافه شد.', 'success')
            
            elif action == 'update':
                staff_id = request.form.get('staff_id')
                full_name = request.form.get('full_name', '').strip()
                staff_type = request.form.get('staff_type', 'doctor')
                is_active = request.form.get('is_active') == '1'
                
                if staff_id and full_name:
                    db.execute(
                        "UPDATE medical_staff SET full_name = ?, staff_type = ?, is_active = ? WHERE id = ?",
                        (full_name, staff_type, 1 if is_active else 0, staff_id)
                    )
                    db.commit()
                    flash('اطلاعات کادر درمان با موفقیت بروزرسانی شد.', 'success')
            
            elif action == 'delete':
                staff_id = request.form.get('staff_id')
                if staff_id:
                    db.execute("DELETE FROM medical_staff WHERE id = ?", (staff_id,))
                    db.commit()
                    flash('کادر درمان با موفقیت حذف شد.', 'success')
        
        return redirect(url_for('manager.users_management'))
    
    # دریافت لیست‌ها
    users = db.execute("SELECT * FROM users ORDER BY role, full_name").fetchall()
    staff = db.execute("SELECT * FROM medical_staff ORDER BY staff_type, full_name").fetchall()
    
    managers = [u for u in users if u['role'] == 'manager']
    receptions = [u for u in users if u['role'] == 'reception']
    doctors = [s for s in staff if s['staff_type'] == 'doctor']
    nurses = [s for s in staff if s['staff_type'] == 'nurse']
    
    return render_template(
        'manager/users.html',
        users=users,
        managers=managers,
        receptions=receptions,
        doctors=doctors,
        nurses=nurses
    )


# ==================== مدیریت حقوق ====================

@bp.route('/payroll', methods=['GET', 'POST'])
@login_required
def payroll():
    """صفحه مدیریت حقوق با سه بخش: محاسبه، تنظیمات ذخیره شده، افزودن تنظیمات"""
    if g.user['role'] != 'manager':
        flash('دسترسی محدود', 'error')
        return redirect(url_for('reception.index'))
    
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
    
    doctors = [s for s in staff if s['staff_type'] == 'doctor']
    nurses = [s for s in staff if s['staff_type'] == 'nurse']
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'save_settings':
            staff_id = request.form.get('staff_id')
            base_morning = float(request.form.get('base_morning', 0))
            base_evening = float(request.form.get('base_evening', 0))
            base_night = float(request.form.get('base_night', 0))
            visit_fee = float(request.form.get('visit_fee', 0))
            injection_percent = float(request.form.get('injection_percent', 0))
            procedure_percent = float(request.form.get('procedure_percent', 0))
            tax_percent = float(request.form.get('tax_percent', 0))
            nursing_percent = float(request.form.get('nursing_percent', 0))
            nurse_procedure_percent = float(request.form.get('nurse_procedure_percent', 0))
            
            # چک کردن وجود تنظیمات
            existing = db.execute("SELECT id FROM payroll_settings WHERE staff_id = ?", (staff_id,)).fetchone()
            
            if existing:
                db.execute("""
                    UPDATE payroll_settings SET
                        base_morning = ?, base_evening = ?, base_night = ?,
                        visit_fee = ?, injection_percent = ?, procedure_percent = ?, tax_percent = ?,
                        nursing_percent = ?, nurse_procedure_percent = ?,
                        updated_at = datetime('now', '+3 hours', '+30 minutes')
                    WHERE staff_id = ?
                """, (base_morning, base_evening, base_night, visit_fee, injection_percent, 
                      procedure_percent, tax_percent, nursing_percent, nurse_procedure_percent, staff_id))
            else:
                db.execute("""
                    INSERT INTO payroll_settings 
                    (staff_id, base_morning, base_evening, base_night, visit_fee, injection_percent, 
                     procedure_percent, tax_percent, nursing_percent, nurse_procedure_percent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (staff_id, base_morning, base_evening, base_night, visit_fee, injection_percent,
                      procedure_percent, tax_percent, nursing_percent, nurse_procedure_percent))
            
            db.commit()
            flash('تنظیمات حقوق با موفقیت ذخیره شد.', 'success')
            return redirect(url_for('manager.payroll'))
        
        elif action == 'delete_settings':
            staff_id = request.form.get('staff_id')
            db.execute("DELETE FROM payroll_settings WHERE staff_id = ?", (staff_id,))
            db.commit()
            flash('تنظیمات حقوق حذف شد.', 'success')
            return redirect(url_for('manager.payroll'))
    
    # تاریخ امروز شمسی
    from datetime import date
    from ..common.jalali import Gregorian
    today = iran_now().date()
    today_jalali = Gregorian(today).persian_string()  # مثلاً 1404-03-15
    
    return render_template(
        'manager/payroll.html',
        staff=staff,
        doctors=doctors,
        nurses=nurses,
        today_jalali=today_jalali
    )


@bp.route('/payroll/calculate', methods=['POST'])
@login_required
def calculate_payroll():
    """محاسبه حقوق بر اساس فیلترها
    
    نکته مهم: محاسبه حقوق از ساعت 7:30 صبح روز شروع تا 7:30 صبح روز بعد از پایان انجام می‌شود.
    این باعث می‌شود شیفت شب آخرین روز هم کامل محاسبه شود.
    """
    if g.user['role'] != 'manager':
        return jsonify({'error': 'دسترسی محدود'}), 403
    
    db = get_db()
    
    # دریافت پارامترها
    staff_id = request.form.get('staff_id')
    staff_type = request.form.get('staff_type')  # doctor یا nurse یا all
    shift_filter = request.form.get('shift')  # morning/evening/night/all
    
    # دریافت تاریخ - حالت 1: تاریخ میلادی (از JavaScript)
    date_from = request.form.get('date_from')
    date_to = request.form.get('date_to')
    
    # حالت 2: اگر تاریخ‌های شمسی جداگانه ارسال شده، آن‌ها را تبدیل کن
    from_year = request.form.get('from_year')
    from_month = request.form.get('from_month')
    from_day = request.form.get('from_day')
    to_year = request.form.get('to_year')
    to_month = request.form.get('to_month')
    to_day = request.form.get('to_day')
    
    if from_year and from_month and from_day and not date_from:
        from ..common.jalali import Persian
        try:
            persian_from = Persian(int(from_year), int(from_month), int(from_day))
            gregorian_from = persian_from.gregorian_datetime()
            date_from = gregorian_from.strftime('%Y-%m-%d')
        except Exception as e:
            print(f"[Manager.calculate_payroll] Error parsing from date: {e}")
    
    if to_year and to_month and to_day and not date_to:
        from ..common.jalali import Persian
        try:
            persian_to = Persian(int(to_year), int(to_month), int(to_day))
            gregorian_to = persian_to.gregorian_datetime()
            date_to = gregorian_to.strftime('%Y-%m-%d')
        except Exception as e:
            print(f"[Manager.calculate_payroll] Error parsing to date: {e}")
    
    # بازه گزارش بر اساس work_date (روز کاری) است تا شیفت شب بین دو تاریخ نشکند.
    work_date_from = date_from
    work_date_to = date_to
    
    results = []
    
    # تعیین لیست پرسنل
    if staff_id and staff_id != 'all':
        staff_list = db.execute("""
            SELECT m.*, p.base_morning, p.base_evening, p.base_night,
                   p.visit_fee, p.injection_percent, p.procedure_percent, p.tax_percent,
                   p.nursing_percent, p.nurse_procedure_percent
            FROM medical_staff m
            LEFT JOIN payroll_settings p ON m.id = p.staff_id
            WHERE m.id = ? AND m.is_active = 1
        """, (staff_id,)).fetchall()
    else:
        query = """
            SELECT m.*, p.base_morning, p.base_evening, p.base_night,
                   p.visit_fee, p.injection_percent, p.procedure_percent, p.tax_percent,
                   p.nursing_percent, p.nurse_procedure_percent
            FROM medical_staff m
            LEFT JOIN payroll_settings p ON m.id = p.staff_id
            WHERE m.is_active = 1
        """
        if staff_type and staff_type != 'all':
            query += f" AND m.staff_type = '{staff_type}'"
        staff_list = db.execute(query).fetchall()
    
    for person in staff_list:
        person_id = person['id']
        person_type = person['staff_type']
        person_name = person['full_name']
        
        # تنظیمات حقوق
        base_morning = person['base_morning'] or 0
        base_evening = person['base_evening'] or 0
        base_night = person['base_night'] or 0
        
        details = []
        total_salary = 0
        
        # ========== محاسبه شیفت‌ها از کار واقعی (تزریقات/ویزیت/کار عملی) ==========
        # توجه: جدول shift_staff فقط یک پزشک/پرستار در هر شیفت ذخیره می‌کند
        # اما ممکن است چند نفر در یک شیفت کار کنند، پس از کار واقعی می‌خوانیم
        # در نسخه جدید، تاریخ کاری از ساعت/شیفت مشتق نمی‌شود؛ از work_date استفاده می‌کنیم.
        
        if person_type == 'doctor':
            # شیفت‌های پزشک از تزریقات و ویزیت‌ها
            shift_query = f"""
                SELECT shift, COUNT(*) as cnt FROM (
                    SELECT DISTINCT work_date as work_date, shift
                    FROM injections WHERE doctor_id = ?
                    UNION
                    SELECT DISTINCT work_date as work_date, shift
                    FROM visits WHERE doctor_id = ?
                    UNION
                    SELECT DISTINCT work_date as work_date, shift
                    FROM procedures WHERE doctor_id = ?
                ) AS actual_shifts
            """
            shift_params = [person_id, person_id, person_id]
        else:
            # شیفت‌های پرستار از تزریقات و کار عملی
            shift_query = f"""
                SELECT shift, COUNT(*) as cnt FROM (
                    SELECT DISTINCT work_date as work_date, shift
                    FROM injections WHERE nurse_id = ?
                    UNION
                    SELECT DISTINCT work_date as work_date, shift
                    FROM procedures WHERE nurse_id = ?
                ) AS actual_shifts
            """
            shift_params = [person_id, person_id]
        
        # اضافه کردن فیلتر تاریخ و فیلتر نوع شیفت
        # قرار می‌دهیم WHERE بعد از derived table باشد (بعد از "AS actual_shifts")
        # تا بتوانیم روی work_date مجموع union را فیلتر کنیم.
        has_where = False
        if work_date_from and work_date_to:
            shift_query += f" WHERE work_date BETWEEN '{work_date_from}' AND '{work_date_to}'"
            has_where = True

        # اضافه کردن فیلتر نوع شیفت
        if shift_filter and shift_filter != 'all':
            if has_where:
                shift_query += f" AND shift = '{shift_filter}'"
            else:
                shift_query += f" WHERE shift = '{shift_filter}'"

        shift_query += " GROUP BY shift"
        
        shifts = db.execute(shift_query, shift_params).fetchall()
        
        morning_count = sum(s['cnt'] for s in shifts if s['shift'] == 'morning')
        evening_count = sum(s['cnt'] for s in shifts if s['shift'] == 'evening')
        night_count = sum(s['cnt'] for s in shifts if s['shift'] == 'night')
        
        shift_total = (morning_count * base_morning) + (evening_count * base_evening) + (night_count * base_night)
        
        if morning_count > 0:
            details.append({
                'type': 'شیفت صبح',
                'count': morning_count,
                'unit_price': base_morning,
                'total': morning_count * base_morning
            })
        if evening_count > 0:
            details.append({
                'type': 'شیفت عصر',
                'count': evening_count,
                'unit_price': base_evening,
                'total': evening_count * base_evening
            })
        if night_count > 0:
            details.append({
                'type': 'شیفت شب',
                'count': night_count,
                'unit_price': base_night,
                'total': night_count * base_night
            })
        
        total_salary += shift_total
        
        if person_type == 'doctor':
            # ========== ویزیت‌ها ==========
            visit_fee = person['visit_fee'] or 20000
            # فقط ویزیت‌های فاکتورهای بسته‌شده را بشمار
            visit_query = """SELECT COUNT(*) as cnt FROM visits v
                             JOIN invoices inv ON v.invoice_id = inv.id
                             WHERE inv.status = 'closed' AND v.doctor_id = ?"""
            visit_params = [person_id]
            
            if work_date_from and work_date_to:
                visit_query += " AND v.work_date BETWEEN ? AND ?"
                visit_params.extend([work_date_from, work_date_to])
            
            if shift_filter and shift_filter != 'all':
                visit_query += " AND v.shift = ?"
                visit_params.append(shift_filter)
            
            visit_count = db.execute(visit_query, visit_params).fetchone()['cnt']
            visit_total = visit_count * visit_fee
            
            if visit_count > 0:
                details.append({
                    'type': 'ویزیت',
                    'count': visit_count,
                    'unit_price': visit_fee,
                    'total': visit_total
                })
            total_salary += visit_total
            
            # ========== تزریقات پزشک ==========
            injection_percent = person['injection_percent'] or 30
            inj_query = """SELECT SUM(i.total_price) as total FROM injections i
                           JOIN invoices inv ON i.invoice_id = inv.id
                           WHERE inv.status = 'closed' AND i.doctor_id = ?"""
            inj_params = [person_id]
            
            if work_date_from and work_date_to:
                inj_query += " AND i.work_date BETWEEN ? AND ?"
                inj_params.extend([work_date_from, work_date_to])
            
            if shift_filter and shift_filter != 'all':
                inj_query += " AND shift = ?"
                inj_params.append(shift_filter)
            
            inj_result = db.execute(inj_query, inj_params).fetchone()
            inj_total = (inj_result['total'] or 0) * injection_percent / 100
            
            if inj_total > 0:
                details.append({
                    'type': f'سهم تزریقات ({injection_percent}%)',
                    'count': 1,
                    'unit_price': inj_result['total'] or 0,
                    'total': inj_total
                })
            total_salary += inj_total
            
            # ========== کار عملی پزشک ==========
            procedure_percent = person['procedure_percent'] or 40
            proc_query = """SELECT SUM(p.price) as total FROM procedures p
                            JOIN invoices inv ON p.invoice_id = inv.id
                            WHERE inv.status = 'closed' AND p.doctor_id = ?"""
            proc_params = [person_id]
            
            if work_date_from and work_date_to:
                proc_query += " AND pr.work_date BETWEEN ? AND ?"
                proc_params.extend([work_date_from, work_date_to])
            
            if shift_filter and shift_filter != 'all':
                proc_query += " AND shift = ?"
                proc_params.append(shift_filter)
            
            proc_result = db.execute(proc_query, proc_params).fetchone()
            proc_total = (proc_result['total'] or 0) * procedure_percent / 100
            
            if proc_total > 0:
                details.append({
                    'type': f'سهم کار عملی ({procedure_percent}%)',
                    'count': 1,
                    'unit_price': proc_result['total'] or 0,
                    'total': proc_total
                })
            total_salary += proc_total
            
            # ========== کسر مالیات ==========
            tax_percent = person['tax_percent'] or 10
            tax_amount = total_salary * tax_percent / 100
            
            details.append({
                'type': f'کسر مالیات ({tax_percent}%)',
                'count': 1,
                'unit_price': total_salary,
                'total': -tax_amount
            })
            total_salary -= tax_amount
        
        else:  # پرستار
            # ========== خدمات پرستاری ==========
            # Note: فعلاً از جدول injections استفاده می‌کنیم که nurse_id دارد
            nursing_percent = person['nursing_percent'] or 6
            
            # تزریقات پرستار
            nurse_inj_query = """SELECT SUM(i.total_price) as total FROM injections i
                                 JOIN invoices inv ON i.invoice_id = inv.id
                                 WHERE inv.status = 'closed' AND i.nurse_id = ?"""
            nurse_inj_params = [person_id]
            
            if work_date_from and work_date_to:
                nurse_inj_query += " AND i.work_date BETWEEN ? AND ?"
                nurse_inj_params.extend([work_date_from, work_date_to])
            
            if shift_filter and shift_filter != 'all':
                nurse_inj_query += " AND shift = ?"
                nurse_inj_params.append(shift_filter)
            
            nurse_inj_result = db.execute(nurse_inj_query, nurse_inj_params).fetchone()
            nursing_total = (nurse_inj_result['total'] or 0) * nursing_percent / 100
            
            if nursing_total > 0:
                details.append({
                    'type': f'سهم خدمات پرستاری ({nursing_percent}%)',
                    'count': 1,
                    'unit_price': nurse_inj_result['total'] or 0,
                    'total': nursing_total
                })
            total_salary += nursing_total
            
            # ========== کار عملی پرستار ==========
            nurse_procedure_percent = person['nurse_procedure_percent'] or 35
            nurse_proc_query = """SELECT SUM(p.price) as total FROM procedures p
                                  JOIN invoices inv ON p.invoice_id = inv.id
                                  WHERE inv.status = 'closed' AND p.nurse_id = ?"""
            nurse_proc_params = [person_id]
            
            if work_date_from and work_date_to:
                nurse_proc_query += " AND pr.work_date BETWEEN ? AND ?"
                nurse_proc_params.extend([work_date_from, work_date_to])
            
            if shift_filter and shift_filter != 'all':
                nurse_proc_query += " AND shift = ?"
                nurse_proc_params.append(shift_filter)
            
            nurse_proc_result = db.execute(nurse_proc_query, nurse_proc_params).fetchone()
            nurse_proc_total = (nurse_proc_result['total'] or 0) * nurse_procedure_percent / 100
            
            if nurse_proc_total > 0:
                details.append({
                    'type': f'سهم کار عملی پرستار ({nurse_procedure_percent}%)',
                    'count': 1,
                    'unit_price': nurse_proc_result['total'] or 0,
                    'total': nurse_proc_total
                })
            total_salary += nurse_proc_total
        
        results.append({
            'id': person_id,
            'name': person_name,
            'type': person_type,
            'type_label': 'پزشک' if person_type == 'doctor' else 'پرستار',
            'details': details,
            'total_salary': total_salary
        })
    
    return jsonify({'success': True, 'results': results})


# ====== بخش لاگ فعالیت‌ها ======

@bp.route('/logs')
@login_required
def activity_logs():
    """صفحه نمایش لاگ فعالیت‌های کاربران."""
    if g.user['role'] != 'manager':
        return redirect(url_for('reception.index'))
    
    from src.services.activity_logger import get_activity_logs, get_logs_count, ActionType, ActionCategory
    
    # فیلترها
    page = request.args.get('page', 1, type=int)
    per_page = 50
    user_id = request.args.get('user_id', type=int)
    action_type = request.args.get('action_type', '')
    action_category = request.args.get('action_category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search_text = request.args.get('patient_name', '')  # جستجو در نام بیمار و توضیحات
    invoice_id = request.args.get('invoice_id', type=int)
    
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
        offset=(page - 1) * per_page
    )
    total = get_logs_count(
        user_id=user_id if user_id else None,
        action_type=action_type if action_type else None,
        action_category=action_category if action_category else None,
        invoice_id=invoice_id if invoice_id else None,
        date_from=date_from if date_from else None,
        date_to=date_to if date_to else None,
        search_text=search_text if search_text else None
    )
    total_pages = (total + per_page - 1) // per_page
    
    # لیست کاربران برای فیلتر
    db = get_db()
    users = db.execute("SELECT id, username, full_name FROM users WHERE is_active = 1 ORDER BY username").fetchall()
    
    # ترجمه نوع عملیات
    action_types = [
        ('login', 'ورود'),
        ('logout', 'خروج'),
        ('patient_create', 'ایجاد بیمار'),
        ('patient_update', 'ویرایش بیمار'),
        ('patient_search', 'جستجوی بیمار'),
        ('patient_history', 'مشاهده سابقه'),
        ('invoice_create', 'ایجاد فاکتور'),
        ('invoice_open', 'باز کردن فاکتور'),
        ('invoice_close', 'بستن فاکتور'),
        ('invoice_view', 'مشاهده فاکتور'),
        ('visit_add', 'ثبت ویزیت'),
        ('visit_delete', 'حذف ویزیت'),
        ('injection_add', 'ثبت تزریق'),
        ('injection_delete', 'حذف تزریق'),
        ('procedure_add', 'ثبت کار عملی'),
        ('procedure_delete', 'حذف کار عملی'),
        ('consumable_use', 'ثبت مصرفی'),
        ('consumable_delete', 'حذف مصرفی'),
        ('item_payment_set', 'تغییر وضعیت پرداخت'),
        ('shift_staff_set', 'تنظیم کادر درمان'),
        ('print_invoice', 'چاپ فاکتور'),
        ('print_receipt', 'چاپ رسید'),
        ('print_report', 'چاپ گزارش'),
    ]
    
    # ترجمه دسته‌بندی
    action_categories = [
        ('auth', 'احراز هویت'),
        ('patient', 'بیمار'),
        ('invoice', 'فاکتور'),
        ('visit', 'ویزیت'),
        ('injection', 'تزریق'),
        ('procedure', 'کار عملی'),
        ('consumable', 'مصرفی'),
        ('shift', 'شیفت'),
        ('print', 'چاپ'),
        ('report', 'گزارش'),
    ]
    
    # ساخت دیکشنری فیلترها برای نمایش در تمپلیت
    current_filters = {
        'user_id': user_id,
        'action_type': action_type,
        'action_category': action_category,
        'date_from': date_from,
        'date_to': date_to,
        'patient_name': search_text,
        'invoice_id': invoice_id
    }

    # تاریخ امروز (تهران) برای دکمه‌های سریع سمت UI
    j_today = jdatetime.date.fromgregorian(date=iran_now().date())
    
    return render_template('manager/logs.html',
        logs=logs,
        page=page,
        total_pages=total_pages,
        total=total,
        users=users,
        action_types=action_types,
        action_categories=action_categories,
        filters=current_filters,
        j_today=j_today
    )


@bp.route('/logs/export')
@login_required
def export_logs():
    """خروجی CSV از لاگ‌ها."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'دسترسی غیرمجاز'}), 403
    
    from src.services.activity_logger import get_activity_logs
    
    # فیلترها
    user_id = request.args.get('user_id', type=int)
    action_type = request.args.get('action_type', '')
    action_category = request.args.get('action_category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search_text = request.args.get('patient_name', '')
    
    # دریافت همه لاگ‌ها (بدون محدودیت)
    logs = get_activity_logs(
        user_id=user_id if user_id else None,
        action_type=action_type if action_type else None,
        action_category=action_category if action_category else None,
        date_from=date_from if date_from else None,
        date_to=date_to if date_to else None,
        search_text=search_text if search_text else None,
        limit=10000
    )
    
    # ساخت CSV با BOM برای پشتیبانی فارسی در Excel
    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow(['تاریخ', 'کاربر', 'نوع عملیات', 'دسته', 'توضیحات', 'بیمار', 'فاکتور', 'مبلغ'])
    
    for log in logs:
        writer.writerow([
            log.get('created_at', ''),
            log.get('username', ''),
            log.get('action_type', ''),
            log.get('action_category', ''),
            log.get('description', ''),
            log.get('patient_name', ''),
            log.get('invoice_id', ''),
            log.get('amount', ''),
        ])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=logs_{iran_now().strftime("%Y%m%d_%H%M%S")}.csv'
    return response


@bp.route('/logs/stats')
@login_required
def logs_stats():
    """آمار لاگ‌ها."""
    if g.user['role'] != 'manager':
        return jsonify({'error': 'دسترسی غیرمجاز'}), 403
    
    from src.services.activity_logger import get_action_stats
    
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    stats = get_action_stats(date_from, date_to)
    return jsonify({'success': True, 'stats': stats})

