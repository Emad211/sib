from typing import Optional, Dict, List
from datetime import datetime
from src.adapters.sqlite.core import get_db
from src.common.utils import get_current_shift_name
from src.common.utils import get_work_date_for_datetime

class ProcedureRepository:
    """Repository for procedure (کار عملی) items."""

    def _ensure_shift_staff(self, work_date: str, shift: str, doctor_id: Optional[int], nurse_id: Optional[int]):
        """اطمینان از ثبت کادر درمان در shift_staff برای این تاریخ و شیفت"""
        if not doctor_id and not nurse_id:
            return
        db = get_db()
        existing = db.execute(
            "SELECT doctor_id, nurse_id FROM shift_staff WHERE work_date = ? AND shift = ?",
            (work_date, shift)
        ).fetchone()
        
        if existing:
            updates = []
            params = []
            if doctor_id and not existing['doctor_id']:
                updates.append("doctor_id = ?")
                params.append(doctor_id)
            if nurse_id and not existing['nurse_id']:
                updates.append("nurse_id = ?")
                params.append(nurse_id)
            if updates:
                params.extend([work_date, shift])
                db.execute(f"UPDATE shift_staff SET {', '.join(updates)} WHERE work_date = ? AND shift = ?", params)
        else:
            db.execute(
                "INSERT INTO shift_staff (work_date, shift, doctor_id, nurse_id) VALUES (?, ?, ?, ?)",
                (work_date, shift, doctor_id, nurse_id)
            )
        db.commit()

    def add_procedure(self, patient_id: int, procedure_type: str, price: float,
                      reception_user: str, invoice_id: Optional[int] = None, notes: str = "",
                      performer_type: Optional[str] = None, performer_id: Optional[int] = None,
                      doctor_id: Optional[int] = None, nurse_id: Optional[int] = None) -> int:
        # Validation
        if price < 0:
            raise ValueError("قیمت نمی‌تواند منفی باشد")
        
        current_shift = self._current_shift()
        work_date = get_work_date_for_datetime()
        
        # اطمینان از ثبت در shift_staff
        self._ensure_shift_staff(work_date, current_shift, doctor_id, nurse_id)
        
        db = get_db()
        cursor = db.execute(
            '''INSERT INTO procedures (
                patient_id, procedure_type, shift, work_date, price, reception_user, notes, invoice_id, 
                performer_type, performer_id, doctor_id, nurse_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                patient_id,
                procedure_type,
                current_shift,
                work_date,
                price,
                reception_user,
                notes,
                invoice_id,
                performer_type,
                performer_id,
                doctor_id,
                nurse_id
            )
        )
        db.commit()
        return cursor.lastrowid

    def list_by_invoice(self, invoice_id: int) -> List[Dict]:
        db = get_db()
        rows = db.execute(
            '''SELECT id, procedure_type, procedure_date, price, notes, performer_type, performer_id
               FROM procedures WHERE invoice_id = ? ORDER BY procedure_date DESC''',
            (invoice_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _current_shift(self) -> str:
        return get_current_shift_name()
