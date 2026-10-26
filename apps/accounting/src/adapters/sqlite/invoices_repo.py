from typing import Optional, List, Dict
from src.adapters.sqlite.core import get_db
from src.common.utils import get_work_date_for_datetime


class InvoiceRepository:
    """Repository for managing invoices."""

    def open_invoice(self, patient_id: int, insurance_type: str, supplementary_insurance: Optional[str], opened_by: str, shift: Optional[str] = None) -> int:
        """Open a new invoice for a patient (without binding to doctor/nurse; staff is shift-level)."""
        db = get_db()
        work_date = get_work_date_for_datetime()
        
        # If shift not provided, try to get from global status or calculate
        if not shift:
            from src.common.utils import get_current_shift_window
            shift, _, _ = get_current_shift_window()

        # Resolve full name for opener (denormalize to preserve historical name)
        try:
            user_row = db.execute("SELECT full_name FROM users WHERE username = ?", (opened_by,)).fetchone()
            opener_name = user_row['full_name'] if user_row and user_row['full_name'] else opened_by
        except Exception:
            opener_name = opened_by

        cursor = db.execute(
            """INSERT INTO invoices (
                patient_id, insurance_type, supplementary_insurance,
                status, opened_by, opened_by_name, total_amount, work_date, shift
            ) VALUES (?, ?, ?, 'open', ?, ?, 0, ?, ?)""",
            (patient_id, insurance_type, supplementary_insurance, opened_by, opener_name, work_date, shift)
        )
        db.commit()
        return cursor.lastrowid

    def get_open_invoices(self, limit: int = 300) -> List[Dict]:
        """Get all open invoices with patient info."""
        db = get_db()
        rows = db.execute("""
            SELECT i.id, i.patient_id, i.status, i.opened_at, i.total_amount,
                   i.insurance_type, i.supplementary_insurance, i.opened_by,
                   COALESCE(i.opened_by_name, u_open.full_name, i.opened_by) AS opened_by_name,
                   COALESCE(i.closed_by_name, u_close.full_name, i.closed_by) AS closed_by_name,
                   p.full_name as patient_name, p.national_id,
                   d.full_name as doctor_name, n.full_name as nurse_name
            FROM invoices i 
            JOIN patients p ON p.id = i.patient_id
            LEFT JOIN users u_open ON u_open.username = i.opened_by
            LEFT JOIN users u_close ON u_close.username = i.closed_by
            LEFT JOIN medical_staff d ON d.id = i.doctor_id
            LEFT JOIN medical_staff n ON n.id = i.nurse_id
            WHERE i.status = 'open'
            ORDER BY i.opened_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_invoice_by_id(self, invoice_id: int) -> Optional[Dict]:
        """Get invoice details by ID."""
        db = get_db()
        row = db.execute("""
            SELECT i.*, p.full_name as patient_name,
                   COALESCE(i.opened_by_name, u_open.full_name, i.opened_by) AS opened_by_name,
                   COALESCE(i.closed_by_name, u_close.full_name, i.closed_by) AS closed_by_name
            FROM invoices i
            JOIN patients p ON p.id = i.patient_id
            LEFT JOIN users u_open ON u_open.username = i.opened_by
            LEFT JOIN users u_close ON u_close.username = i.closed_by
            WHERE i.id = ?
        """, (invoice_id,)).fetchone()
        return dict(row) if row else None

    def get_invoice_items(self, invoice_id: int) -> List[Dict]:
        """Get all items (visits, injections, procedures, consumables) for an invoice."""
        db = get_db()
        # Determine invoice insurance to apply nursing coverage rules
        inv_row = db.execute("SELECT insurance_type FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        invoice_insurance = inv_row['insurance_type'] if inv_row else None
        # Read both nursing_covers (boolean) and nursing_tariff (legacy numeric)
        nursing_covers = None
        nursing_tariff = None
        if invoice_insurance:
            t = db.execute("SELECT nursing_covers, nursing_tariff FROM visit_tariffs WHERE insurance_type = ?", (invoice_insurance,)).fetchone()
            if t:
                # prefer explicit boolean flag if present
                if 'nursing_covers' in t.keys():
                    nursing_covers = bool(t['nursing_covers'])
                nursing_tariff = t['nursing_tariff'] if 'nursing_tariff' in t.keys() else None
        items = []
        
        # Get base tariff (آزاد / پایه) for visits
        base_tariff_row = db.execute("SELECT tariff_price FROM visit_tariffs WHERE is_base_tariff = 1 AND is_active = 1 LIMIT 1").fetchone()
        if not base_tariff_row:
            base_tariff_row = db.execute("SELECT tariff_price FROM visit_tariffs WHERE insurance_type = 'آزاد' AND is_active = 1 LIMIT 1").fetchone()
        base_visit_price = float(base_tariff_row['tariff_price']) if base_tariff_row else 0.0

        # Visits: read doctor from item itself (no nurse for visits)
        visits = db.execute("""
            SELECT 'visit' AS type, v.id, v.visit_date AS date,
                   COALESCE(doc.full_name, v.doctor_name) AS doctor_name,
                   NULL AS nurse_name,
                   v.price AS stored_price,
                   'ویزیت' AS description,
                   NULL AS category,
                   v.insurance_type, v.supplementary_insurance
            FROM visits v
            LEFT JOIN medical_staff doc ON doc.id = v.doctor_id
            WHERE v.invoice_id = ?
        """, (invoice_id,)).fetchall()
        for r in visits:
            it = dict(r)
            # recorded_price = تعرفه پایه/آزاد (base tariff)
            # patient_share = تعرفه بیمه ای (سهم بیمار) - what insurance says patient pays
            recorded_price = base_visit_price
            
            # Get patient share from insurance tariff
            visit_insurance = it.get('insurance_type') or invoice_insurance
            patient_share = recorded_price  # default to base if no insurance
            
            if visit_insurance:
                ins_row = db.execute("SELECT tariff_price FROM visit_tariffs WHERE insurance_type = ? AND is_active = 1 AND COALESCE(is_supplementary,0)=0 LIMIT 1", (visit_insurance,)).fetchone()
                if ins_row and ins_row['tariff_price'] is not None:
                    patient_share = float(ins_row['tariff_price'])
            
            # If supplementary insurance exists, it may override patient_share
            supp = it.get('supplementary_insurance')
            if supp:
                srow = db.execute("SELECT tariff_price FROM visit_tariffs WHERE insurance_type = ? AND is_active = 1 AND COALESCE(is_supplementary,0)=1 LIMIT 1", (supp,)).fetchone()
                if srow and srow['tariff_price'] is not None:
                    patient_share = float(srow['tariff_price'])
            
            insurance_share = recorded_price - patient_share if recorded_price > patient_share else 0
            covered = 1 if patient_share == 0 and recorded_price > 0 else 0
            
            it['recorded_price'] = float(recorded_price)
            it['patient_share'] = float(patient_share)
            it['insurance_share'] = float(insurance_share)
            it['covered_by_insurance'] = covered
            items.append(it)

        # Injections: read doctor/nurse from item itself (i.doctor_id, i.nurse_id)
        # Load exclusions for this invoice's insurance (nursing services that are NOT covered)
        excluded_services = set()
        if invoice_insurance:
            rows_ex = db.execute("SELECT nursing_service_id FROM insurance_nursing_exclusions WHERE insurance_type = ?", (invoice_insurance,)).fetchall()
            excluded_services = {r['nursing_service_id'] for r in rows_ex}

        injections = db.execute("""
            SELECT 'injection' AS type, i.id, i.injection_date AS date,
                   doc.full_name AS doctor_name,
                   nurse.full_name AS nurse_name,
                   i.total_price AS recorded_price,
                   i.injection_type AS description,
                   i.service_id AS service_id,
                   NULL AS category
            FROM injections i
            LEFT JOIN medical_staff doc ON doc.id = i.doctor_id
            LEFT JOIN medical_staff nurse ON nurse.id = i.nurse_id
            WHERE i.invoice_id = ?
        """, (invoice_id,)).fetchall()
        inj_rows = [dict(r) for r in injections]
        for r in inj_rows:
            original = float(r.get('recorded_price') or 0)
            # Default patient share is recorded price
            patient_share = original
            insurance_share = 0
            covered = 0
            # Determine coverage: prefer `nursing_covers` flag (manager UI),
            # fall back to legacy `nursing_tariff==0` meaning free.
            if (nursing_covers is not None and nursing_covers) or (nursing_covers is None and nursing_tariff is not None and float(nursing_tariff) == 0):
                # If service_id is excluded for this insurance, do NOT mark as covered
                svc_id = r.get('service_id')
                if svc_id and svc_id in excluded_services:
                    # Not covered due to explicit exclusion
                    insurance_share = 0
                    patient_share = original
                    covered = 0
                else:
                    insurance_share = original
                    patient_share = 0
                    covered = 1
            r['recorded_price'] = original
            r['patient_share'] = float(patient_share)
            r['insurance_share'] = float(insurance_share)
            r['covered_by_insurance'] = covered
        items.extend(inj_rows)

        # Procedures: read doctor/nurse from item itself (pr.doctor_id, pr.nurse_id)
        procedures = db.execute("""
             SELECT 'procedure' AS type, pr.id, pr.procedure_date AS date,
                   doc.full_name AS doctor_name,
                   nurse.full_name AS nurse_name,
                   pr.price AS recorded_price,
                 CASE WHEN pr.performer_type = 'nurse' THEN pr.procedure_type || ' (پرستار)'
                   WHEN pr.performer_type = 'doctor' THEN pr.procedure_type || ' (پزشک)'
                   ELSE pr.procedure_type END AS description,
                   NULL AS category
            FROM procedures pr
            LEFT JOIN medical_staff doc ON doc.id = pr.doctor_id
            LEFT JOIN medical_staff nurse ON nurse.id = pr.nurse_id
            WHERE pr.invoice_id = ?
        """, (invoice_id,)).fetchall()
        proc_rows = [dict(r) for r in procedures]
        for r in proc_rows:
            original = float(r.get('recorded_price') or 0)
            patient_share = original
            insurance_share = 0
            covered = 0
            if r.get('description', '').endswith('(پرستار)') and ((nursing_covers is not None and nursing_covers) or (nursing_covers is None and nursing_tariff is not None and float(nursing_tariff) == 0)):
                insurance_share = original
                patient_share = 0
                covered = 1
            r['recorded_price'] = original
            r['patient_share'] = float(patient_share)
            r['insurance_share'] = float(insurance_share)
            r['covered_by_insurance'] = covered
        items.extend(proc_rows)

        # Consumables: read doctor/nurse from item itself (c.doctor_id, c.nurse_id)
        consumables = db.execute("""
            SELECT 'consumable' AS type, c.id, c.usage_date AS date,
                   doc.full_name AS doctor_name,
                   nurse.full_name AS nurse_name,
                   c.total_cost AS recorded_price,
                   c.item_name AS description,
                   c.category AS category,
                   c.patient_provided AS patient_provided
            FROM consumables_ledger c
            LEFT JOIN medical_staff doc ON doc.id = c.doctor_id
            LEFT JOIN medical_staff nurse ON nurse.id = c.nurse_id
            WHERE c.invoice_id = ?
        """, (invoice_id,)).fetchall()
        for r in consumables:
            it = dict(r)
            original = float(it.get('recorded_price') or 0)
            it['recorded_price'] = original
            it['patient_share'] = float(original)
            it['insurance_share'] = 0.0
            it['covered_by_insurance'] = 0
            items.append(it)
        
        # Fallback: if doctor_name still empty for non-visit items, use latest visit doctor
        last_visit_doctor = None
        for it in items:
            if it['type'] == 'visit' and it.get('doctor_name'):
                last_visit_doctor = it.get('doctor_name')
                break
        if last_visit_doctor:
            for it in items:
                if it['type'] != 'visit' and not it.get('doctor_name'):
                    it['doctor_name'] = last_visit_doctor
        return sorted(items, key=lambda x: x['date'], reverse=True)

    def close_invoice(self, invoice_id: int, closed_by: str) -> bool:
        """Close an invoice and update totals."""
        db = get_db()
        
        # Update invoice totals first
        self.update_invoice_totals(invoice_id)
        
        # Close the invoice - use Iran local time
        # Resolve full name for closer (denormalize)
        try:
            user_row = db.execute("SELECT full_name FROM users WHERE username = ?", (closed_by,)).fetchone()
            closer_name = user_row['full_name'] if user_row and user_row['full_name'] else closed_by
        except Exception:
            closer_name = closed_by

        cursor = db.execute("""
            UPDATE invoices
            SET status = 'closed', closed_at = datetime('now', '+3 hours', '+30 minutes'), closed_by = ?, closed_by_name = ?
            WHERE id = ? AND status = 'open'
        """, (closed_by, closer_name, invoice_id))
        
        db.commit()
        return cursor.rowcount > 0

    def update_invoice_totals(self, invoice_id: int):
        """Recalculate and update total_amount for the invoice."""
        db = get_db()
        # Recalculate total as the sum of patient-facing amounts (patient_share)
        items = self.get_invoice_items(invoice_id)
        total = 0.0
        for it in items:
            amt = it.get('patient_share') or 0
            total += float(amt)

        db.execute(
            "UPDATE invoices SET total_amount = ? WHERE id = ?",
            (total, invoice_id)
        )
        db.commit()

    def get_financials(self, invoice_id: int) -> Dict:
        """Return total per category, paid amount (by type), and remaining for invoice.
        IMPORTANT: Consumables are NOT counted in revenue/income calculations."""
        db = get_db()
        # Build financials from computed invoice items (summing patient_share per category)
        items = self.get_invoice_items(invoice_id)
        totals = {'visit': 0.0, 'injection': 0.0, 'procedure': 0.0, 'consumable': 0.0}
        for it in items:
            t = it.get('type')
            amt = float(it.get('patient_share') or 0)
            if t in totals:
                totals[t] += amt

        # Revenue = visits + injections + procedures (NOT consumables)
        revenue_total = totals['visit'] + totals['injection'] + totals['procedure']
        consumables_total = totals['consumable']
        
        # Grand total includes consumables for invoice balance
        grand_total = revenue_total + consumables_total

        # Invoice total_amount should reflect patient-facing total (may already be set)
        inv_total = db.execute("SELECT total_amount FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        invoice_total = float(inv_total['total_amount']) if inv_total else grand_total

        # Calculate paid amount from invoice_item_payments with payment type breakdown
        paid_card = 0.0
        paid_cash = 0.0
        paid_total = 0.0
        
        # Read all payments for this invoice
        pays = db.execute("""
            SELECT item_type, item_id, payment_type, is_paid 
            FROM invoice_item_payments 
            WHERE invoice_id = ?
        """, (invoice_id,)).fetchall()
        
        for p in pays:
            if p['is_paid'] == 1:
                # Find the item in items to get its patient_share
                for it in items:
                    if it.get('type') == p['item_type'] and it.get('id') == p['item_id']:
                        amt = float(it.get('patient_share') or 0)
                        paid_total += amt
                        if p['payment_type'] == 'card':
                            paid_card += amt
                        elif p['payment_type'] == 'cash':
                            paid_cash += amt
                        break

        remaining = invoice_total - paid_total if invoice_total > paid_total else 0.0

        return {
            'visits': totals['visit'],
            'injections': totals['injection'],
            'procedures': totals['procedure'],
            'consumables': consumables_total,
            'revenue': revenue_total,  # درآمد (بدون مصرفی)
            'total': grand_total,      # جمع کل فاکتور
            'paid': paid_total,
            'paid_card': paid_card,    # پرداخت کارتخوان
            'paid_cash': paid_cash,    # پرداخت نقدی
            'remaining': remaining
        }
