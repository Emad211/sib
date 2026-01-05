from typing import List, Dict, Optional
from src.adapters.sqlite.core import get_db

class NursingServicesRepository:
    def list_active(self) -> List[Dict]:
        db = get_db()
        rows = db.execute("SELECT id, service_name, unit_price FROM nursing_services WHERE is_active = 1 ORDER BY service_name").fetchall()
        return [dict(r) for r in rows]

    def create(self, service_name: str, unit_price: float) -> int:
        db = get_db()
        cur = db.execute("INSERT INTO nursing_services (service_name, unit_price) VALUES (?, ?)", (service_name, unit_price))
        db.commit()
        return cur.lastrowid

    def deactivate(self, service_id: int):
        db = get_db()
        db.execute("UPDATE nursing_services SET is_active = 0 WHERE id = ?", (service_id,))
        db.commit()

    def get(self, service_id: int) -> Optional[Dict]:
        db = get_db()
        row = db.execute("SELECT id, service_name, unit_price FROM nursing_services WHERE id = ?", (service_id,)).fetchone()
        return dict(row) if row else None
