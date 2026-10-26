from typing import List, Dict, Optional
from src.adapters.sqlite.core import get_db


USAGE_WINDOW_DAYS = 90


def usage_since() -> str:
    """Lists are ordered by how often each item was used recently (FB-06): most used first."""
    from datetime import timedelta

    from src.common.utils import iran_now

    return (iran_now() - timedelta(days=USAGE_WINDOW_DAYS)).strftime("%Y-%m-%d")


class NursingServicesRepository:
    def list_active(self) -> List[Dict]:
        db = get_db()
        rows = db.execute(
            """SELECT s.id, s.service_name, s.unit_price FROM nursing_services s
               LEFT JOIN (SELECT service_id, COUNT(*) AS n FROM injections WHERE work_date >= ? GROUP BY service_id) u
                      ON u.service_id = s.id
               WHERE s.is_active = 1 ORDER BY COALESCE(u.n, 0) DESC, s.service_name""",
            (usage_since(),),
        ).fetchall()
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
