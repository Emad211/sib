"""v7 — voids for the shift app, and money views that count closed invoices only (ACC-2.11, S04).

The shift app reads accounting through the `v_shift_*_v1` views and nothing else.

* `v_shift_voids_v1` — one row per voided or refunded invoice, so the shift app can take a cancelled
  payment back out of its handover. The join to `patients` keeps the contract honest: a void is only
  readable while its invoice still resolves to a patient row (patients are never deleted).
* Money bug fixed here, not in v4: `v_shift_cash_receipts_v1` and `v_shift_payment_lines_v1` copied
  `invoice_payment_lines` without looking at the invoice. A voided invoice keeps its lines (v6), so
  cash that was handed back stayed in the shift handover; a reopened invoice loses its lines, but the
  status filter is the second line of defence. Both views now join `invoices` on `status = 'closed'`;
  column names and their order are unchanged, so `_v1` readers see the same shape.
* `idx_patients_created_at` / `idx_invoice_voids_voided_at` — the shift app reads registrations and
  voids by watermark ("everything since"), so both tables need the timestamp indexed.

Views and indexes only; the history in v4 and v6 stays immutable (ADR-002). Older program versions
run on this database untouched because they never read these views.
"""

import sqlite3

CLOSED_ONLY_VIEWS: dict[str, str] = {
    "v_shift_payment_lines_v1": """
CREATE VIEW v_shift_payment_lines_v1 AS
SELECT l.invoice_id, l.item_type, l.item_id, l.payment_type, l.patient_amount AS amount, l.closed_at
FROM invoice_payment_lines l
JOIN invoices i ON i.id = l.invoice_id AND i.status = 'closed'
""",
    "v_shift_cash_receipts_v1": """
CREATE VIEW v_shift_cash_receipts_v1 AS
SELECT l.invoice_id, l.closed_at, SUM(l.patient_amount) AS amount
FROM invoice_payment_lines l
JOIN invoices i ON i.id = l.invoice_id AND i.status = 'closed'
WHERE l.payment_type = 'cash' AND l.patient_amount > 0
GROUP BY l.invoice_id, l.closed_at
""",
}

VOIDS_VIEWS: dict[str, str] = {
    "v_shift_voids_v1": """
CREATE VIEW v_shift_voids_v1 AS
SELECT v.id AS void_id,
       v.invoice_id,
       v.kind,
       v.amount,
       v.refund_method,
       v.reason,
       v.voided_by,
       v.voided_at,
       i.work_date,
       i.shift
FROM invoice_voids v
JOIN invoices i ON i.id = v.invoice_id
JOIN patients p ON p.id = i.patient_id
""",
}


def _replace(conn: sqlite3.Connection, views: dict[str, str]) -> None:
    """Drop and recreate: `CREATE VIEW IF NOT EXISTS` would silently keep the old (wrong) body."""
    for name, sql in views.items():
        conn.execute(f"DROP VIEW IF EXISTS {name}")
        conn.execute(sql)


def up(conn: sqlite3.Connection) -> None:
    _replace(conn, CLOSED_ONLY_VIEWS)
    _replace(conn, VOIDS_VIEWS)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_patients_created_at ON patients (created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_voids_voided_at ON invoice_voids (voided_at)")
