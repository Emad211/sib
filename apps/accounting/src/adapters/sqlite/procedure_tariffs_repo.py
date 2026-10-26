from typing import List, Dict, Optional
from src.adapters.sqlite.core import get_db
from src.adapters.sqlite.nursing_services_repo import usage_since

class ProcedureTariffsRepository:
    """Catalog repository for procedure tariffs."""

    def list_active(self) -> List[Dict]:
        db = get_db()
        rows = db.execute(
            """SELECT t.id, t.name, t.unit_price FROM procedure_tariffs t
               LEFT JOIN (SELECT procedure_type, COUNT(*) AS n FROM procedures WHERE work_date >= ? GROUP BY procedure_type) u
                      ON u.procedure_type = t.name
               WHERE t.is_active = 1 ORDER BY COALESCE(u.n, 0) DESC, t.name""",
            (usage_since(),),
        ).fetchall()
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
