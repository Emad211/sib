from typing import List, Dict, Optional
from src.adapters.sqlite.core import get_db

class ProcedureTariffsRepository:
    """Catalog repository for procedure tariffs."""

    def list_active(self) -> List[Dict]:
        db = get_db()
        rows = db.execute("SELECT id, name, unit_price FROM procedure_tariffs WHERE is_active = 1 ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get(self, tariff_id: int) -> Optional[Dict]:
        db = get_db()
        row = db.execute("SELECT id, name, unit_price FROM procedure_tariffs WHERE id = ? AND is_active = 1", (tariff_id,)).fetchone()
        return dict(row) if row else None

    def create(self, name: str, unit_price: float) -> int:
        db = get_db()
        cur = db.execute("INSERT INTO procedure_tariffs (name, unit_price, is_active) VALUES (?, ?, 1)", (name, unit_price))
        db.commit(); return cur.lastrowid

    def deactivate(self, tariff_id: int) -> bool:
        db = get_db()
        cur = db.execute("UPDATE procedure_tariffs SET is_active = 0 WHERE id = ?", (tariff_id,))
        db.commit(); return cur.rowcount > 0
