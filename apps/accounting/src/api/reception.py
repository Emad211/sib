from flask import (
    Blueprint, render_template, request, flash, redirect, url_for, jsonify, g
)
from src.api.auth import login_required
from src.services.reception_service import ReceptionService
from src.services.activity_logger import log_activity, ActionType, ActionCategory
from src.common.utils import iran_now
from datetime import datetime, timedelta

bp = Blueprint('reception', __name__, url_prefix='/reception')


@bp.route('/')
@login_required
def index():
    """Reception dashboard: show open invoices and invoice panel."""
    service = ReceptionService()
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    invoice_repo = InvoiceRepository()
    
    # Get all open invoices
    open_invoices = invoice_repo.get_open_invoices()
    
    # Get selected invoice (from query param or first open invoice)
    selected_invoice_id = request.args.get('invoice_id', type=int)
    if not selected_invoice_id and open_invoices:
        selected_invoice_id = open_invoices[0]['id']
    
    # Get invoice details and items if an invoice is selected
    invoice_details = None
    invoice_items = []
    if selected_invoice_id:
        invoice_details = invoice_repo.get_invoice_by_id(selected_invoice_id)
        invoice_items = invoice_repo.get_invoice_items(selected_invoice_id)
        financials = invoice_repo.get_financials(selected_invoice_id)
        from src.adapters.sqlite.payments_repo import InvoiceItemPaymentRepository
        pay_repo = InvoiceItemPaymentRepository()
        payments = pay_repo.get_payments_for_invoice(selected_invoice_id)
    else:
        financials = None
        payments = []
    
    # Load doctors/nurses for staff card
    from src.adapters.sqlite.core import get_db
    db = get_db()
    doctors = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type = 'doctor' AND is_active = 1").fetchall()
    nurses = db.execute("SELECT id, full_name FROM medical_staff WHERE staff_type = 'nurse' AND is_active = 1").fetchall()

    # Determine user's effective (manual) shift
    from src.adapters.sqlite.user_shift_repo import UserShiftRepository
    user_shift_repo = UserShiftRepository()

    initial_active_shift, initial_work_date, initial_is_overdue, initial_should_prompt = user_shift_repo.get_effective_shift_for_user(g.user['id'])

    # Current shift staff (if any) - keyed by manual work_date + shift
    from src.adapters.sqlite.shift_staff_repo import ShiftStaffRepository
    shift_repo = ShiftStaffRepository()
    shift_staff = shift_repo.get_shift_staff(initial_work_date, initial_active_shift) or {}
    current_shift = initial_active_shift

    return render_template(
        'reception/index.html',
        open_invoices=open_invoices,
        selected_invoice=invoice_details,
        invoice_items=invoice_items,
        payments=payments,
        financials=financials,
        doctors=[dict(d) for d in doctors],
        nurses=[dict(n) for n in nurses],
        shift_staff=shift_staff,
        current_shift=current_shift,
        initial_active_shift=initial_active_shift,
        initial_active_shift_fa={'morning':'شیفت صبح','evening':'شیفت عصر','night':'شیفت شب'}.get(initial_active_shift, initial_active_shift),
        initial_work_date=initial_work_date,
        initial_is_overdue=initial_is_overdue,
        initial_should_prompt=initial_should_prompt,
        server_time=iran_now().isoformat(),
    )


@bp.before_app_request
def _ensure_user_shift_state():
    """
    Ensure that for any request we compute and attach the user's effective shift
    and the current open-invoice count to `g` so templates can rely on it.
    This makes the UI reflect the server state immediately (no need to wait
    for the first client poll) and centralizes the logic.
    """
    # Initialize with defaults to avoid AttributeError
    g.user_shift_status = {
        'active_shift': None,
        'work_date': None,
        'is_overdue': False,
        'should_prompt': False,
        'open_invoices_count': 0
    }

    # Skip for static file requests (performance optimization)
    if request.path.startswith('/static'):
        return

    # Only run for logged-in users (login_required sets g.user)
    try:
        if not hasattr(g, 'user') or not g.user:
            return
    except Exception:
        return

    from src.adapters.sqlite.user_shift_repo import UserShiftRepository
    from src.adapters.sqlite.invoices_repo import InvoiceRepository

    try:
        shift_repo = UserShiftRepository()
        
        # Use single combined query instead of multiple calls
        active_shift, work_date, is_overdue, should_prompt = shift_repo.get_effective_shift_for_user(g.user['id'])
        user_shift = shift_repo.get_user_active_shift(g.user['id'])
        shift_started_at = user_shift.get('shift_started_at') if user_shift else None
        
        # Get open invoice count with a faster COUNT query
        from src.adapters.sqlite.core import get_db
        db = get_db()
        count_row = db.execute("SELECT COUNT(*) as cnt FROM invoices WHERE status = 'open'").fetchone()
        open_count = count_row['cnt'] if count_row else 0

        g.user_shift_status = {
            'active_shift': active_shift,
            'work_date': work_date,
            'shift_started_at': shift_started_at,
            'is_overdue': is_overdue,
            'should_prompt': should_prompt,
            'open_invoices_count': open_count
        }
    except Exception:
        # Do not break request processing on errors here; set sensible defaults
        g.user_shift_status = {
            'active_shift': None,
            'work_date': None,
            'is_overdue': False,
            'should_prompt': False,
            'open_invoices_count': 0
        }

@bp.route('/new', methods=('GET', 'POST'))
@login_required
def new_visit():
    """باز کردن فاکتور جدید برای بیمار (open new invoice)."""
    service = ReceptionService()
    
    if request.method == 'GET':
        # فقط تعرفه‌های ویزیت برای انتخاب نوع بیمه لازم است
        tariffs = service.get_active_visit_tariffs()
        supps = service.get_active_supplementary_insurances()
        return render_template('reception/new_visit.html', tariffs=tariffs, supplementary_insurances=supps)
    
    # POST: Create or find patient, then open invoice
    try:
        from src.common.validators import validate_iranian_national_id, validate_iranian_phone
        
        # Extract and validate form data
        name = request.form.get('name', '').strip()
        family_name = request.form.get('family_name', '').strip()
        phone = request.form.get('phone', '').strip()
        national_id = request.form.get('national_id', '').strip()
        is_foreign = request.form.get('is_foreign') == 'on'
        insurance_type = request.form.get('insurance_type', '').strip()
        supp_insurance = request.form.get('supplementary_insurance', '').strip()
        
        # Validation: Required fields
        if not name or not family_name:
            return jsonify({'error': 'نام و نام خانوادگی الزامی است'}), 400
        
        # Validation: Phone number format
        if phone and not validate_iranian_phone(phone):
            return jsonify({'error': 'شماره همراه باید 11 رقم و با 09 شروع شود'}), 400
        
        # Validation: National ID for Iranian patients
        if not is_foreign:
            if not national_id:
                return jsonify({'error': 'کدملی برای بیماران ایرانی اجباری است'}), 400
            if not validate_iranian_national_id(national_id):
                return jsonify({'error': 'کدملی وارد شده نامعتبر است'}), 400
        
        # Validation: Insurance type required
        if not insurance_type:
            return jsonify({'error': 'نوع بیمه الزامی است'}), 400
        
        # Validation: Supplementary insurance requires a base insurance (not آزاد)
        if supp_insurance and supp_insurance != 'ندارد' and insurance_type == 'آزاد':
            return jsonify({'error': 'بیمه تکمیلی فقط برای بیماران دارای بیمه پایه قابل انتخاب است'}), 400
        
        # Create or find patient, then open invoice
        invoice_id, patient_id = service.create_or_open_invoice(
            name=name,
            family_name=family_name,
            phone=phone if phone else None,
            national_id=national_id if not is_foreign else None,
            insurance_type=insurance_type,
            supplementary_insurance=supp_insurance if supp_insurance and supp_insurance != 'ندارد' else None,
            opened_by=g.user['username'],
            is_foreign=is_foreign
        )
        
        # ثبت لاگ
        full_name = f"{name} {family_name}"
        log_activity(
            action_type=ActionType.INVOICE_CREATE,
            action_category=ActionCategory.INVOICE,
            description=f'ایجاد فاکتور جدید برای {full_name}',
            invoice_id=invoice_id,
            patient_id=patient_id,
            patient_name=full_name,
            target_type='invoice',
            target_id=invoice_id,
            new_value=insurance_type
        )
        
        return jsonify({
            'success': True,
            'message': 'فاکتور با موفقیت باز شد',
            'invoice_id': invoice_id,
            'patient_id': patient_id
        }), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'خطا در باز کردن فاکتور: {str(e)}'}), 500

@bp.route('/search_patient')
@login_required
def search_patient():
    national_id = request.args.get('national_id')
    if not national_id:
        return jsonify({'found': False})
        
    service = ReceptionService()
    patient = service.get_patient_by_national_id(national_id)
    
    if patient:
        return jsonify({
            'found': True,
            'patient': {
                'id': patient.id,
                'name': patient.name,
                'family_name': patient.family_name,
                'phone_number': patient.phone_number,
                'birthdate': patient.birthdate,
                'gender': patient.gender,
                'insurance_type': patient.insurance_type
            }
        })
    return jsonify({'found': False})

@bp.route('/patients/list')
@login_required
def list_patients():
    """Return all patients or filtered by query (for modal search)."""
    from src.adapters.sqlite.core import get_db
    db = get_db()
    q = request.args.get('q','').strip()
    if q:
        rows = db.execute("""
            SELECT p.id, p.name, p.family_name, p.national_id, p.phone_number,
                   COALESCE(p.insurance_type, (
                     SELECT insurance_type FROM invoices i WHERE i.patient_id = p.id ORDER BY i.id DESC LIMIT 1
                   )) AS effective_insurance_type
            FROM patients p
            WHERE p.name LIKE ? OR p.family_name LIKE ? OR p.national_id LIKE ? OR p.phone_number LIKE ?
            ORDER BY p.id DESC
        """, (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        rows = db.execute("""
            SELECT p.id, p.name, p.family_name, p.national_id, p.phone_number,
                   COALESCE(p.insurance_type, (
                     SELECT insurance_type FROM invoices i WHERE i.patient_id = p.id ORDER BY i.id DESC LIMIT 1
                   )) AS effective_insurance_type
            FROM patients p
            ORDER BY p.id DESC
            LIMIT 1000
        """).fetchall()
    return jsonify({'patients': [
        {
            'id': r['id'],
            'full_name': f"{r['name']} {r['family_name']}",
            'national_id': r['national_id'],
            'phone_number': r['phone_number'],
            'insurance_type': r['effective_insurance_type']
        } for r in rows
    ]})

@bp.route('/patients/<int:patient_id>/history')
@login_required
def patient_history(patient_id: int):
    """Return full historical dossier for a patient (visits, injections, procedures, consumables, invoices)."""
    from src.adapters.sqlite.core import get_db
    db = get_db()
    # Patient basic info + effective insurance (fallback latest invoice)
    p_row = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not p_row:
        return jsonify({'error': 'بیمار یافت نشد'}), 404
    latest_inv = db.execute("SELECT insurance_type, supplementary_insurance FROM invoices WHERE patient_id=? ORDER BY id DESC LIMIT 1", (patient_id,)).fetchone()
    effective_insurance_type = p_row['insurance_type'] or (latest_inv['insurance_type'] if latest_inv else None)
    effective_supp = latest_inv['supplementary_insurance'] if latest_inv and latest_inv['supplementary_insurance'] else None
    # Invoices
    invoices = db.execute("""
        SELECT id, status, insurance_type, supplementary_insurance, total_amount, opened_at, closed_at
        FROM invoices WHERE patient_id = ? ORDER BY id DESC
    """, (patient_id,)).fetchall()
    # Visits
    visits = db.execute("""
        SELECT id, visit_date, doctor_name, price AS amount, status, invoice_id, insurance_type, supplementary_insurance
        FROM visits WHERE patient_id = ? ORDER BY visit_date DESC
    """, (patient_id,)).fetchall()
    # Injections with doctor/nurse names
    injections = db.execute("""
        SELECT i.id, i.injection_date, i.injection_type, i.unit_price, i.total_price, i.invoice_id, i.service_id, i.shift,
               COALESCE(inv_doc.full_name, shift_doc.full_name) AS doctor_name,
               COALESCE(inv_nurse.full_name, shift_nurse.full_name) AS nurse_name
        FROM injections i
        LEFT JOIN invoices inv ON inv.id = i.invoice_id
        LEFT JOIN medical_staff inv_doc ON inv_doc.id = inv.doctor_id
        LEFT JOIN medical_staff inv_nurse ON inv_nurse.id = inv.nurse_id
        LEFT JOIN shift_staff ss ON ss.work_date = i.work_date AND ss.shift = i.shift
        LEFT JOIN medical_staff shift_doc ON shift_doc.id = ss.doctor_id
        LEFT JOIN medical_staff shift_nurse ON shift_nurse.id = ss.nurse_id
        WHERE i.patient_id = ? ORDER BY i.injection_date DESC
    """, (patient_id,)).fetchall()
    # Procedures with performer info
    procedures = db.execute("""
        SELECT pr.id, pr.procedure_date, pr.procedure_type, pr.price, pr.invoice_id,
               pr.performer_type, pr.performer_id, pr.shift,
               COALESCE(inv_doc.full_name, shift_doc.full_name) AS doctor_name,
               COALESCE(inv_nurse.full_name, shift_nurse.full_name) AS nurse_name
        FROM procedures pr
        LEFT JOIN invoices inv ON inv.id = pr.invoice_id
        LEFT JOIN medical_staff inv_doc ON inv_doc.id = inv.doctor_id
        LEFT JOIN medical_staff inv_nurse ON inv_nurse.id = inv.nurse_id
        LEFT JOIN shift_staff ss ON ss.work_date = pr.work_date AND ss.shift = pr.shift
        LEFT JOIN medical_staff shift_doc ON shift_doc.id = ss.doctor_id
        LEFT JOIN medical_staff shift_nurse ON shift_nurse.id = ss.nurse_id
        WHERE pr.patient_id = ? ORDER BY pr.procedure_date DESC
    """, (patient_id,)).fetchall()
    # Consumables ledger
    consumables = db.execute("""
        SELECT id, usage_date, item_name, category, quantity, unit_price, total_cost, patient_provided, invoice_id
        FROM consumables_ledger WHERE patient_id = ? ORDER BY usage_date DESC
    """, (patient_id,)).fetchall()
    # Jalali formatting utility for Y-m-d H:M
    from jdatetime import datetime as jdt
    def fmt_jalali(ts):
        if not ts: return '—'
        try:
            # Assume ts is in format YYYY-MM-DD HH:MM:SS or ISO; slice
            date_part = str(ts).replace('T',' ')[:19]
            from datetime import datetime as gdt
            g = gdt.strptime(date_part, '%Y-%m-%d %H:%M:%S')
            j = jdt.fromgregorian(datetime=g)
            return f"{j.year}-{j.month:02d}-{j.day:02d} {g.hour:02d}:{g.minute:02d}"
        except Exception:
            return str(ts)
    def row_to_dict(row, jalali_fields=None):
        d = {k: row[k] for k in row.keys()}
        if jalali_fields:
            for f in jalali_fields:
                if f in d:
                    d[f] = fmt_jalali(d[f])
        return d
    return jsonify({
        'patient': {
            'id': p_row['id'],
            'full_name': p_row['full_name'],
            'national_id': p_row['national_id'],
            'phone_number': p_row['phone_number'],
            'insurance_type': p_row['insurance_type'],
            'effective_insurance_type': effective_insurance_type,
            'effective_supplementary_insurance': effective_supp,
            'insurance_expiry': p_row['insurance_expiry'],
            'birthdate': p_row['birthdate'],
            'gender': p_row['gender'],
            'address': p_row['address'],
            'is_foreign': bool(p_row['is_foreign'])
        },
        'invoices': [row_to_dict(r, ['opened_at','closed_at']) for r in invoices],
        'visits': [row_to_dict(r, ['visit_date']) for r in visits],
        'injections': [row_to_dict(r, ['injection_date']) for r in injections],
        'procedures': [row_to_dict(r, ['procedure_date']) for r in procedures],
        'consumables': [row_to_dict(r, ['usage_date']) for r in consumables]
    })

@bp.route('/invoice/open_existing', methods=['POST'])
@login_required
def open_invoice_existing():
    """Directly open a new invoice for an existing patient id (used in history modal)."""
    pid = request.form.get('patient_id', type=int)
    if not pid:
        return jsonify({'error': 'شناسه بیمار لازم است'}), 400
    from src.adapters.sqlite.core import get_db
    db = get_db()
    prow = db.execute("SELECT id, full_name, insurance_type FROM patients WHERE id = ?", (pid,)).fetchone()
    if not prow:
        return jsonify({'error': 'بیمار یافت نشد'}), 404
    patient_name = prow['full_name']
    # Determine effective insurance (patient record or latest invoice)
    if not prow['insurance_type']:
        inv = db.execute("SELECT insurance_type FROM invoices WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        insurance_type = inv['insurance_type'] if inv else 'آزاد'
    else:
        insurance_type = prow['insurance_type']
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    inv_repo = InvoiceRepository()
    invoice_id = inv_repo.open_invoice(pid, insurance_type, None, g.user['username'])
    
    # لاگ باز کردن فاکتور
    log_activity(
        action_type=ActionType.INVOICE_OPEN,
        action_category=ActionCategory.INVOICE,
        description=f'باز کردن فاکتور جدید از سابقه {patient_name}',
        invoice_id=invoice_id,
        patient_id=pid,
        patient_name=patient_name,
        target_type='invoice',
        target_id=invoice_id
    )
    
    return jsonify({'success': True, 'invoice_id': invoice_id})


@bp.route('/shift_staff', methods=['POST'])
@login_required
def set_shift_staff_route():
    """Set doctor/nurse for current shift (global, not per-invoice).
    
    This sets the staff for the current shift. All NEW items added to ANY invoice
    will use this staff. Existing items keep their original staff.
    """
    from src.adapters.sqlite.shift_staff_repo import ShiftStaffRepository
    from src.adapters.sqlite.core import get_db
    shift_repo = ShiftStaffRepository()

    doctor_id = request.form.get('doctor_id') or None
    nurse_id = request.form.get('nurse_id') or None

    if not doctor_id and not nurse_id:
        return jsonify({'error': 'حداقل یکی از پزشک یا پرستار را انتخاب کنید'}), 400

    # Update shift_staff table - this is the source of truth for current shift
    shift_repo.set_shift_staff(
        work_date=None,
        shift=None,
        doctor_id=int(doctor_id) if doctor_id else None,
        nurse_id=int(nurse_id) if nurse_id else None,
    )

    # DO NOT update invoices - staff is per-item, not per-invoice
    # Items will read from shift_staff when they are created
    
    db = get_db()
    
    # لاگ تنظیم کادر درمان
    doctor_name = None
    nurse_name = None
    if doctor_id:
        doc_row = db.execute("SELECT full_name FROM medical_staff WHERE id = ?", (int(doctor_id),)).fetchone()
        doctor_name = doc_row['full_name'] if doc_row else None
    if nurse_id:
        nurse_row = db.execute("SELECT full_name FROM medical_staff WHERE id = ?", (int(nurse_id),)).fetchone()
        nurse_name = nurse_row['full_name'] if nurse_row else None
    
    staff_desc = []
    if doctor_name:
        staff_desc.append(f'پزشک: {doctor_name}')
    if nurse_name:
        staff_desc.append(f'پرستار: {nurse_name}')
    
    log_activity(
        action_type=ActionType.SHIFT_STAFF_SET,
        action_category=ActionCategory.SHIFT,
        description=f'تنظیم کادر درمان شیفت - {" و ".join(staff_desc)}',
        target_type='shift_staff',
        new_value=f'doctor:{doctor_id},nurse:{nurse_id}'
    )
    
    return jsonify({'success': True})


@bp.route('/add_visit', methods=['POST'])
@login_required
def add_visit_to_invoice():
    """Add a new visit item to selected invoice (like desktop 'ثبت ویزیت جدید').
    
    Staff is read from current shift, NOT from invoice.
    """
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.shift_staff_repo import ShiftStaffRepository
    from src.adapters.sqlite.core import get_db

    invoice_id = request.form.get('invoice_id', type=int)
    if not invoice_id:
        return jsonify({'error': 'هیچ فاکتور فعالی انتخاب نشده است'}), 400

    invoice_repo = InvoiceRepository()
    invoice = invoice_repo.get_invoice_by_id(invoice_id)
    if not invoice:
        return jsonify({'error': 'فاکتور مورد نظر یافت نشد'}), 404

    # بررسی باز بودن فاکتور - نمی‌توان به فاکتور بسته ویزیت اضافه کرد
    if invoice.get('status') == 'closed':
        return jsonify({'error': 'فاکتور بسته شده است و امکان افزودن ویزیت وجود ندارد'}), 400

    # Get staff from CURRENT SHIFT (not from invoice)
    shift_repo = ShiftStaffRepository()
    current_shift = shift_repo._current_shift()
    staff = shift_repo.get_shift_staff(None, current_shift)
    
    if not staff or not staff.get('doctor_id'):
        return jsonify({'error': 'ابتدا کادر درمان شیفت را تعیین کنید'}), 400

    doctor_id = int(staff.get('doctor_id')) if staff.get('doctor_id') else None
    
    # Get doctor name
    db = get_db()
    doc_row = db.execute("SELECT full_name FROM medical_staff WHERE id = ?", (doctor_id,)).fetchone()
    doctor_name = doc_row['full_name'] if doc_row else None
    
    # گرفتن نام بیمار برای لاگ
    patient_row = db.execute("SELECT full_name FROM patients WHERE id = ?", (invoice['patient_id'],)).fetchone()
    patient_name = patient_row['full_name'] if patient_row else None

    # Use ReceptionService to calculate price and create visit
    service = ReceptionService()
    visit_id = service.add_visit(
        patient_id=invoice['patient_id'],
        insurance_type=invoice.get('insurance_type'),
        supplementary_insurance=invoice.get('supplementary_insurance'),
        reception_user=g.user['username'],
        doctor_name=doctor_name,
        notes='',
        invoice_id=invoice_id,
        doctor_id=doctor_id,  # From shift, not invoice
    )

    # Update invoice totals
    invoice_repo.update_invoice_totals(invoice_id)
    
    # لاگ ثبت ویزیت
    log_activity(
        action_type=ActionType.VISIT_ADD,
        action_category=ActionCategory.VISIT,
        description=f'ثبت ویزیت برای {patient_name} - پزشک: {doctor_name}',
        invoice_id=invoice_id,
        patient_id=invoice['patient_id'],
        patient_name=patient_name,
        target_type='visit',
        target_id=visit_id,
        target_name=doctor_name
    )

    return jsonify({'success': True, 'visit_id': visit_id})

@bp.route('/item/payment', methods=['POST'])
@login_required
def set_item_payment():
    """Set payment status/type for an item then return updated financials."""
    from src.adapters.sqlite.payments_repo import InvoiceItemPaymentRepository
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.core import get_db
    from src.adapters.sqlite.invoices_repo import InvoiceRepository

    invoice_id = request.form.get('invoice_id', type=int)
    item_type = request.form.get('item_type', type=str)
    item_id = request.form.get('item_id', type=int)
    payment_type = request.form.get('payment_type') or None
    is_paid = request.form.get('is_paid') == 'true'

    if not invoice_id or not item_type or not item_id:
        return jsonify({'error': 'پارامترهای لازم ناقص هستند'}), 400
    
    # بررسی وضعیت فاکتور - نمی‌توان پرداخت فاکتور بسته را تغییر داد
    inv_repo = InvoiceRepository()
    invoice_check = inv_repo.get_invoice_by_id(invoice_id)
    if not invoice_check:
        return jsonify({'error': 'فاکتور یافت نشد'}), 404
    if invoice_check.get('status') == 'closed':
        return jsonify({'error': 'فاکتور بسته شده است و امکان تغییر وضعیت پرداخت وجود ندارد'}), 400

    # گرفتن اطلاعات برای لاگ
    db = get_db()
    invoice = db.execute("SELECT i.patient_id, p.full_name FROM invoices i JOIN patients p ON i.patient_id = p.id WHERE i.id = ?", (invoice_id,)).fetchone()
    patient_name = invoice['full_name'] if invoice else None
    patient_id = invoice['patient_id'] if invoice else None

    pay_repo = InvoiceItemPaymentRepository()
    pay_repo.set_payment(invoice_id, item_type, item_id, payment_type, is_paid)

    inv_repo.update_invoice_totals(invoice_id)
    financials = inv_repo.get_financials(invoice_id)
    
    # لاگ تغییر وضعیت پرداخت
    type_names = {'visit': 'ویزیت', 'injection': 'تزریق', 'procedure': 'کار عملی', 'consumable': 'مصرفی'}
    status = 'پرداخت شده' if is_paid else 'پرداخت نشده'
    payment_display = {'cash': 'نقد', 'insurance': 'بیمه', 'supplementary': 'تکمیلی'}.get(payment_type, payment_type or '-')
    log_activity(
        action_type=ActionType.ITEM_PAYMENT_SET,
        action_category=ActionCategory.INVOICE,
        description=f'تغییر وضعیت پرداخت {type_names.get(item_type, item_type)} به {status} ({payment_display})',
        invoice_id=invoice_id,
        patient_id=patient_id,
        patient_name=patient_name,
        target_type=item_type,
        target_id=item_id,
        new_value=f'{payment_type}:{is_paid}'
    )
    
    return jsonify({'success': True, 'financials': financials})


@bp.route('/item/settle_all', methods=['POST'])
@login_required
def settle_all_items():
    """Set all items in an invoice as paid with the given payment type."""
    from src.adapters.sqlite.payments_repo import InvoiceItemPaymentRepository
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.core import get_db

    invoice_id = request.form.get('invoice_id', type=int)
    payment_type = request.form.get('payment_type') or None

    if not invoice_id:
        return jsonify({'error': 'پارامترهای لازم ناقص هستند'}), 400

    inv_repo = InvoiceRepository()
    invoice_check = inv_repo.get_invoice_by_id(invoice_id)
    if not invoice_check:
        return jsonify({'error': 'فاکتور یافت نشد'}), 404
    if invoice_check.get('status') == 'closed':
        return jsonify({'error': 'فاکتور بسته شده است و امکان تغییر وضعیت پرداخت وجود ندارد'}), 400

    # Get all items for this invoice
    items = inv_repo.get_invoice_items(invoice_id)
    pay_repo = InvoiceItemPaymentRepository()
    # Set payment for each item
    for it in items:
        try:
            pay_repo.set_payment(invoice_id, it.get('type'), it.get('id'), payment_type, True)
        except Exception:
            # continue on failure for individual items
            continue

    # Update totals and return new financials
    inv_repo.update_invoice_totals(invoice_id)
    financials = inv_repo.get_financials(invoice_id)

    # Log activity
    db = get_db()
    invoice = db.execute("SELECT i.patient_id, p.full_name FROM invoices i JOIN patients p ON i.patient_id = p.id WHERE i.id = ?", (invoice_id,)).fetchone()
    patient_name = invoice['full_name'] if invoice else None
    patient_id = invoice['patient_id'] if invoice else None
    payment_display = {'cash': 'نقد', 'card': 'کارت', 'insurance': 'بیمه', 'supplementary': 'تکمیلی'}.get(payment_type, payment_type or '-')
    log_activity(
        action_type=ActionType.ITEM_PAYMENT_SET,
        action_category=ActionCategory.INVOICE,
        description=f'تسویه یکجا با روش {payment_display}',
        invoice_id=invoice_id,
        patient_id=patient_id,
        patient_name=patient_name,
        new_value=f'{payment_type}:all'
    )

    return jsonify({'success': True, 'financials': financials})

@bp.route('/item/delete', methods=['POST'])
@login_required
def delete_item():
    """Delete an item from invoice (visit/injection/procedure/consumable)."""
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    invoice_id = request.form.get('invoice_id', type=int)
    item_type = request.form.get('item_type')
    item_id = request.form.get('item_id', type=int)
    if not invoice_id or not item_type or not item_id:
        return jsonify({'error': 'پارامترهای لازم ناقص هستند'}), 400
    
    # بررسی وضعیت فاکتور - نمی‌توان از فاکتور بسته آیتم حذف کرد
    inv_repo = InvoiceRepository()
    invoice_check = inv_repo.get_invoice_by_id(invoice_id)
    if not invoice_check:
        return jsonify({'error': 'فاکتور یافت نشد'}), 404
    if invoice_check.get('status') == 'closed':
        return jsonify({'error': 'فاکتور بسته شده است و امکان حذف آیتم وجود ندارد'}), 400
    
    db = None
    try:
        from src.adapters.sqlite.core import get_db
        db = get_db()
        table_map = {
            'visit': 'visits',
            'injection': 'injections',
            'procedure': 'procedures',
            'consumable': 'consumables_ledger'
        }
        table = table_map.get(item_type)
        if not table:
            return jsonify({'error': 'نوع آیتم نامعتبر'}), 400
        
        # گرفتن اطلاعات قبل از حذف برای لاگ
        row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
        if not row or row['invoice_id'] != invoice_id:
            return jsonify({'error': 'آیتم یافت نشد برای این فاکتور'}), 404
        
        # گرفتن نام بیمار
        invoice = db.execute("SELECT p.full_name, i.patient_id FROM invoices i JOIN patients p ON i.patient_id = p.id WHERE i.id = ?", (invoice_id,)).fetchone()
        patient_name = invoice['full_name'] if invoice else None
        patient_id = invoice['patient_id'] if invoice else None
        
        # حذف آیتم
        db.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
        db.execute("DELETE FROM invoice_item_payments WHERE invoice_id = ? AND item_type = ? AND item_id = ?", (invoice_id, item_type, item_id))
        db.commit()
        
        # لاگ حذف
        type_names = {'visit': 'ویزیت', 'injection': 'تزریق', 'procedure': 'کار عملی', 'consumable': 'مصرفی'}
        action_types = {
            'visit': ActionType.VISIT_DELETE,
            'injection': ActionType.INJECTION_DELETE,
            'procedure': ActionType.PROCEDURE_DELETE,
            'consumable': ActionType.CONSUMABLE_DELETE
        }
        categories = {
            'visit': ActionCategory.VISIT,
            'injection': ActionCategory.INJECTION,
            'procedure': ActionCategory.PROCEDURE,
            'consumable': ActionCategory.CONSUMABLE
        }
        log_activity(
            action_type=action_types.get(item_type, ActionType.ITEM_DELETE),
            action_category=categories.get(item_type, ActionCategory.INVOICE),
            description=f'حذف {type_names.get(item_type, item_type)} از فاکتور',
            invoice_id=invoice_id,
            patient_id=patient_id,
            patient_name=patient_name,
            target_type=item_type,
            target_id=item_id,
            amount=row.get('amount', 0) if hasattr(row, 'get') else 0
        )
        
        inv_repo = InvoiceRepository()
        inv_repo.update_invoice_totals(invoice_id)
        financials = inv_repo.get_financials(invoice_id)
        return jsonify({'success': True, 'financials': financials})
    except Exception as e:
        if db:
            db.rollback()
        return jsonify({'error': f'خطا در حذف آیتم: {str(e)}'}), 500

@bp.route('/invoice/close', methods=['POST'])
@login_required
def close_invoice():
    """Close an invoice (prevent further item additions, enable ledger inclusion)."""
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.core import get_db
    invoice_id = request.form.get('invoice_id', type=int)
    if not invoice_id:
        return jsonify({'error': 'شناسه فاکتور لازم است'}), 400
    
    repo = InvoiceRepository()

    # گرفتن اطلاعات قبل از بستن برای لاگ
    db = get_db()
    invoice = db.execute("SELECT i.*, p.full_name FROM invoices i JOIN patients p ON i.patient_id = p.id WHERE i.id = ?", (invoice_id,)).fetchone()

    # Ensure every invoice item is marked as paid before closing
    from src.adapters.sqlite.payments_repo import InvoiceItemPaymentRepository
    pay_repo = InvoiceItemPaymentRepository()
    items = repo.get_invoice_items(invoice_id)
    unpaid_items = []
    for it in items:
        p = pay_repo.get_item_payment(invoice_id, it.get('type'), it.get('id'))
        if not p or int(p.get('is_paid', 0)) != 1:
            unpaid_items.append({'type': it.get('type'), 'id': it.get('id'), 'description': it.get('description')})
    if unpaid_items:
        # Return a clear error listing count of unpaid items and the items themselves
        return jsonify({
            'error': f'امکان بستن فاکتور وجود ندارد — {len(unpaid_items)} آیتم تسویه نشده است.',
            'unpaid_items': unpaid_items
        }), 400

    success = repo.close_invoice(invoice_id, g.user['username'])
    if not success:
        return jsonify({'error': 'بستن فاکتور ناموفق بود یا فاکتور قبلاً بسته شده'}), 400
    
    financials = repo.get_financials(invoice_id)
    
    # لاگ بستن فاکتور
    log_activity(
        action_type=ActionType.INVOICE_CLOSE,
        action_category=ActionCategory.INVOICE,
        description=f'بستن فاکتور {invoice["full_name"]}',
        invoice_id=invoice_id,
        patient_id=invoice['patient_id'],
        patient_name=invoice['full_name'],
        target_type='invoice',
        target_id=invoice_id,
        amount=financials.get('total', 0) if financials else 0
    )
    
    return jsonify({'success': True, 'financials': financials})

@bp.route('/nursing', methods=['GET'])
@login_required
def nursing_form():
    """Route deprecated -> redirect to new injections UI."""
    invoice_id = request.args.get('invoice_id', type=int)
    return redirect(url_for('reception.injections_new', invoice_id=invoice_id))

@bp.route('/nursing', methods=['POST'])
@login_required
def nursing_submit():
    """ثبت خدمات پرستاری انتخاب شده و ایجاد رکورد تزریق برای هر واحد."""
    from src.adapters.sqlite.injections_repo import InjectionRepository
    from src.adapters.sqlite.nursing_services_repo import NursingServicesRepository
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    inj_repo = InjectionRepository()
    service_repo = NursingServicesRepository()
    invoice_id = request.form.get('invoice_id', type=int)
    if not invoice_id:
        return jsonify({'error': 'شناسه فاکتور الزامی است'}), 400
    inv_repo = InvoiceRepository()
    invoice = inv_repo.get_invoice_by_id(invoice_id)
    if not invoice:
        return jsonify({'error': 'فاکتور یافت نشد'}), 404
    
    # بررسی باز بودن فاکتور - نمی‌توان به فاکتور بسته خدمات اضافه کرد
    if invoice.get('status') == 'closed':
        return jsonify({'error': 'فاکتور بسته شده است و امکان افزودن خدمات وجود ندارد'}), 400

    # Ensure shift staff locked (doctor or nurse)
    from src.adapters.sqlite.shift_staff_repo import ShiftStaffRepository
    shift_repo = ShiftStaffRepository()
    current_shift = shift_repo._current_shift()
    staff = shift_repo.get_shift_staff(None, current_shift)
    if not staff or (not staff.get('doctor_id') and not staff.get('nurse_id')):
        return jsonify({'error': 'ابتدا کادر درمان شیفت را ثبت و قفل کنید'}), 400

    pairs = request.form.get('services')  # "12:2,7:1" -> service_id:qty
    consumables_raw = request.form.get('consumables')  # name|category|qty|unit_price, separated by commas
    notes = request.form.get('notes', '').strip()
    if not pairs and not consumables_raw:
        return jsonify({'error': 'هیچ موردی انتخاب نشده است'}), 400
    created_ids = []
    from src.adapters.sqlite.consumables_repo import ConsumableLedgerRepository
    cons_repo = ConsumableLedgerRepository()
    try:
        if pairs:
            for part in pairs.split(','):
                part = part.strip()
                if not part:
                    continue
                if ':' not in part:
                    continue
                sid_str, qty_str = part.split(':')
                sid = int(sid_str)
                qty = int(qty_str)
                service = service_repo.get(sid)
                if not service:
                    return jsonify({'error': f'خدمت {sid} یافت نشد'}), 400
                for _ in range(qty):
                    inj_id = inj_repo.add_injection(
                        patient_id=invoice['patient_id'],
                        injection_type=service['service_name'],
                        count=1,
                        unit_price=service['unit_price'],
                        reception_user=g.user['username'],
                        invoice_id=invoice_id,
                        notes=notes,
                        doctor_id=staff.get('doctor_id'),
                        nurse_id=staff.get('nurse_id')
                    )
                    created_ids.append(inj_id)
        consumable_count = 0
        if consumables_raw:
            for row in consumables_raw.split(','):
                row = row.strip()
                if not row:
                    continue
                # name|category|qty|unit_price
                parts = row.split('|')
                if len(parts) != 4:
                    return jsonify({'error': 'فرمت مصرفی نامعتبر'}), 400
                # support optional patient_provided flag as 5th part (backwards-compatible)
                name = parts[0]
                category = parts[1]
                qty_str = parts[2]
                price_str = parts[3]
                patient_provided = False
                if len(parts) >= 5:
                    pflag = parts[4].strip()
                    patient_provided = pflag in ('1', 'true', 'True', 'yes', 'YES')
                try:
                    qty = float(qty_str)
                    unit_price = float(price_str)
                except ValueError:
                    return jsonify({'error': 'مقادیر عددی مصرفی نامعتبر'}), 400
                # attach to invoice so it appears in the invoice view; mark patient_provided for reports
                invoice_for_insert = invoice_id
                cons_repo.add_consumable(
                    patient_id=invoice['patient_id'],
                    item_name=name,
                    category=category,
                    quantity=qty,
                    unit_price=unit_price,
                    reception_user=g.user['username'],
                    invoice_id=invoice_for_insert,
                    notes=notes,
                    patient_provided=1 if patient_provided else 0,
                    doctor_id=staff.get('doctor_id'),
                    nurse_id=staff.get('nurse_id')
                )
                consumable_count += 1
        inv_repo.update_invoice_totals(invoice_id)
        financials = inv_repo.get_financials(invoice_id)
        
        # لاگ ثبت خدمات پرستاری
        if created_ids or consumable_count:
            from src.adapters.sqlite.core import get_db
            db = get_db()
            patient_row = db.execute("SELECT full_name FROM patients WHERE id = ?", (invoice['patient_id'],)).fetchone()
            patient_name = patient_row['full_name'] if patient_row else None
            
            desc_parts = []
            if created_ids:
                desc_parts.append(f'{len(created_ids)} تزریق')
            if consumable_count:
                desc_parts.append(f'{consumable_count} مصرفی')
            
            log_activity(
                action_type=ActionType.INJECTION_ADD,
                action_category=ActionCategory.INJECTION,
                description=f'ثبت {" و ".join(desc_parts)} برای {patient_name}',
                invoice_id=invoice_id,
                patient_id=invoice['patient_id'],
                patient_name=patient_name,
                target_type='injection',
                target_id=created_ids[0] if created_ids else None
            )
        
        return jsonify({'success': True, 'created_services': len(created_ids), 'created_consumables': consumable_count, 'financials': financials})
    except Exception as e:
        return jsonify({'error': f'خطا در ثبت: {str(e)}'}), 500

@bp.route('/injections/new', methods=['GET'])
@login_required
def injections_new():
    """صفحه جدید خدمات پرستاری (تزریقات) - نسخه مدرن."""
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.nursing_services_repo import NursingServicesRepository
    from src.adapters.sqlite.consumable_tariffs_repo import ConsumableTariffsRepository
    invoice_id = request.args.get('invoice_id', type=int)
    inv_repo = InvoiceRepository()
    invoice = inv_repo.get_invoice_by_id(invoice_id) if invoice_id else None
    ns_repo = NursingServicesRepository()
    services = ns_repo.list_active()
    ct_repo = ConsumableTariffsRepository()
    supplies = ct_repo.list_active('supply')
    drugs = ct_repo.list_active('drug')
    return render_template('reception/injections_new.html', invoice=invoice, services=services, supplies=supplies, drugs=drugs)

@bp.route('/injections', methods=['POST'])
@login_required
def injections_submit():
    """JSON endpoint submit nursing services + consumables + drugs.
    
    Staff is read from current shift, NOT from invoice.
    """
    if not request.is_json:
        return jsonify({'error': 'ارسال باید JSON باشد'}), 400
    data = request.get_json(silent=True) or {}
    invoice_id = data.get('invoice_id')
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.shift_staff_repo import ShiftStaffRepository
    inv_repo = InvoiceRepository()
    invoice = inv_repo.get_invoice_by_id(invoice_id) if invoice_id else None
    if not invoice:
        return jsonify({'error': 'فاکتور یافت نشد'}), 404
    
    # بررسی باز بودن فاکتور - نمی‌توان به فاکتور بسته خدمات اضافه کرد
    if invoice.get('status') == 'closed':
        return jsonify({'error': 'فاکتور بسته شده است و امکان افزودن خدمات وجود ندارد'}), 400
    
    # Get staff from CURRENT SHIFT (not from invoice)
    shift_repo = ShiftStaffRepository()
    current_shift = shift_repo._current_shift()
    staff = shift_repo.get_shift_staff(None, current_shift)
    
    if not staff or (not staff.get('doctor_id') and not staff.get('nurse_id')):
        return jsonify({'error': 'ابتدا کادر درمان شیفت را تعیین کنید'}), 400
    
    doctor_id = staff.get('doctor_id')
    nurse_id = staff.get('nurse_id')
    
    services_payload = data.get('services', [])
    consumables_payload = data.get('consumables', [])
    notes = (data.get('notes') or '').strip()
    if not services_payload and not consumables_payload:
        return jsonify({'error': 'هیچ موردی ارسال نشد'}), 400
    from src.adapters.sqlite.injections_repo import InjectionRepository
    from src.adapters.sqlite.nursing_services_repo import NursingServicesRepository
    from src.adapters.sqlite.consumables_repo import ConsumableLedgerRepository
    inj_repo = InjectionRepository(); ns_repo = NursingServicesRepository(); cons_repo = ConsumableLedgerRepository()
    created_services = 0
    for svc in services_payload:
        sid = svc.get('id'); qty = int(svc.get('qty', 0))
        if not sid or qty < 1: continue
        service = ns_repo.get(int(sid))
        if not service: return jsonify({'error': f'خدمت {sid} نامعتبر'}), 400
        for _ in range(qty):
            inj_repo.add_injection(
                patient_id=invoice['patient_id'],
                injection_type=service['service_name'],
                count=1,
                unit_price=service['unit_price'],
                reception_user=g.user['username'],
                invoice_id=invoice_id,
                notes=notes,
                service_id=service['id'],
                doctor_id=doctor_id,
                nurse_id=nurse_id
            ); created_services += 1
    created_consumables = 0
    for item in consumables_payload:
        name = (item.get('name') or '').strip();
        if not name: continue
        qty = float(item.get('qty', 0) or 0); unit_price = float(item.get('unit_price', 0) or 0)
        cat = item.get('category') in ['drug','supply'] and item.get('category') or 'supply'
        patient_provided = bool(item.get('patient_provided'))
        is_exception = bool(item.get('is_exception'))
        if qty <= 0: continue
        # Always attach consumables to the invoice so they appear in the invoice view.
        # We still mark `patient_provided` so reports can exclude them, but items
        # marked `is_exception` should still surface in manager reports.
        invoice_for_insert = invoice_id
        cons_repo.add_consumable(
            patient_id=invoice['patient_id'], item_name=name, category=cat, quantity=qty,
            unit_price=unit_price, reception_user=g.user['username'], invoice_id=invoice_for_insert,
            notes=notes, patient_provided=patient_provided, is_exception=is_exception,
            doctor_id=doctor_id, nurse_id=nurse_id
        ); created_consumables += 1
    inv_repo.update_invoice_totals(invoice_id); financials = inv_repo.get_financials(invoice_id)
    
    # لاگ ثبت تزریقات
    if created_services or created_consumables:
        from src.adapters.sqlite.core import get_db
        db = get_db()
        patient_row = db.execute("SELECT full_name FROM patients WHERE id = ?", (invoice['patient_id'],)).fetchone()
        patient_name = patient_row['full_name'] if patient_row else None
        
        desc_parts = []
        if created_services:
            desc_parts.append(f'{created_services} خدمت پرستاری')
        if created_consumables:
            desc_parts.append(f'{created_consumables} مصرفی')
        
        log_activity(
            action_type=ActionType.INJECTION_ADD,
            action_category=ActionCategory.INJECTION,
            description=f'ثبت {" و ".join(desc_parts)} برای {patient_name}',
            invoice_id=invoice_id,
            patient_id=invoice['patient_id'],
            patient_name=patient_name,
            target_type='injection'
        )
    
    return jsonify({'success': True, 'created_services': created_services, 'created_consumables': created_consumables, 'financials': financials})

@bp.route('/nursing/redirect', methods=['GET'])
@login_required
def nursing_redirect():
    """Redirect old nursing path to new injections UI (backward compatibility)."""
    invoice_id = request.args.get('invoice_id', type=int)
    return redirect(url_for('reception.injections_new', invoice_id=invoice_id))

@bp.route('/procedures/new', methods=['GET'])
@login_required
def procedures_new():
    """UI for adding procedure items (manual) + consumables."""
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.consumable_tariffs_repo import ConsumableTariffsRepository
    invoice_id = request.args.get('invoice_id', type=int)
    inv_repo = InvoiceRepository(); invoice = inv_repo.get_invoice_by_id(invoice_id) if invoice_id else None
    ct_repo = ConsumableTariffsRepository(); supplies = ct_repo.list_active('supply'); drugs = ct_repo.list_active('drug')
    return render_template('reception/procedures_new.html', invoice=invoice, supplies=supplies, drugs=drugs)

@bp.route('/procedures', methods=['POST'])
@login_required
def procedures_submit():
    """Submit procedure items + consumables.
    
    Staff is read from current shift, NOT from invoice.
    """
    if not request.is_json:
        return jsonify({'error': 'ارسال باید JSON باشد'}), 400
    data = request.get_json(silent=True) or {}
    invoice_id = data.get('invoice_id')
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.adapters.sqlite.shift_staff_repo import ShiftStaffRepository
    inv_repo = InvoiceRepository(); invoice = inv_repo.get_invoice_by_id(invoice_id) if invoice_id else None
    if not invoice:
        return jsonify({'error': 'فاکتور یافت نشد'}), 404
    
    # بررسی باز بودن فاکتور - نمی‌توان به فاکتور بسته عملیات اضافه کرد
    if invoice.get('status') == 'closed':
        return jsonify({'error': 'فاکتور بسته شده است و امکان افزودن عملیات وجود ندارد'}), 400
    
    # Get staff from CURRENT SHIFT (not from invoice)
    shift_repo = ShiftStaffRepository()
    current_shift = shift_repo._current_shift()
    staff = shift_repo.get_shift_staff(None, current_shift)
    
    if not staff or (not staff.get('doctor_id') and not staff.get('nurse_id')):
        return jsonify({'error': 'ابتدا کادر درمان شیفت را تعیین کنید'}), 400
    
    doctor_id = staff.get('doctor_id')
    nurse_id = staff.get('nurse_id')
    
    procedures_payload = data.get('procedures', [])  # [{name, unit_price, qty, performer_type}]
    consumables_payload = data.get('consumables', [])  # [{name, qty, unit_price, patient_provided, category}]
    notes = (data.get('notes') or '').strip()
    if not procedures_payload and not consumables_payload:
        return jsonify({'error': 'هیچ موردی ارسال نشد'}), 400
    from src.adapters.sqlite.procedures_repo import ProcedureRepository
    from src.adapters.sqlite.consumables_repo import ConsumableLedgerRepository
    proc_repo = ProcedureRepository(); cons_repo = ConsumableLedgerRepository()
    created_procs = 0; created_cons = 0
    
    # Add manual procedures
    for pr in procedures_payload:
        name = (pr.get('name') or '').strip(); qty = int(pr.get('qty',0)); unit_price = float(pr.get('unit_price',0) or 0)
        performer_type = (pr.get('performer_type') or '').strip().lower()
        if performer_type not in ['doctor','nurse']:
            performer_type = 'doctor' if doctor_id else 'nurse'
        performer_id = doctor_id if performer_type=='doctor' else nurse_id
        # فقط ID انجام‌دهنده واقعی را ثبت کن - برای جلوگیری از شمارش دوگانه در گزارش‌ها
        actual_doctor_id = doctor_id if performer_type == 'doctor' else None
        actual_nurse_id = nurse_id if performer_type == 'nurse' else None
        if not name or unit_price <= 0 or qty < 1: continue
        for _ in range(qty):
            proc_repo.add_procedure(
                patient_id=invoice['patient_id'],
                procedure_type=name,
                price=unit_price,
                reception_user=g.user['username'],
                invoice_id=invoice_id,
                notes=notes,
                performer_type=performer_type,
                performer_id=performer_id,
                doctor_id=actual_doctor_id,
                nurse_id=actual_nurse_id
            ); created_procs += 1
    # Add consumables ledger entries
    for item in consumables_payload:
        name = (item.get('name') or '').strip(); qty = float(item.get('qty',0) or 0); unit_price = float(item.get('unit_price',0) or 0)
        category = item.get('category') in ['drug','supply'] and item.get('category') or 'supply'
        patient_provided = bool(item.get('patient_provided'))
        is_exception = bool(item.get('is_exception'))
        if not name or qty <= 0: continue
        # attach to invoice so it appears in the invoice view; mark patient_provided and is_exception for reports
        invoice_for_insert = invoice_id
        cons_repo.add_consumable(
            patient_id=invoice['patient_id'], item_name=name, category=category, quantity=qty,
            unit_price=unit_price, reception_user=g.user['username'], invoice_id=invoice_for_insert,
            notes=notes, patient_provided=patient_provided, is_exception=is_exception,
            doctor_id=doctor_id, nurse_id=nurse_id
        ); created_cons += 1
    inv_repo.update_invoice_totals(invoice_id); financials = inv_repo.get_financials(invoice_id)
    
    # لاگ ثبت کار عملی
    if created_procs or created_cons:
        from src.adapters.sqlite.core import get_db
        db = get_db()
        patient_row = db.execute("SELECT full_name FROM patients WHERE id = ?", (invoice['patient_id'],)).fetchone()
        patient_name = patient_row['full_name'] if patient_row else None
        
        desc_parts = []
        if created_procs:
            desc_parts.append(f'{created_procs} کار عملی')
        if created_cons:
            desc_parts.append(f'{created_cons} مصرفی')
        
        log_activity(
            action_type=ActionType.PROCEDURE_ADD,
            action_category=ActionCategory.PROCEDURE,
            description=f'ثبت {" و ".join(desc_parts)} برای {patient_name}',
            invoice_id=invoice_id,
            patient_id=invoice['patient_id'],
            patient_name=patient_name,
            target_type='procedure'
        )
    
    return jsonify({'success': True, 'created_procedures': created_procs, 'created_consumables': created_cons, 'financials': financials})

@bp.route('/nursing_ledger', methods=['GET'])
@login_required
def nursing_ledger():
    """نمایش دفتر خدمات پرستاری فقط برای شیفت جاری."""
    from src.adapters.sqlite.core import get_db
    from src.common.utils import get_current_shift_window
    db = get_db()

    # Determine current shift and its time window (database stores Iran local time)
    shift, start_dt, end_dt = get_current_shift_window()

    # Query injections for current shift/window - read doctor/nurse from item itself
    rows = db.execute(
        '''SELECT i.id, i.injection_date, i.injection_type, i.count, i.unit_price, i.total_price, i.notes,
                  p.full_name AS patient_name,
                  doc.full_name AS doctor_name,
                  nurse.full_name AS nurse_name
           FROM injections i
           JOIN patients p ON p.id = i.patient_id
           LEFT JOIN medical_staff doc ON doc.id = i.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = i.nurse_id
           WHERE i.work_date = ? AND i.shift = ?
           ORDER BY i.injection_date DESC''',
        (g.user_shift_status['work_date'], g.user_shift_status['active_shift'])
    ).fetchall()

    injections = [dict(r) for r in rows]
    total_services = len(injections)
    total_amount = sum(r['total_price'] or 0 for r in injections)

    return render_template('reception/nursing_ledger.html',
                           shift=shift,
                           start=start_dt,
                           end=end_dt,
                           injections=injections,
                           total_services=total_services,
                           total_amount=total_amount)

@bp.route('/procedures_ledger', methods=['GET'])
@login_required
def procedures_ledger():
    """دفتر کارهای عملی فقط شیفت جاری."""
    from src.adapters.sqlite.core import get_db
    db = get_db()

    # Shift determination reused (database stores Iran local time)
    from src.common.utils import get_current_shift_window
    shift, start_dt, end_dt = get_current_shift_window()

    # Read doctor/nurse from item itself
    rows = db.execute(
        '''SELECT pr.id, pr.procedure_date, pr.procedure_type, pr.price, pr.notes,
                  p.full_name AS patient_name, pr.performer_type,
                  doc.full_name AS doctor_name,
                  nurse.full_name AS nurse_name
           FROM procedures pr
           JOIN patients p ON p.id = pr.patient_id
           LEFT JOIN medical_staff doc ON doc.id = pr.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = pr.nurse_id
           WHERE pr.work_date = ? AND pr.shift = ?
           ORDER BY pr.procedure_date DESC''',
        (g.user_shift_status['work_date'], g.user_shift_status['active_shift'])
    ).fetchall()
    procedures = [dict(r) for r in rows]
    total_count = len(procedures)
    total_amount = sum(r['price'] or 0 for r in procedures)
    return render_template('reception/procedures_ledger.html',
                           shift=shift,
                           start=start_dt,
                           end=end_dt,
                           procedures=procedures,
                           total_count=total_count,
                           total_amount=total_amount)

@bp.route('/ledgers', methods=['GET'])
@login_required
def combined_ledgers():
    """صفحه یکپارچه دفاتر با تب ها (پرستاری، کارهای عملی، مصرفی‌ها، ویزیت‌ها، فاکتورها)."""
    from src.adapters.sqlite.core import get_db
    db = get_db()

    # Helper shift window (database stores Iran local time)
    from src.common.utils import get_current_shift_window
    shift, start_dt, end_dt = get_current_shift_window()

    # Nursing (injections) - only from closed invoices, read doctor/nurse from item itself
    injections = db.execute(
        '''SELECT i.id, i.injection_date, i.injection_type, i.count, i.unit_price, i.total_price, i.notes,
                  p.full_name AS patient_name,
                  doc.full_name AS doctor_name,
                  nurse.full_name AS nurse_name,
                  i.invoice_id
           FROM injections i
           JOIN patients p ON p.id = i.patient_id
           JOIN invoices inv ON inv.id = i.invoice_id AND inv.status = 'closed'
           LEFT JOIN medical_staff doc ON doc.id = i.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = i.nurse_id
           WHERE i.work_date = ? AND i.shift = ? ORDER BY i.injection_date DESC''',
        (g.user_shift_status.get('work_date'), g.user_shift_status.get('active_shift'))
    ).fetchall()
    injections_list = [dict(r) for r in injections]

    # Nursing injections related to doctor (only invoices that have at least one visit item)
    injections_doctor = db.execute(
        '''SELECT i.id, i.injection_date, i.injection_type, i.count, i.unit_price, i.total_price, i.notes,
                  p.full_name AS patient_name,
                  doc.full_name AS doctor_name,
                  nurse.full_name AS nurse_name,
                  i.invoice_id
           FROM injections i
           JOIN patients p ON p.id = i.patient_id
           JOIN invoices inv ON inv.id = i.invoice_id AND inv.status = 'closed'
           LEFT JOIN medical_staff doc ON doc.id = i.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = i.nurse_id
           WHERE i.work_date = ?
             AND i.shift = ?
             AND EXISTS (
                 SELECT 1 FROM visits v
                 WHERE v.invoice_id = i.invoice_id
             )
           ORDER BY i.injection_date DESC''',
        (g.user_shift_status.get('work_date'), g.user_shift_status.get('active_shift'))
    ).fetchall()
    injections_doctor_list = [dict(r) for r in injections_doctor]

    # Procedures - only from closed invoices, read doctor/nurse from item itself
    procedures = db.execute(
        '''SELECT pr.id, pr.procedure_date, pr.procedure_type, pr.price, pr.notes,
                  p.full_name AS patient_name, pr.performer_type,
                  doc.full_name AS doctor_name,
                  nurse.full_name AS nurse_name
           FROM procedures pr
           JOIN patients p ON p.id = pr.patient_id
           JOIN invoices inv ON inv.id = pr.invoice_id AND inv.status = 'closed'
           LEFT JOIN medical_staff doc ON doc.id = pr.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = pr.nurse_id
           WHERE pr.work_date = ? AND pr.shift = ? ORDER BY pr.procedure_date DESC''',
        (g.user_shift_status.get('work_date'), g.user_shift_status.get('active_shift'))
    ).fetchall()
    procedures_list = [dict(r) for r in procedures]

    # Consumables (supplies & drugs) - only from closed invoices
    consumables_rows = db.execute(
        '''SELECT c.id, c.usage_date, c.item_name, c.category, c.quantity, c.unit_price, c.total_cost,
                  c.patient_provided, c.notes, p.full_name AS patient_name,
                  doc.full_name AS doctor_name, nurse.full_name AS nurse_name
           FROM consumables_ledger c
           JOIN patients p ON p.id = c.patient_id
           JOIN invoices inv ON inv.id = c.invoice_id AND inv.status = 'closed'
           LEFT JOIN medical_staff doc ON doc.id = c.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = c.nurse_id
           WHERE c.work_date = ? AND c.shift = ?
           ORDER BY c.usage_date DESC''',
        (g.user_shift_status.get('work_date'), g.user_shift_status.get('active_shift'))
    ).fetchall()
    consumables_list = [dict(r) for r in consumables_rows]

    # Visits - only from closed invoices, read doctor/nurse from item itself
    visits = db.execute(
        '''SELECT v.id, v.visit_date, v.insurance_type, v.supplementary_insurance, v.price AS total_amount, v.notes,
                  p.full_name AS patient_name, v.doctor_name,
                  doc.full_name AS shift_doctor_name, nurse.full_name AS shift_nurse_name
           FROM visits v
           JOIN patients p ON p.id = v.patient_id
           JOIN invoices inv ON inv.id = v.invoice_id AND inv.status = 'closed'
           LEFT JOIN medical_staff doc ON doc.id = v.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = v.nurse_id
           WHERE v.work_date = ? AND v.shift = ?
           ORDER BY v.visit_date DESC''',
        (g.user_shift_status.get('work_date'), g.user_shift_status.get('active_shift'))
    ).fetchall()
    visits_list = [dict(r) for r in visits]

    # Closed Invoices in current shift with all nurses involved (excluding visits - visits don't have nurses)
    invoices = db.execute(
        '''SELECT inv.id, inv.opened_at, inv.closed_at, inv.status, inv.total_amount,
                  inv.insurance_type, inv.supplementary_insurance, inv.opened_by,
                  p.full_name AS patient_name,
                  COALESCE(doc.full_name,
                                        (
                                            SELECT COALESCE(v.doctor_name, ms.full_name)
                                            FROM visits v
                                            LEFT JOIN medical_staff ms ON ms.id = v.doctor_id
                                            WHERE v.invoice_id = inv.id AND (v.doctor_name IS NOT NULL OR v.doctor_id IS NOT NULL)
                                            ORDER BY v.visit_date DESC LIMIT 1
                                        )
                                    ) AS doctor_name,
                                    nurse.full_name AS nurse_name,
                  (
                      SELECT GROUP_CONCAT(ms.full_name, '، ')
                      FROM (
                          SELECT DISTINCT nurse_id FROM injections WHERE invoice_id = inv.id AND nurse_id IS NOT NULL
                          UNION
                          SELECT DISTINCT nurse_id FROM procedures WHERE invoice_id = inv.id AND nurse_id IS NOT NULL
                          UNION
                          SELECT DISTINCT nurse_id FROM consumables_ledger WHERE invoice_id = inv.id AND nurse_id IS NOT NULL
                      ) AS all_nurses
                      JOIN medical_staff ms ON ms.id = all_nurses.nurse_id
                  ) AS all_nurses_names
           FROM invoices inv
           JOIN patients p ON p.id = inv.patient_id
           LEFT JOIN medical_staff doc ON doc.id = inv.doctor_id
           LEFT JOIN medical_staff nurse ON nurse.id = inv.nurse_id
           WHERE inv.status = 'closed' AND inv.work_date = ? AND inv.shift = ?
           ORDER BY inv.closed_at DESC''',
        (g.user_shift_status.get('work_date'), g.user_shift_status.get('active_shift'))
    ).fetchall()
    invoices_list = [dict(r) for r in invoices]

    return render_template('reception/ledgers.html',
                           shift=shift,
                           start=start_dt,
                           end=end_dt,
                           injections=injections_list,
                           injections_doctor=injections_doctor_list,
                           procedures=procedures_list,
                           consumables=consumables_list,
                           visits=visits_list,
                           invoices=invoices_list)

@bp.route('/shift_performance', methods=['GET'])
@login_required
def shift_performance():
    """صفحه عملکرد شیفت برای پذیرش (شیفت جاری + شیفت‌های قبلی خودش)."""
    from src.adapters.sqlite.core import get_db
    from src.common.utils import get_current_shift_window, format_jalali_datetime

    db = get_db()
    username = g.user['username']

    current_work_date = g.user_shift_status.get('work_date')
    current_shift = g.user_shift_status.get('active_shift')

    selected_work_date = request.args.get('work_date', current_work_date)
    selected_shift = request.args.get('shift', current_shift)

    shift_names = {'morning': 'صبح', 'evening': 'عصر', 'night': 'شب'}

    shifts_list = _get_user_shifts_list(db, username, current_work_date, current_shift)
    allowed = {(s['work_date'], s['shift']) for s in shifts_list}
    if selected_work_date and selected_shift and (selected_work_date, selected_shift) not in allowed:
        selected_work_date = current_work_date
        selected_shift = current_shift

    report = _generate_shift_report(db, selected_work_date, selected_shift, username)

    start_time = '—'
    end_time = '—'
    is_current = (selected_work_date == current_work_date and selected_shift == current_shift)
    if is_current:
        _, start_dt, end_dt = get_current_shift_window()
        start_time = format_jalali_datetime(start_dt)
        end_time = format_jalali_datetime(end_dt)

    return render_template('reception/shift_performance.html',
        shifts_list=shifts_list,
        selected_work_date=selected_work_date,
        selected_shift=selected_shift,
        shift=selected_shift,
        shift_fa=shift_names.get(selected_shift, selected_shift),
        jalali_date=report.get('jalali_date', '—'),
        start_time=start_time,
        end_time=end_time,
        username=username,
        report=report,
    )


@bp.route('/my_shifts', methods=['GET'])
@login_required
def my_shifts_report():
    """گزارش شیفت‌های پذیرش - نمایش شیفت فعلی و شیفت‌های قبلی ثبت‌شده"""
    from src.adapters.sqlite.core import get_db
    from src.common.utils import format_jalali_datetime
    from src.common.jalali import Gregorian
    from datetime import timedelta
    
    db = get_db()
    username = g.user['username']
    
    # Current active shift
    work_date = g.user_shift_status.get('work_date')
    active_shift = g.user_shift_status.get('active_shift')

    shift_names = {'morning': 'صبح', 'evening': 'عصر', 'night': 'شب'}

    shifts_list = _get_user_shifts_list(db, username, work_date, active_shift)
    
    # Get selected shift (from query or current)
    selected_work_date = request.args.get('work_date', work_date)
    selected_shift = request.args.get('shift', active_shift)
    
    # ============ Detailed Report for Selected Shift ============
    report = _generate_shift_report(db, selected_work_date, selected_shift, username)
    
    return render_template('reception/my_shifts.html',
        shifts_list=shifts_list,
        selected_work_date=selected_work_date,
        selected_shift=selected_shift,
        selected_shift_fa=shift_names.get(selected_shift, selected_shift),
        report=report
    )


def _generate_shift_report(db, work_date: str, shift: str, username: str) -> dict:
    """Generate comprehensive shift report with all details."""
    from src.common.jalali import Gregorian
    
    if not work_date or not shift:
        return {}
    
    # ============ ویزیت به تفکیک بیمه ============
    visits_by_insurance = db.execute("""
        SELECT v.insurance_type, COUNT(*) as count, COALESCE(SUM(v.price), 0) as total
        FROM visits v
        WHERE v.work_date = ? AND v.shift = ? AND v.reception_user = ?
        GROUP BY v.insurance_type
    """, (work_date, shift, username)).fetchall()
    
    visits_total = sum(r['total'] for r in visits_by_insurance)
    visits_count = sum(r['count'] for r in visits_by_insurance)
    
    # ===================== معوقات بیمه (طبق منطق پنل مدیر) =====================
    # ویزیت:
    # - تعرفه پایه (آزاد) = visit_tariffs.is_base_tariff یا بیمه 'آزاد'
    # - سهم بیمار برای بیمه پایه = visit_tariffs.tariff_price (non-supplementary)
    # - معوقه بیمه پایه = تعرفه پایه - سهم بیمار
    # - اگر بیمه تکمیلی وجود داشته باشد: معوقه تکمیلی = سهم بیمارِ بیمه پایه - سهم نهایی بیمار با تکمیلی

    base_tariff = db.execute("SELECT tariff_price FROM visit_tariffs WHERE is_base_tariff = 1 AND is_active = 1 LIMIT 1").fetchone()
    if not base_tariff:
        base_tariff = db.execute("SELECT tariff_price FROM visit_tariffs WHERE insurance_type = 'آزاد' AND is_active = 1 LIMIT 1").fetchone()
    base_visit_price = float(base_tariff['tariff_price']) if base_tariff and base_tariff['tariff_price'] is not None else 0.0

    insurance_tariffs_rows = db.execute("""
        SELECT insurance_type, tariff_price
        FROM visit_tariffs
        WHERE insurance_type != 'آزاد'
          AND COALESCE(is_supplementary, 0) = 0
          AND COALESCE(is_base_tariff, 0) = 0
          AND is_active = 1
    """).fetchall()
    insurance_tariffs = {r['insurance_type']: float(r['tariff_price'] or 0) for r in insurance_tariffs_rows}

    supplementary_rows = db.execute("""
        SELECT insurance_type, tariff_price
        FROM visit_tariffs
        WHERE COALESCE(is_supplementary, 0) = 1
          AND is_active = 1
    """).fetchall()
    supplementary_tariffs = {r['insurance_type']: float(r['tariff_price'] or 0) for r in supplementary_rows}

    visits_for_arrears = db.execute("""
        SELECT v.id, v.insurance_type, v.supplementary_insurance
        FROM visits v
        WHERE v.work_date = ? AND v.shift = ? AND v.reception_user = ?
          AND v.insurance_type IS NOT NULL AND v.insurance_type != 'آزاد'
    """, (work_date, shift, username)).fetchall()

    visits_base_arrears_total = 0.0
    visits_supplementary_arrears_total = 0.0
    visits_arrears_visit_ids = set()

    for v in visits_for_arrears:
        ins_type = v['insurance_type']
        supp_ins = v['supplementary_insurance']

        patient_share = float(insurance_tariffs.get(ins_type, 0.0) or 0.0)
        base_debt = base_visit_price - patient_share
        if base_debt > 0:
            visits_base_arrears_total += base_debt
            visits_arrears_visit_ids.add(v['id'])

        if supp_ins and supp_ins in supplementary_tariffs and patient_share > 0:
            final_patient_share = float(supplementary_tariffs.get(supp_ins, 0.0) or 0.0)
            supp_debt = patient_share - final_patient_share
            if supp_debt > 0:
                visits_supplementary_arrears_total += supp_debt
                visits_arrears_visit_ids.add(v['id'])

    visits_pending_total = visits_base_arrears_total + visits_supplementary_arrears_total
    visits_pending_count = len(visits_arrears_visit_ids)
    
    # ============ خدمات پرستاری ============
    nursing_stats = db.execute("""
        SELECT COUNT(*) as count, COALESCE(SUM(total_price), 0) as total
        FROM injections
        WHERE work_date = ? AND shift = ? AND reception_user = ?
    """, (work_date, shift, username)).fetchone()
    
    # خدمات پرستاری - معوقه بیمه (طبق منطق پنل مدیر: nursing_covers و استثناها)
    nursing_tariffs_rows = db.execute("""
        SELECT insurance_type, nursing_covers
        FROM visit_tariffs
        WHERE insurance_type != 'آزاد'
          AND COALESCE(is_supplementary, 0) = 0
          AND COALESCE(is_base_tariff, 0) = 0
          AND is_active = 1
    """).fetchall()
    nursing_covers_map = {r['insurance_type']: bool(r['nursing_covers']) for r in nursing_tariffs_rows}

    nursing_arrears_rows = db.execute("""
        SELECT inj.id, inj.total_price, inj.service_id, inv.insurance_type
        FROM injections inj
        JOIN invoices inv ON inv.id = inj.invoice_id
        WHERE inj.work_date = ? AND inj.shift = ? AND inj.reception_user = ?
          AND inv.insurance_type IS NOT NULL AND inv.insurance_type != 'آزاد'
    """, (work_date, shift, username)).fetchall()

    nursing_pending_total = 0.0
    nursing_pending_count = 0
    for r in nursing_arrears_rows:
        ins_type = r['insurance_type']
        if not nursing_covers_map.get(ins_type, False):
            continue
        svc_id = r['service_id']
        if svc_id is not None:
            excluded = db.execute(
                "SELECT 1 FROM insurance_nursing_exclusions WHERE insurance_type = ? AND nursing_service_id = ? LIMIT 1",
                (ins_type, int(svc_id)),
            ).fetchone()
            if excluded:
                # این خدمت پرستاری برای این بیمه استثنا شده (پوشش ندارد)
                continue

        nursing_pending_total += float(r['total_price'] or 0.0)
        nursing_pending_count += 1
    
    # تزریقات به تفکیک پزشک
    # منطق جدید: تزریقات پزشک فقط زمانی محاسبه می‌شود که در همان فاکتور هم ویزیت وجود داشته باشد
    # یعنی پزشک هم ویزیت کرده و هم تزریقات ثبت شده
    injections_by_doctor = db.execute("""
        SELECT 
            COALESCE(ms.full_name, 'نامشخص') as doctor_name,
            COUNT(*) as count,
            COALESCE(SUM(inj.total_price), 0) as total
        FROM injections inj
        LEFT JOIN medical_staff ms ON inj.doctor_id = ms.id
        WHERE inj.work_date = ? AND inj.shift = ? AND inj.reception_user = ?
          AND inj.doctor_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM visits v 
              WHERE v.invoice_id = inj.invoice_id 
              AND v.doctor_id = inj.doctor_id
          )
        GROUP BY inj.doctor_id
    """, (work_date, shift, username)).fetchall()
    
    # ============ کار عملی ============
    procedures_stats = db.execute("""
        SELECT COUNT(*) as count, COALESCE(SUM(price), 0) as total
        FROM procedures
        WHERE work_date = ? AND shift = ? AND reception_user = ?
    """, (work_date, shift, username)).fetchone()
    
    # کار عملی معوق
    procedures_pending = db.execute("""
        SELECT COALESCE(SUM(p.price), 0) as total, COUNT(*) as count
        FROM procedures p
        JOIN invoices i ON p.invoice_id = i.id
        WHERE p.work_date = ? AND p.shift = ?
        AND p.reception_user = ?
        AND i.status = 'open'
        AND NOT EXISTS (
            SELECT 1 FROM invoice_item_payments iip 
            WHERE iip.item_id = p.id AND iip.item_type = 'procedure' AND iip.is_paid = 1
        )
    """, (work_date, shift, username)).fetchone()
    
    # ============ مصرفی‌ها به تفکیک آیتم ============
    consumables_items = db.execute("""
        SELECT item_name, category, SUM(quantity) as total_qty, 
               COALESCE(SUM(total_cost), 0) as total_cost
        FROM consumables_ledger
        WHERE work_date = ? AND shift = ? 
        AND reception_user = ?
        AND category = 'supply' 
        AND (COALESCE(patient_provided, 0) = 0 AND COALESCE(is_exception, 0) = 0)
        GROUP BY item_name
        ORDER BY total_qty DESC
    """, (work_date, shift, username)).fetchall()
    
    consumables_total = sum(r['total_cost'] for r in consumables_items)
    
    # ============ داروها به تفکیک آیتم ============
    drugs_items = db.execute("""
        SELECT item_name, category, SUM(quantity) as total_qty, 
               COALESCE(SUM(total_cost), 0) as total_cost
        FROM consumables_ledger
        WHERE work_date = ? AND shift = ? 
        AND reception_user = ?
        AND category = 'drug'
        AND (COALESCE(patient_provided, 0) = 0 AND COALESCE(is_exception, 0) = 0)
        GROUP BY item_name
        ORDER BY total_qty DESC
    """, (work_date, shift, username)).fetchall()
    
    drugs_total = sum(r['total_cost'] for r in drugs_items)
    
    # ============ تعداد فاکتورها و بیماران ============
    invoices_stats = db.execute("""
        SELECT COUNT(*) as invoice_count, COUNT(DISTINCT patient_id) as patient_count
        FROM invoices
        WHERE work_date = ? AND shift = ? AND opened_by = ?
    """, (work_date, shift, username)).fetchone()
    
    # ============ خلاصه مالی ============
    # تسویه شده
    settled_visits = db.execute("""
        SELECT COALESCE(SUM(v.price), 0) as total
        FROM visits v
        JOIN invoices i ON v.invoice_id = i.id
        WHERE v.work_date = ? AND v.shift = ?
        AND v.reception_user = ?
        AND (
            i.status = 'closed'
            OR EXISTS (
                SELECT 1 FROM invoice_item_payments iip 
                WHERE iip.item_id = v.id AND iip.item_type = 'visit' AND iip.is_paid = 1
            )
        )
    """, (work_date, shift, username)).fetchone()['total']
    
    settled_injections = db.execute("""
        SELECT COALESCE(SUM(inj.total_price), 0) as total
        FROM injections inj
        JOIN invoices i ON inj.invoice_id = i.id
        WHERE inj.work_date = ? AND inj.shift = ?
        AND inj.reception_user = ?
        AND (
            i.status = 'closed'
            OR EXISTS (
                SELECT 1 FROM invoice_item_payments iip 
                WHERE iip.item_id = inj.id AND iip.item_type = 'injection' AND iip.is_paid = 1
            )
        )
    """, (work_date, shift, username)).fetchone()['total']
    
    settled_procedures = db.execute("""
        SELECT COALESCE(SUM(p.price), 0) as total
        FROM procedures p
        JOIN invoices i ON p.invoice_id = i.id
        WHERE p.work_date = ? AND p.shift = ?
        AND p.reception_user = ?
        AND (
            i.status = 'closed'
            OR EXISTS (
                SELECT 1 FROM invoice_item_payments iip 
                WHERE iip.item_id = p.id AND iip.item_type = 'procedure' AND iip.is_paid = 1
            )
        )
    """, (work_date, shift, username)).fetchone()['total']
    
    settled_consumables = db.execute("""
        SELECT COALESCE(SUM(c.total_cost), 0) as total
        FROM consumables_ledger c
        JOIN invoices i ON c.invoice_id = i.id
        WHERE c.work_date = ? AND c.shift = ? 
        AND c.reception_user = ?
        AND (COALESCE(c.patient_provided, 0) = 0 AND COALESCE(c.is_exception, 0) = 0)
        AND (
            i.status = 'closed'
            OR EXISTS (
                SELECT 1 FROM invoice_item_payments iip 
                WHERE iip.item_id = c.id AND iip.item_type = 'consumable' AND iip.is_paid = 1
            )
        )
    """, (work_date, shift, username)).fetchone()['total']
    
    total_revenue = visits_total + nursing_stats['total'] + procedures_stats['total'] + consumables_total + drugs_total
    total_settled = settled_visits + settled_injections + settled_procedures + settled_consumables
    total_pending = total_revenue - total_settled
    
    # Jalali date
    try:
        parts = work_date.split('-')
        g_tuple = (int(parts[0]), int(parts[1]), int(parts[2]))
        j_tuple = Gregorian(*g_tuple).persian_tuple()
        jalali_date = f"{j_tuple[0]}/{j_tuple[1]:02d}/{j_tuple[2]:02d}"
    except:
        jalali_date = work_date
    
    injections_doctor_total = sum(r['total'] for r in injections_by_doctor)

    return {
        'jalali_date': jalali_date,
        'work_date': work_date,
        'shift': shift,
        
        # ویزیت
        'visits_by_insurance': [dict(r) for r in visits_by_insurance],
        'visits_total': visits_total,
        'visits_count': visits_count,
        # معوقات بیمه (ویزیت)
        'visits_pending_total': visits_pending_total,
        'visits_pending_count': visits_pending_count,
        'visits_base_arrears_total': visits_base_arrears_total,
        'visits_supplementary_arrears_total': visits_supplementary_arrears_total,
        
        # خدمات پرستاری
        'nursing_total': nursing_stats['total'],
        'nursing_count': nursing_stats['count'],
        # معوقات بیمه (پرستاری)
        'nursing_pending_total': nursing_pending_total,
        'nursing_pending_count': nursing_pending_count,
        
        # تزریقات پزشک
        'injections_by_doctor': [dict(r) for r in injections_by_doctor],
        'injections_doctor_total': injections_doctor_total,
        
        # کار عملی
        'procedures_total': procedures_stats['total'],
        'procedures_count': procedures_stats['count'],
        'procedures_pending_total': procedures_pending['total'],
        'procedures_pending_count': procedures_pending['count'],
        
        # مصرفی
        'consumables_items': [dict(r) for r in consumables_items],
        'consumables_total': consumables_total,
        
        # دارو
        'drugs_items': [dict(r) for r in drugs_items],
        'drugs_total': drugs_total,
        
        # آمار کلی
        'invoice_count': invoices_stats['invoice_count'],
        'patient_count': invoices_stats['patient_count'],
        
        # مالی
        'total_revenue': total_revenue,
        'total_settled': total_settled,
        'total_pending': total_pending
    }


def _get_user_shifts_list(db, username: str, current_work_date: str | None, current_shift: str | None):
    """Build a list of shifts for a reception user (current + historical)."""
    from src.common.jalali import Gregorian

    shift_names = {'morning': 'صبح', 'evening': 'عصر', 'night': 'شب'}

    # Historical shifts come from invoices opened by this user
    user_shifts = db.execute("""
        SELECT DISTINCT work_date, shift
        FROM invoices
        WHERE opened_by = ? AND work_date IS NOT NULL AND shift IS NOT NULL
        ORDER BY work_date DESC,
            CASE shift WHEN 'night' THEN 1 WHEN 'evening' THEN 2 WHEN 'morning' THEN 3 END
    """, (username,)).fetchall()

    shifts_list = []
    seen = set()

    # Add current shift first (even if no invoices yet)
    if current_work_date and current_shift:
        key = (current_work_date, current_shift)
        seen.add(key)
        shifts_list.append({
            'work_date': current_work_date,
            'shift': current_shift,
            'shift_fa': shift_names.get(current_shift, current_shift),
            'jalali_date': _to_jalali_date_str(current_work_date, Gregorian),
            'is_current': True,
        })

    # Add past shifts
    for s in user_shifts:
        key = (s['work_date'], s['shift'])
        if key in seen:
            continue
        seen.add(key)
        shifts_list.append({
            'work_date': s['work_date'],
            'shift': s['shift'],
            'shift_fa': shift_names.get(s['shift'], s['shift']),
            'jalali_date': _to_jalali_date_str(s['work_date'], Gregorian),
            'is_current': False,
        })

    return shifts_list


def _to_jalali_date_str(work_date: str, GregorianType) -> str:
    try:
        parts = work_date.split('-')
        g_tuple = (int(parts[0]), int(parts[1]), int(parts[2]))
        j_tuple = GregorianType(*g_tuple).persian_tuple()
        return f"{j_tuple[0]}/{j_tuple[1]:02d}/{j_tuple[2]:02d}"
    except Exception:
        return work_date


@bp.route('/api/invoice/<int:invoice_id>/details', methods=['GET'])
@login_required
def get_invoice_details_api(invoice_id):
    """API endpoint to get full invoice details with all items."""
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.common.utils import format_iran_datetime
    invoice_repo = InvoiceRepository()
    
    invoice = invoice_repo.get_invoice_by_id(invoice_id)
    if not invoice:
        return jsonify({'error': 'فاکتور یافت نشد'}), 404
    
    items = invoice_repo.get_invoice_items(invoice_id)
    financials = invoice_repo.get_financials(invoice_id)
    
    # Convert datetime fields to Jalali string (already converted to Iran time)
    if invoice.get('opened_at'):
        invoice['opened_at'] = format_iran_datetime(invoice['opened_at'])
    if invoice.get('closed_at'):
        invoice['closed_at'] = format_iran_datetime(invoice['closed_at'])
    
    for item in items:
        if item.get('date'):
            item['date'] = format_iran_datetime(item['date'])
    
    return jsonify({
        'invoice': invoice,
        'items': items,
        'financials': financials
    })


# =====================================================
# Shift Management APIs (مدیریت شیفت دستی)
# =====================================================

@bp.route('/api/shift/status', methods=['GET'])
@login_required
def get_shift_status():
    """
    Get current shift status for the logged-in user.
    Returns: active_shift, work_date, open_invoices_count
    """
    from src.adapters.sqlite.user_shift_repo import UserShiftRepository
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    
    user_id = g.user['id']
    shift_repo = UserShiftRepository()
    invoice_repo = InvoiceRepository()
    
    # Get effective shift for user
    active_shift, work_date, is_overdue, should_prompt = shift_repo.get_effective_shift_for_user(user_id)
    user_shift = shift_repo.get_user_active_shift(user_id)
    shift_started_at = user_shift.get('shift_started_at') if user_shift else None
    
    # Count open invoices
    open_invoices = invoice_repo.get_open_invoices()
    open_count = len(open_invoices) if open_invoices else 0
    
    # Shift names in Persian
    shift_names = {
        'morning': 'صبح',
        'evening': 'عصر',
        'night': 'شب'
    }
    
    return jsonify({
        'active_shift': active_shift,
        'active_shift_fa': shift_names.get(active_shift, active_shift),
        'work_date': work_date,
        'shift_started_at': shift_started_at,
        'is_overdue': False,
        'should_prompt': False,
        'open_invoices_count': open_count,
        'allowed_shifts': [
            {'key': 'morning', 'label': 'صبح'},
            {'key': 'evening', 'label': 'عصر'},
            {'key': 'night', 'label': 'شب'},
        ],
    })


@bp.route('/api/shift/change', methods=['POST'])
@login_required
def change_shift():
    """
    Change user's active shift (manual).
    فاکتورهای باز مانعی برای تغییر شیفت نیستند.
    """
    from src.adapters.sqlite.user_shift_repo import UserShiftRepository
    
    user_id = g.user['id']
    shift_repo = UserShiftRepository()
    
    payload = request.get_json(silent=True) or {}
    requested_shift = (payload.get('shift') or '').strip()
    requested_work_date = (payload.get('work_date') or '').strip()

    allowed = {'morning', 'evening', 'night'}
    if requested_shift not in allowed:
        return jsonify({'error': 'شیفت نامعتبر است'}), 400

    if not requested_work_date:
        from src.common.utils import iran_now
        now = iran_now()
        # Night shift spans two calendar dates (starts ~19:30 and ends ~07:30).
        # If user selects night shift after midnight and before 07:30, the work_date
        # should remain the previous day.
        if requested_shift == 'night' and (now.hour, now.minute) < (7, 30):
            requested_work_date = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            requested_work_date = now.strftime('%Y-%m-%d')

    # Update user's active shift
    shift_repo.set_user_active_shift(user_id, requested_shift, requested_work_date)
    
    # Log the shift change
    shift_names = {'morning': 'صبح', 'evening': 'عصر', 'night': 'شب'}
    log_activity(
        action_type=ActionType.SHIFT_STAFF_SET,
        action_category=ActionCategory.SHIFT,
        description=f'تغییر شیفت به شیفت {shift_names.get(requested_shift, requested_shift)}',
        target_type='user_shift',
        new_value=f'{requested_shift}:{requested_work_date}'
    )
    
    return jsonify({
        'success': True,
        'new_shift': requested_shift,
        'new_shift_fa': shift_names.get(requested_shift, requested_shift),
        'work_date': requested_work_date
    })


@bp.route('/api/shift/dismiss_prompt', methods=['POST'])
@login_required
def dismiss_shift_prompt():
    """
    Backward compatible no-op.
    Manual shift switching has no time-based prompts.
    """
    return jsonify({'success': True})
