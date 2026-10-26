"""SQLite connection handling, first-run schema creation and versioned migrations.

A fresh database gets `schema.sql`; every database (fresh or existing) is then brought
to the latest version by `migrations.run_migrations` — once per process. Migration
failures are NOT swallowed any more (DATA-04): `MigrationError` propagates so the
program refuses to run on a half-migrated schema; `start.py` shows it to the user.
"""

import logging
import os
import pkgutil
import sqlite3
import sys

from flask import current_app, g, has_app_context

from src.adapters.sqlite.migrations import MigrationError, MigrationReport, run_migrations
from src.config.settings import Config

log = logging.getLogger(__name__)

# Module-level flag: migrations run once per process, not per request.
_migrations_done = False
last_migration_report: MigrationReport | None = None

__all__ = ["get_db", "close_connection", "ensure_database_ready", "MigrationError", "last_migration_report"]


def _read_schema_sql() -> str:
    """Load bundled schema.sql in source and PyInstaller (frozen) modes."""
    try:
        data = pkgutil.get_data("src.adapters.sqlite", "schema.sql")
        if data:
            return data.decode("utf-8")
    except Exception:  # noqa: BLE001 — fall through to the file-system candidates
        log.debug("schema.sql not available through pkgutil", exc_info=True)
    candidates = [os.path.join(os.path.dirname(__file__), "schema.sql")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [
            os.path.join(meipass, "src", "adapters", "sqlite", "schema.sql"),
            os.path.join(meipass, "schema.sql"),
        ]
    candidates.append(os.path.join(Config.PROJECT_ROOT, "schema.sql"))
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
    raise FileNotFoundError("schema.sql not found in package data or fallback locations")


def _load_schema_and_initialize(db: sqlite3.Connection) -> None:
    db.executescript(_read_schema_sql())


def _backup_folder() -> str:
    if has_app_context():
        folder = current_app.config.get("BACKUP_FOLDER")
        if folder:
            return folder
    return Config.BACKUP_FOLDER


def _prepare(db: sqlite3.Connection, db_path: str) -> MigrationReport:
    """Create the schema on an empty file, then apply pending migrations."""
    has_users = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    fresh = not has_users
    if fresh:
        _load_schema_and_initialize(db)

    def backup():
        if fresh or db_path == ":memory:":
            return None
        from src.services.backup import KIND_PRE_MIGRATION, create_backup

        return create_backup(db_path, _backup_folder(), KIND_PRE_MIGRATION).name

    return run_migrations(db, backup=backup)


def _connect(db_path: str) -> sqlite3.Connection:
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def ensure_database_ready(db_path: str | None = None) -> MigrationReport:
    """Run schema creation + migrations outside a request (called by start.py)."""
    global _migrations_done, last_migration_report
    path = db_path or Config.DATABASE_PATH
    db = _connect(path)
    try:
        last_migration_report = _prepare(db, path)
    finally:
        db.close()
    _migrations_done = True
    return last_migration_report


def get_db() -> sqlite3.Connection:
    global _migrations_done, last_migration_report
    db = getattr(g, "_database", None)
    if db is None:
        db_path = Config.DATABASE_PATH
        db = g._database = _connect(db_path)
        if not _migrations_done:
            last_migration_report = _prepare(db, db_path)
            _migrations_done = True
    return db


def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()
        g._database = None


def init_db():
    """Ensure the configured database exists and is migrated."""
    return get_db()


def init_db_command():
    """Re-run schema.sql (idempotent CREATE ... IF NOT EXISTS) and migrations."""
    db = get_db()
    _load_schema_and_initialize(db)
    run_migrations(db)
    print("Initialized the database.")
