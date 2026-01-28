import sqlite3
import pkgutil
import os
from flask import g
from src.config.settings import Config
import sys

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
        pass


def _load_schema_and_initialize(db):
    """Load bundled schema.sql (works in source and frozen modes) and run it."""
    # Try to load schema from package data (works when bundled by PyInstaller)
    schema_bytes = None
    try:
        schema_bytes = pkgutil.get_data('src.adapters.sqlite', 'schema.sql')
    except Exception:
        schema_bytes = None

    if schema_bytes:
        schema_text = schema_bytes.decode('utf-8')
    else:
        schema_text = None

        # Fallback 1: read next to this module (development / some bundling modes)
        try:
            schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
            if os.path.exists(schema_path):
                with open(schema_path, 'r', encoding='utf-8') as f:
                    schema_text = f.read()
        except Exception:
            schema_text = None

        # Fallback 2: PyInstaller onefile extraction dir
        if schema_text is None:
            try:
                meipass = getattr(sys, '_MEIPASS', None)
                if meipass:
                    # ابتدا در مسیر src/adapters/sqlite بگرد
                    schema_path = os.path.join(meipass, 'src', 'adapters', 'sqlite', 'schema.sql')
                    if os.path.exists(schema_path):
                        with open(schema_path, 'r', encoding='utf-8') as f:
                            schema_text = f.read()
                    else:
                        # سپس در ریشه _MEIPASS بگرد
                        schema_path = os.path.join(meipass, 'schema.sql')
                        if os.path.exists(schema_path):
                            with open(schema_path, 'r', encoding='utf-8') as f:
                                schema_text = f.read()
            except Exception:
                schema_text = None

        # Fallback 3: next to executable / project root
        if schema_text is None:
            try:
                schema_path = os.path.join(Config.PROJECT_ROOT, 'schema.sql')
                if os.path.exists(schema_path):
                    with open(schema_path, 'r', encoding='utf-8') as f:
                        schema_text = f.read()
            except Exception:
                schema_text = None

        if schema_text is None:
            raise FileNotFoundError('schema.sql not found in package data or fallback locations')

    db.executescript(schema_text)


# Module-level flag to track if migrations have run in this process
_migrations_done = False


def get_db():
    global _migrations_done
    db = getattr(g, '_database', None)
    if db is None:
        # Ensure directory exists for DB file
        db_path = Config.DATABASE_PATH
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except Exception:
                pass

        # Connect (this will create the file if missing)
        db = g._database = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row

        # Simple check: if users table missing, initialize schema
        try:
            cur = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            if not cur.fetchone():
                _load_schema_and_initialize(db)
                _migrations_done = False  # Force migrations after schema init
        except Exception:
            # If anything goes wrong here, try to initialize schema anyway
            try:
                _load_schema_and_initialize(db)
                _migrations_done = False
            except Exception:
                pass

        # Run migrations only ONCE per process (not per request)
        if not _migrations_done:
            _ensure_work_date_columns(db)
            _ensure_indexes(db)  # Create performance indexes
            _ensure_settings_table(db)  # Create settings table if missing
            _migrations_done = True

    return db

def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Initialize the database with the schema."""
    db = get_db()
    # We will execute schema creation scripts here
    # For now, we just ensure connection works
    return db

def init_db_command():
    """Clear the existing data and create new tables."""
    db = get_db()
    
    # Read schema file
    import os
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    
    with open(schema_path, 'r', encoding='utf-8') as f:
        db.executescript(f.read())
    
    print('Initialized the database.')
