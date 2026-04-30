"""
Doctor Dashboard API
Clean and modular doctor-facing routes
"""
from flask import Blueprint, render_template, redirect, url_for, g
from src.api.auth import login_required
from src.adapters.sqlite.core import get_db
from src.common.utils import iran_now, get_work_date_for_datetime
from datetime import datetime, timedelta
import jdatetime

bp = Blueprint('doctor', __name__, url_prefix='/doctor')


@bp.route('/')
@login_required
def index():
    """Doctor dashboard - clean overview of their work."""
    # Ensure user is actually a doctor
    if g.user['role'] != 'doctor':
        return redirect(url_for('dashboard.index'))
    
    # Get doctor's medical_staff record
    doctor_staff = _get_doctor_staff_record(g.user)
    if not doctor_staff:
        return render_template('doctor/index.html', error='حساب کاربری شما به کادر درمان متصل نیست')
    
    doctor_id = doctor_staff['id']
    db = get_db()
    
    # Today's work date
    today_date = get_work_date_for_datetime()
    
    # Today's stats
    today_stats = _get_today_stats(db, doctor_id, today_date)
    
    # Recent visits (last 10)
    recent_visits = _get_recent_visits(db, doctor_id, limit=10)
    
    # This month stats
    month_stats = _get_month_stats(db, doctor_id)
    
    return render_template(
        'doctor/index.html',
        doctor=doctor_staff,
        today_stats=today_stats,
        recent_visits=recent_visits,
        month_stats=month_stats,
        server_time=iran_now().isoformat()
    )


def _get_doctor_staff_record(user_row: dict) -> dict:
    """Get medical_staff record linked to this user account.

    Preferred mapping: users.staff_id -> medical_staff.id.
    No fallback: doctor dashboards must be tied to the correct staff record.
    """
    db = get_db()
    staff_id = None
    try:
        staff_id = user_row['staff_id']
    except Exception:
        staff_id = None
    if not staff_id:
        return None

    row = db.execute(
        "SELECT * FROM medical_staff WHERE id = ? AND staff_type = 'doctor' AND is_active = 1",
        (staff_id,),
    ).fetchone()
    return dict(row) if row else None


def _get_today_stats(db, doctor_id: int, work_date: str) -> dict:
    """Get today's statistics for doctor."""
    visits_count = db.execute("""
        SELECT COUNT(*) as count FROM visits 
        WHERE doctor_id = ? AND work_date = ?
    """, (doctor_id, work_date)).fetchone()['count']
    
    injections_count = db.execute("""
        SELECT COUNT(*) as count FROM injections 
        WHERE doctor_id = ? AND work_date = ?
    """, (doctor_id, work_date)).fetchone()['count']
    
    procedures_count = db.execute("""
        SELECT COUNT(*) as count FROM procedures 
        WHERE doctor_id = ? AND work_date = ?
    """, (doctor_id, work_date)).fetchone()['count']
    
    # Unique patients today
    patients_count = db.execute("""
        SELECT COUNT(DISTINCT patient_id) as count FROM visits 
        WHERE doctor_id = ? AND work_date = ?
    """, (doctor_id, work_date)).fetchone()['count']
    
    return {
        'visits': visits_count,
        'injections': injections_count,
        'procedures': procedures_count,
        'patients': patients_count
    }


def _get_recent_visits(db, doctor_id: int, limit: int = 10) -> list:
    """Get doctor's recent visits with patient info."""
    rows = db.execute("""
        SELECT v.*, p.full_name as patient_name, p.national_id,
               i.status as invoice_status, i.id as invoice_id
        FROM visits v
        JOIN patients p ON v.patient_id = p.id
        LEFT JOIN invoices i ON v.invoice_id = i.id
        WHERE v.doctor_id = ?
        ORDER BY v.visit_date DESC
        LIMIT ?
    """, (doctor_id, limit)).fetchall()
    return [dict(r) for r in rows]


def _get_month_stats(db, doctor_id: int) -> dict:
    """Get this month's statistics."""
    # Calculate month start (Jalali)
    j_today = jdatetime.date.fromgregorian(date=iran_now().date())
    j_month_start = jdatetime.date(j_today.year, j_today.month, 1)
    g_month_start = j_month_start.togregorian()
    month_start = datetime(g_month_start.year, g_month_start.month, g_month_start.day)
    month_start_date = month_start.strftime('%Y-%m-%d')
    
    visits_count = db.execute("""
        SELECT COUNT(*) as count FROM visits 
        WHERE doctor_id = ? AND work_date >= ?
    """, (doctor_id, month_start_date)).fetchone()['count']
    
    patients_count = db.execute("""
        SELECT COUNT(DISTINCT patient_id) as count FROM visits 
        WHERE doctor_id = ? AND work_date >= ?
    """, (doctor_id, month_start_date)).fetchone()['count']
    
    return {
        'visits': visits_count,
        'patients': patients_count
    }
