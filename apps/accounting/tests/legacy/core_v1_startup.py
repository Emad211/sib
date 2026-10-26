"""Frozen copy of the startup migrations shipped up to v1.x (src/adapters/sqlite/core.py at
clinic_managment origin/main 89fcf258). Used only to prove that the previous exe still opens a
database migrated by the current code (ADR-002 rule 5). Do not edit.
"""
# ruff: noqa

def _ensure_column(db, table: str, column: str, decl_sql: str) -> None:
    try:
        cols = db.execute(f"PRAGMA table_info({table})").fetchall()
        if any(c["name"] == column for c in cols):
            return
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl_sql}")
        db.commit()
    except Exception:
        # Keep app usable even if migration fails.
        pass


def _ensure_work_date_columns(db) -> None:
    """Ensure `work_date` exists on operational tables.

    Manual shifts can span midnight (especially night shift). Relying on
    DATE(timestamp) splits a single work shift across two calendar dates.
    We persist `work_date` so reports/ledgers can stay consistent.
    """
    _ensure_column(db, "invoices", "work_date", "TEXT")
    _ensure_column(db, "invoices", "shift", "TEXT")
    _ensure_column(db, "visits", "work_date", "TEXT")
    _ensure_column(db, "injections", "work_date", "TEXT")
    _ensure_column(db, "procedures", "work_date", "TEXT")
    _ensure_column(db, "consumables_ledger", "work_date", "TEXT")

    # Best-effort backfill for existing rows.
    try:
        db.execute("UPDATE invoices SET work_date = substr(opened_at, 1, 10) WHERE work_date IS NULL OR work_date = ''")
        
        # Backfill shift for invoices based on hour
        db.execute("""
            UPDATE invoices SET shift = CASE 
                WHEN strftime('%H', opened_at) BETWEEN '07' AND '13' THEN 'morning'
                WHEN strftime('%H', opened_at) BETWEEN '14' AND '19' THEN 'evening'
                ELSE 'night'
            END
            WHERE shift IS NULL OR shift = ''
        """)

        db.execute("UPDATE visits SET work_date = substr(visit_date, 1, 10) WHERE work_date IS NULL OR work_date = ''")
        db.execute("UPDATE injections SET work_date = substr(injection_date, 1, 10) WHERE work_date IS NULL OR work_date = ''")
        db.execute("UPDATE procedures SET work_date = substr(procedure_date, 1, 10) WHERE work_date IS NULL OR work_date = ''")
        db.execute("UPDATE consumables_ledger SET work_date = substr(usage_date, 1, 10) WHERE work_date IS NULL OR work_date = ''")
        db.commit()
    except Exception:
        pass


def _ensure_user_staff_link(db) -> None:
    """Ensure optional linkage between users and medical_staff.

    Used for doctor accounts created from the management panel.
    """
    _ensure_column(db, "users", "staff_id", "INTEGER")


def _ensure_indexes(db) -> None:
    """Create performance indexes if they don't exist."""
    try:
        # Invoices indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices (status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_invoices_work_date ON invoices (work_date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_invoices_patient_id ON invoices (patient_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_invoices_status_opened_at ON invoices (status, opened_at DESC)")
        
        # Visits indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_visits_invoice_id ON visits (invoice_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_visits_work_date ON visits (work_date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_visits_patient_id ON visits (patient_id)")
        
        # Injections indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_injections_invoice_id ON injections (invoice_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_injections_work_date ON injections (work_date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_injections_patient_id ON injections (patient_id)")
        
        # Procedures indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_procedures_invoice_id ON procedures (invoice_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_procedures_work_date ON procedures (work_date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_procedures_patient_id ON procedures (patient_id)")
        
        # Consumables indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_consumables_invoice_id ON consumables_ledger (invoice_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_consumables_work_date ON consumables_ledger (work_date)")
        
        # Patients indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_patients_national_id ON patients (national_id)")
        
        # Activity logs indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs (created_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_user_id ON activity_logs (user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_invoice_id ON activity_logs (invoice_id)")
        
        # Medical staff indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_medical_staff_type_active ON medical_staff (staff_type, is_active)")
        
        # Payments indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_payments_invoice_id ON invoice_item_payments (invoice_id)")

        # Users indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_staff_id ON users (staff_id)")
        
        db.commit()
    except Exception:
        pass


def _ensure_settings_table(db) -> None:
    """Ensure settings table exists for existing databases."""
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
                updated_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes'))
            )
        """)
        db.commit()
    except Exception:
        pass


def run_legacy_startup(db):
    _ensure_work_date_columns(db)
    _ensure_user_staff_link(db)
    _ensure_indexes(db)
    _ensure_settings_table(db)
