from src.adapters.sqlite.core import get_db
from src.domain.visits import Visit
from src.common.utils import get_work_date_for_datetime

class VisitRepository:
    def create(self, visit: Visit) -> int:
        db = get_db()
        work_date = get_work_date_for_datetime()
        # Map Visit.total_amount -> visits.price column in schema
        cursor = db.execute(
            '''INSERT INTO visits (
                patient_id, doctor_name, shift, insurance_type,
                supplementary_insurance, price, reception_user, notes, payment_status, invoice_id,
                doctor_id, nurse_id, work_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                visit.patient_id,
                visit.doctor_name,
                visit.shift,
                visit.insurance_type,
                visit.supplementary_insurance,
                visit.total_amount,
                visit.reception_user,
                visit.notes,
                visit.payment_status,
                getattr(visit, 'invoice_id', None),
                getattr(visit, 'doctor_id', None),
                getattr(visit, 'nurse_id', None),
                work_date,
            )
        )
        db.commit()
        return cursor.lastrowid

    def get_today_visits(self, work_date: str = None):
        db = get_db()
        if not work_date:
            work_date = get_work_date_for_datetime()
            
        rows = db.execute(
            '''SELECT v.*, p.full_name as patient_name 
               FROM visits v 
               JOIN patients p ON v.patient_id = p.id
               WHERE v.work_date = ?
               ORDER BY v.visit_date DESC''',
            (work_date,)
        ).fetchall()
        
        return [self._map_row(row) for row in rows]

    def get_unpaid_visits(self):
        db = get_db()
        rows = db.execute(
            '''SELECT v.*, p.full_name as patient_name 
               FROM visits v 
               JOIN patients p ON v.patient_id = p.id
               WHERE v.payment_status = 'unpaid'
               ORDER BY v.visit_date DESC'''
        ).fetchall()
        return [self._map_row(row) for row in rows]

    def mark_as_paid(self, visit_id):
        db = get_db()
        db.execute(
            "UPDATE visits SET payment_status = 'paid', status = 'completed' WHERE id = ?",
            (visit_id,)
        )
        db.commit()

    def _map_row(self, row) -> Visit:
        return Visit(
            id=row['id'],
            patient_id=row['patient_id'],
            doctor_name=row['doctor_name'],
            visit_date=row['visit_date'],
            shift=row['shift'],
            insurance_type=row['insurance_type'],
            supplementary_insurance=row['supplementary_insurance'],
            status=row['status'],
            payment_status=row['payment_status'] if 'payment_status' in row.keys() else 'unpaid',
            total_amount=row['price'] if 'price' in row.keys() else 0,  # schema uses 'price' column
            reception_user=row['reception_user'],
            notes=row['notes'],
            patient_name=row['patient_name'] if 'patient_name' in row.keys() else None,
            doctor_id=row['doctor_id'] if 'doctor_id' in row.keys() else None,
            nurse_id=row['nurse_id'] if 'nurse_id' in row.keys() else None
        )
