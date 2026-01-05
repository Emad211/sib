from typing import Optional, List, Dict
from src.adapters.sqlite.core import get_db


class TariffRepository:
    """Repository for tariffs/services pricing."""

    def get_price(self, service_type: str, insurance_type: str, doctor_type: str = None) -> int:
        """
        Get price for a service based on type, insurance, and doctor type.
        For now, return a placeholder; later we'll query the services table.
        """
        # TODO: Implement real tariff lookup from services table
        # For now, return a default price
        return 50000

    def get_all_services(self) -> List[Dict]:
        """Get all services from the catalog."""
        db = get_db()
        rows = db.execute("SELECT * FROM services ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def create_service(self, name: str, base_price: int, service_type: str) -> int:
        """Create a new service/tariff."""
        db = get_db()
        cursor = db.execute(
            "INSERT INTO services (name, base_price, service_type) VALUES (?, ?, ?)",
            (name, base_price, service_type)
        )
        db.commit()
        return cursor.lastrowid
    
    def get_active_visit_tariffs(self) -> List[Dict]:
        """Get active visit tariffs (insurance types with prices)."""
        db = get_db()
        # Query visit_tariffs table for active tariffs
        rows = db.execute("""
            SELECT insurance_type, tariff_price 
            FROM visit_tariffs 
            WHERE is_active = 1 AND COALESCE(is_supplementary, 0) = 0
            ORDER BY insurance_type
        """).fetchall()
        return [{'insurance_type': r['insurance_type'], 'tariff_price': r['tariff_price']} for r in rows]

    def get_active_supplementary_insurances(self) -> List[Dict]:
        """Get active supplementary insurance names (for 'بیمه تکمیلی' dropdown)."""
        db = get_db()
        rows = db.execute("""
            SELECT insurance_type, tariff_price, nursing_tariff, is_active
            FROM visit_tariffs
            WHERE is_active = 1 AND COALESCE(is_supplementary, 0) = 1
            ORDER BY insurance_type
        """).fetchall()
        return [
            {
                'insurance_type': r['insurance_type'],
                'tariff_price': r['tariff_price'],
                'nursing_tariff': r['nursing_tariff'],
                'is_active': r['is_active']
            } for r in rows
        ]
    
    def get_visit_tariff_by_insurance(self, insurance_type: str) -> Optional[float]:
        """Get visit price for specific insurance type."""
        db = get_db()
        row = db.execute("""
            SELECT tariff_price 
            FROM visit_tariffs 
            WHERE insurance_type = ? AND is_active = 1 AND COALESCE(is_supplementary, 0) = 0
            LIMIT 1
        """, (insurance_type,)).fetchone()
        return float(row['tariff_price']) if row else 0.0

    def resolve_visit_price(self, insurance_type: Optional[str], supplementary_insurance: Optional[str]) -> float:
        """Business rule for final visit price.

        - If supplementary_insurance is provided, we'll not hard-code names here.
        - For now: prefer supplementary behavior to be handled elsewhere; default to base tariff by insurance_type.
        - Fallback 0 if not found
        """
        # If a supplementary insurance is provided and exists in visit_tariffs, prefer its tariff_price
        if supplementary_insurance and supplementary_insurance.strip():
            row = db = None
            try:
                db = get_db()
                r = db.execute("SELECT tariff_price FROM visit_tariffs WHERE insurance_type = ? AND is_active = 1 AND COALESCE(is_supplementary,0) = 1 LIMIT 1", (supplementary_insurance,)).fetchone()
                if r and r['tariff_price'] is not None:
                    return float(r['tariff_price'])
            except Exception:
                pass

        if insurance_type:
            return self.get_visit_tariff_by_insurance(insurance_type)
        return 0.0
