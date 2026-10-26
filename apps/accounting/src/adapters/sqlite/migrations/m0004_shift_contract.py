"""v4 — what the shift app reads from accounting (ACC-2.11, docs/shift/S04).

Everything here is additive, so older program versions keep working on this database (ADR-002):

* `consumable_tariffs.acc_code` / `stock_factor` — the manager maps each consumable tariff to the
  stock item code the shift app counts, and says how many stock units one ledger unit is.
* `invoice_payment_lines` — what each item cost the patient and how it was paid, written once when an
  invoice is closed. The patient's share is *computed* from tariffs (not stored per item), so
  recomputing later would give another answer after a tariff change; cash that was handed over is a
  fact and must not change. Forward-only: invoices closed by older programs have no rows.
* `idx_invoices_closed_at` — the shift app reads "closed since the watermark".
* `v_shift_*_v1` views — the only thing the shift app may read. A breaking change gets a `_v2` view;
  `_v1` stays until the shift app has moved.

The views never expose a phone number or a national ID, only whether each is valid.
"""

import sqlite3

VIEWS_V1: dict[str, str] = {}


def _valid_national_id_sql(col: str) -> str:
    """Pure-SQL version of `domain.identity.is_valid_national_id` (checked against it by a test)."""
    digits = " + ".join(f"CAST(substr({col}, {i}, 1) AS INTEGER) * {10 - i + 1}" for i in range(1, 10))
    same = ", ".join(f"'{d * 10}'" for d in "0123456789")
    return (
        f"({col} IS NOT NULL AND length({col}) = 10 AND {col} NOT GLOB '*[^0-9]*' AND {col} NOT IN ({same}) "
        f"AND CAST(substr({col}, 10, 1) AS INTEGER) = "
        f"CASE WHEN ({digits}) % 11 < 2 THEN ({digits}) % 11 ELSE 11 - ({digits}) % 11 END)"
    )


def _valid_phone_sql(col: str) -> str:
    return f"({col} IS NOT NULL AND length({col}) = 11 AND substr({col}, 1, 2) = '09' AND {col} NOT GLOB '*[^0-9]*')"


VIEWS_V1["v_shift_open_invoices_v1"] = """
CREATE VIEW v_shift_open_invoices_v1 AS
SELECT id AS invoice_id, opened_at, opened_by, shift, work_date
FROM invoices
WHERE status = 'open'
"""

VIEWS_V1["v_shift_invoice_lookup_v1"] = """
CREATE VIEW v_shift_invoice_lookup_v1 AS
SELECT id AS invoice_id, patient_id, status, total_amount AS total, closed_at
FROM invoices
"""

VIEWS_V1["v_shift_consumption_v1"] = """
CREATE VIEW v_shift_consumption_v1 AS
SELECT l.id AS ledger_id,
       l.invoice_id,
       i.closed_at,
       (SELECT t.acc_code FROM consumable_tariffs t WHERE t.name = l.item_name
        ORDER BY t.is_active DESC, t.id LIMIT 1) AS acc_code,
       l.quantity * COALESCE((SELECT t.stock_factor FROM consumable_tariffs t WHERE t.name = l.item_name
                              ORDER BY t.is_active DESC, t.id LIMIT 1), 1.0) AS quantity,
       l.item_name
FROM consumables_ledger l
JOIN invoices i ON i.id = l.invoice_id AND i.status = 'closed'
WHERE COALESCE(l.patient_provided, 0) = 0
"""

VIEWS_V1["v_shift_payment_lines_v1"] = """
CREATE VIEW v_shift_payment_lines_v1 AS
SELECT invoice_id, item_type, item_id, payment_type, patient_amount AS amount, closed_at
FROM invoice_payment_lines
"""

VIEWS_V1["v_shift_cash_receipts_v1"] = """
CREATE VIEW v_shift_cash_receipts_v1 AS
SELECT invoice_id, closed_at, SUM(patient_amount) AS amount
FROM invoice_payment_lines
WHERE payment_type = 'cash' AND patient_amount > 0
GROUP BY invoice_id, closed_at
"""

VIEWS_V1["v_shift_vitals_services_v1"] = """
CREATE VIEW v_shift_vitals_services_v1 AS
SELECT j.id AS injection_id, j.invoice_id, j.nurse_id, j.injection_type AS type, i.closed_at
FROM injections j
JOIN invoices i ON i.id = j.invoice_id AND i.status = 'closed'
WHERE j.injection_type IN ('تست قند', 'کنترل فشار')
"""

VIEWS_V1["v_shift_patient_registrations_v1"] = f"""
CREATE VIEW v_shift_patient_registrations_v1 AS
SELECT p.id AS patient_id, p.created_by, p.created_at,
       CASE WHEN {_valid_national_id_sql("p.national_id")} THEN 1 ELSE 0 END AS has_valid_nid,
       CASE WHEN {_valid_phone_sql("p.phone_number")} THEN 1 ELSE 0 END AS has_valid_phone,
       COALESCE(p.is_foreign, 0) AS is_foreign
FROM patients p
"""


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def up(conn: sqlite3.Connection) -> None:
    if not _has_column(conn, "consumable_tariffs", "acc_code"):
        conn.execute("ALTER TABLE consumable_tariffs ADD COLUMN acc_code TEXT")
    if not _has_column(conn, "consumable_tariffs", "stock_factor"):
        conn.execute("ALTER TABLE consumable_tariffs ADD COLUMN stock_factor REAL DEFAULT 1")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS invoice_payment_lines (
               invoice_id INTEGER NOT NULL,
               item_type TEXT NOT NULL,
               item_id INTEGER NOT NULL,
               payment_type TEXT,
               patient_amount REAL NOT NULL DEFAULT 0,
               closed_at TIMESTAMP,
               PRIMARY KEY (invoice_id, item_type, item_id)
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_lines_closed_at ON invoice_payment_lines (closed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_closed_at ON invoices (closed_at)")
    for name, sql in VIEWS_V1.items():
        conn.execute(f"DROP VIEW IF EXISTS {name}")
        conn.execute(sql)
