"""v1 — formalise the runtime `_ensure_*` steps that every version up to 1.x ran at startup.

On the production database (2026-09-24) this only adds `users.staff_id`, its index and
the `shift_staff` table if missing; everything else already exists. Backfills touch only
NULL/empty values, exactly as the old startup code did.
"""

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_xinfo("{table}")')}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')


def up(conn: sqlite3.Connection) -> None:
    for table in ("invoices", "visits", "injections", "procedures", "consumables_ledger"):
        _add_column(conn, table, "work_date", "TEXT")
    _add_column(conn, "invoices", "shift", "TEXT")
    _add_column(conn, "users", "staff_id", "INTEGER")

    conn.execute("UPDATE invoices SET work_date = substr(opened_at, 1, 10) WHERE work_date IS NULL OR work_date = ''")
    conn.execute(
        """UPDATE invoices SET shift = CASE
               WHEN strftime('%H', opened_at) BETWEEN '07' AND '13' THEN 'morning'
               WHEN strftime('%H', opened_at) BETWEEN '14' AND '19' THEN 'evening'
               ELSE 'night' END
           WHERE shift IS NULL OR shift = ''"""
    )
    for table, ts in (
        ("visits", "visit_date"),
        ("injections", "injection_date"),
        ("procedures", "procedure_date"),
        ("consumables_ledger", "usage_date"),
    ):
        conn.execute(f"UPDATE {table} SET work_date = substr({ts}, 1, 10) WHERE work_date IS NULL OR work_date = ''")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               key TEXT UNIQUE NOT NULL,
               value TEXT,
               created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
               updated_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')))"""
    )
    # Previously created lazily by the shift-staff repository on first use.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shift_staff (
               work_date TEXT NOT NULL,
               shift TEXT NOT NULL,
               doctor_id INTEGER,
               nurse_id INTEGER,
               PRIMARY KEY (work_date, shift))"""
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices (status)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_work_date ON invoices (work_date)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_patient_id ON invoices (patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_invoices_status_opened_at ON invoices (status, opened_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_visits_invoice_id ON visits (invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_visits_work_date ON visits (work_date)",
        "CREATE INDEX IF NOT EXISTS idx_visits_patient_id ON visits (patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_injections_invoice_id ON injections (invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_injections_work_date ON injections (work_date)",
        "CREATE INDEX IF NOT EXISTS idx_injections_patient_id ON injections (patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_procedures_invoice_id ON procedures (invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_procedures_work_date ON procedures (work_date)",
        "CREATE INDEX IF NOT EXISTS idx_procedures_patient_id ON procedures (patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_consumables_invoice_id ON consumables_ledger (invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_consumables_work_date ON consumables_ledger (work_date)",
        "CREATE INDEX IF NOT EXISTS idx_patients_national_id ON patients (national_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_activity_logs_user_id ON activity_logs (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_logs_invoice_id ON activity_logs (invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_medical_staff_type_active ON medical_staff (staff_type, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_payments_invoice_id ON invoice_item_payments (invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_staff_id ON users (staff_id)",
    ):
        conn.execute(sql)
