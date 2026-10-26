from typing import List, Dict, Optional
from src.adapters.sqlite.core import get_db

class ConsumableTariffsRepository:
    def list_active(self, category: Optional[str] = None) -> List[Dict]:
        db = get_db()
        if category:
            rows = db.execute("SELECT id, name, default_price, category FROM consumable_tariffs WHERE is_active = 1 AND category = ? ORDER BY name", (category,)).fetchall()
        else:
            rows = db.execute("SELECT id, name, default_price, category FROM consumable_tariffs WHERE is_active = 1 ORDER BY category, name").fetchall()
        return [dict(r) for r in rows]

    def create(self, name: str, default_price: float, category: str) -> int:
        db = get_db()
        cur = db.execute("INSERT INTO consumable_tariffs (name, default_price, category) VALUES (?,?,?)", (name, default_price, category))
        db.commit()
        return cur.lastrowid

    def deactivate(self, tariff_id: int):
        db = get_db()
        db.execute("UPDATE consumable_tariffs SET is_active = 0 WHERE id = ?", (tariff_id,))
        db.commit()

    def get(self, tariff_id: int) -> Optional[Dict]:
        db = get_db()
        row = db.execute("SELECT id, name, default_price, category FROM consumable_tariffs WHERE id = ?", (tariff_id,)).fetchone()
        return dict(row) if row else None
