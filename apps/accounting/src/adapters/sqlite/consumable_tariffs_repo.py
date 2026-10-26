from typing import List, Dict, Optional
from src.adapters.sqlite.core import get_db
from src.adapters.sqlite.nursing_services_repo import usage_since

class ConsumableTariffsRepository:
    def list_active(self, category: Optional[str] = None) -> List[Dict]:
        db = get_db()
        usage = """SELECT t.id, t.name, t.default_price, t.category FROM consumable_tariffs t
                   LEFT JOIN (SELECT item_name, COUNT(*) AS n FROM consumables_ledger WHERE work_date >= ? GROUP BY item_name) u
                          ON u.item_name = t.name
                   WHERE t.is_active = 1"""
        if category:
            rows = db.execute(usage + " AND t.category = ? ORDER BY COALESCE(u.n, 0) DESC, t.name", (usage_since(), category)).fetchall()
        else:
            rows = db.execute(usage + " ORDER BY t.category, COALESCE(u.n, 0) DESC, t.name", (usage_since(),)).fetchall()
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
