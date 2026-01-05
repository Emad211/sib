from typing import Optional, Dict, List
from src.adapters.sqlite.core import get_db
from src.common.utils import get_current_shift_name
from src.common.utils import get_work_date_for_datetime

class ConsumableLedgerRepository:
    """Repository for consumables ledger items."""

    def add_consumable(self, patient_id: Optional[int], item_name: str, category: str,
                       quantity: float, unit_price: float, reception_user: str,
                       invoice_id: Optional[int] = None, notes: str = "", patient_provided: bool = False,
                       is_exception: bool = False,
                       doctor_id: Optional[int] = None, nurse_id: Optional[int] = None) -> int:
        # Validation
        if quantity <= 0:
            raise ValueError("تعداد باید بزرگتر از صفر باشد")
        if unit_price < 0:
            raise ValueError("قیمت واحد نمی‌تواند منفی باشد")
        
        total_cost = 0.0 if patient_provided else float(unit_price) * float(quantity)
        work_date = get_work_date_for_datetime()
        db = get_db()
        cursor = db.execute(
            '''INSERT INTO consumables_ledger (
                patient_id, item_name, category, quantity, unit_price, total_cost,
                patient_provided, is_exception, shift, work_date, reception_user, notes, invoice_id, doctor_id, nurse_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                patient_id,
                item_name,
                category,
                quantity,
                unit_price,
                total_cost,
                1 if patient_provided else 0,
                1 if is_exception else 0,
                self._current_shift(),
                work_date,
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
            '''SELECT id, item_name, category, usage_date, quantity, unit_price, total_cost, notes
               FROM consumables_ledger WHERE invoice_id = ? ORDER BY usage_date DESC''',
            (invoice_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _current_shift(self) -> str:
        return get_current_shift_name()
