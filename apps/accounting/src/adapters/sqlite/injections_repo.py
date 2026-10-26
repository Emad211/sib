from typing import Optional, Dict, List
from datetime import datetime
from src.adapters.sqlite.core import get_db
from src.common.utils import get_current_shift_name
from src.common.utils import get_work_date_for_datetime

class InjectionRepository:
    """Repository for nursing/injection services."""

    def _ensure_shift_staff(self, work_date: str, shift: str, doctor_id: Optional[int], nurse_id: Optional[int]):
        """اطمینان از ثبت کادر درمان در shift_staff برای این تاریخ و شیفت"""
        if not doctor_id and not nurse_id:
            return
        db = get_db()
        # بررسی وجود رکورد
        existing = db.execute(
            "SELECT doctor_id, nurse_id FROM shift_staff WHERE work_date = ? AND shift = ?",
            (work_date, shift)
        ).fetchone()
        
        if existing:
            # آپدیت فقط اگر مقدار جدید داریم و قبلی خالی بوده
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
            # درج رکورد جدید
            db.execute(
                "INSERT INTO shift_staff (work_date, shift, doctor_id, nurse_id) VALUES (?, ?, ?, ?)",
                (work_date, shift, doctor_id, nurse_id)
            )
        db.commit()

    def add_injection(self, patient_id: int, injection_type: str, count: int, unit_price: float,
                      reception_user: str, invoice_id: Optional[int] = None, notes: str = "",
                      service_id: Optional[int] = None, doctor_id: Optional[int] = None,
                      nurse_id: Optional[int] = None) -> int:
        # Validation
        if count <= 0:
            raise ValueError("تعداد باید بزرگتر از صفر باشد")
        if unit_price < 0:
            raise ValueError("قیمت واحد نمی‌تواند منفی باشد")
        
        total_price = float(unit_price) * int(count)
        current_shift = self._current_shift()
        work_date = get_work_date_for_datetime()
        
        # اطمینان از ثبت در shift_staff
        self._ensure_shift_staff(work_date, current_shift, doctor_id, nurse_id)
        
        db = get_db()
        cursor = db.execute(
            '''INSERT INTO injections (
                patient_id, injection_type, service_id, shift, work_date, count, unit_price, total_price, 
                reception_user, notes, invoice_id, doctor_id, nurse_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                patient_id,
                injection_type,
                service_id,
                current_shift,
                work_date,
                count,
                unit_price,
                total_price,
                reception_user,
                notes,
                invoice_id,
                doctor_id,
                nurse_id
            )
        )
        db.commit()
        return cursor.lastrowid

    def list_by_invoice(self, invoice_id: int) -> List[Dict]:
        db = get_db()
        rows = db.execute(
            '''SELECT id, injection_type, injection_date, count, unit_price, total_price, notes
               FROM injections WHERE invoice_id = ? ORDER BY injection_date DESC''',
            (invoice_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _current_shift(self) -> str:
        return get_current_shift_name()
