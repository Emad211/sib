"""Smoke test: proves the isolated webapp test harness works and stays off the real DB."""
import os

from src.config.settings import Config


def test_harness_initialises_isolated_schema(webapp_db):
    db = webapp_db
    # The active DB path must be the temp one, never the real clinic_new.db.
    assert "clinic_new.db" not in os.path.basename(Config.DATABASE_PATH)
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    # Core accounting tables exist (schema loaded).
    for t in ("users", "invoices", "visits", "injections", "procedures",
              "visit_tariffs", "invoice_item_payments"):
        assert t in tables, f"missing table {t}"


def test_isolated_db_starts_empty(webapp_db):
    db = webapp_db
    assert db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0
