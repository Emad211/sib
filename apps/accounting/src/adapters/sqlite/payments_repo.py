from typing import List, Dict, Optional
from src.adapters.sqlite.core import get_db

class InvoiceItemPaymentRepository:
    """Repository for invoice item payment tracking."""

    def set_payment(self, invoice_id: int, item_type: str, item_id: int,
                    payment_type: Optional[str], is_paid: bool) -> None:
        db = get_db()
        db.execute(
            '''INSERT INTO invoice_item_payments (invoice_id, item_type, item_id, payment_type, is_paid)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(invoice_id, item_type, item_id) DO UPDATE SET
                   payment_type = excluded.payment_type,
                   is_paid = excluded.is_paid,
                   updated_at = datetime('now', '+3 hours', '+30 minutes')''',
            (invoice_id, item_type, item_id, payment_type, 1 if is_paid else 0)
        )
        db.commit()

    def get_payments_for_invoice(self, invoice_id: int) -> List[Dict]:
        db = get_db()
        rows = db.execute(
            '''SELECT invoice_id, item_type, item_id, payment_type, is_paid, updated_at
               FROM invoice_item_payments WHERE invoice_id = ?''',
            (invoice_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_item_payment(self, invoice_id: int, item_type: str, item_id: int) -> Optional[Dict]:
        db = get_db()
        row = db.execute(
            '''SELECT invoice_id, item_type, item_id, payment_type, is_paid, updated_at
               FROM invoice_item_payments
               WHERE invoice_id = ? AND item_type = ? AND item_id = ?''',
            (invoice_id, item_type, item_id)
        ).fetchone()
        return dict(row) if row else None

    def calculate_paid_total(self, invoice_id: int) -> float:
        db = get_db()
        # Sum amounts of items marked paid. Need to join to each item table.
        # Visits
        visits_paid = db.execute('''
            SELECT COALESCE(SUM(v.price),0) FROM visits v
            JOIN invoice_item_payments p ON p.item_id = v.id AND p.item_type = 'visit' AND p.invoice_id = v.invoice_id
            WHERE v.invoice_id = ? AND p.is_paid = 1
        ''', (invoice_id,)).fetchone()[0]
        # Injections
        injections_paid = db.execute('''
            SELECT COALESCE(SUM(i.total_price),0) FROM injections i
            JOIN invoice_item_payments p ON p.item_id = i.id AND p.item_type = 'injection' AND p.invoice_id = i.invoice_id
            WHERE i.invoice_id = ? AND p.is_paid = 1
        ''', (invoice_id,)).fetchone()[0]
        # Procedures
        procedures_paid = db.execute('''
            SELECT COALESCE(SUM(pr.price),0) FROM procedures pr
            JOIN invoice_item_payments p ON p.item_id = pr.id AND p.item_type = 'procedure' AND p.invoice_id = pr.invoice_id
            WHERE pr.invoice_id = ? AND p.is_paid = 1
        ''', (invoice_id,)).fetchone()[0]
        # Consumables
        consumables_paid = db.execute('''
            SELECT COALESCE(SUM(c.total_cost),0) FROM consumables_ledger c
            JOIN invoice_item_payments p ON p.item_id = c.id AND p.item_type = 'consumable' AND p.invoice_id = c.invoice_id
            WHERE c.invoice_id = ? AND p.is_paid = 1
        ''', (invoice_id,)).fetchone()[0]
        return float(visits_paid or 0) + float(injections_paid or 0) + float(procedures_paid or 0) + float(consumables_paid or 0)
