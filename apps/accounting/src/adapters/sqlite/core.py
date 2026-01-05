import sqlite3
import pkgutil
import os
from flask import g
from src.config.settings import Config


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
        # Fallback to reading from filesystem (development)
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema_text = f.read()

    db.executescript(schema_text)


def get_db():
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
        except Exception:
            # If anything goes wrong here, try to initialize schema anyway
            try:
                _load_schema_and_initialize(db)
            except Exception:
                pass

        # Ensure migrations that are safe to run repeatedly.
        _ensure_work_date_columns(db)

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
