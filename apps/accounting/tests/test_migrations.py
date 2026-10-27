"""Versioned migrations (ACC-1.1, ADR-002, docs/accounting/A03 §4)."""

import os
import sqlite3

import pytest
from conftest import Config, core

from src.adapters.sqlite import migrations
from src.adapters.sqlite.migrations import LATEST, MigrationError, run_migrations
from tools.schema_diff import describe, diff

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
PROD_SCHEMA = os.path.join(GOLDEN, "clinic_new_2026-09-24.schema.sql")


def _prod_shaped_v0(path: str) -> None:
    """An empty database with exactly the production schema of 2026-09-24 (user_version 0)."""
    with open(PROD_SCHEMA, encoding="utf-8") as f:
        ddl = f.read()
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    conn.execute("INSERT INTO users (username, password_hash, role, full_name) VALUES ('m', x'00', 'manager', 'm')")
    conn.execute("INSERT INTO patients (name, family_name) VALUES ('الف', 'ب')")
    conn.execute(
        "INSERT INTO invoices (patient_id, status, opened_at, work_date, shift) "
        "VALUES (1, 'closed', '2026-09-01 10:00:00', '2026-09-01', 'morning')"
    )
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


@pytest.fixture()
def v0_db(tmp_path):
    path = str(tmp_path / "prod_v0.db")
    _prod_shaped_v0(path)
    return path


def _open(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def test_fresh_database_is_created_at_latest_version(tmp_path):
    path = str(tmp_path / "fresh.db")
    report = core.ensure_database_ready(path)
    conn = _open(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert report.backup is None  # nothing to protect on an empty file
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "invoices", "shift_staff", "settings"} <= tables
    conn.close()


def test_production_shaped_database_is_migrated_additively(v0_db, tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "BACKUP_FOLDER", str(tmp_path / "backups"))
    before = describe(v0_db)
    report = core.ensure_database_ready(v0_db)
    after = describe(v0_db)
    bad, added = diff(before, after)
    assert not bad
    # Exactly what production gains — nothing more (the prod schema already has the rest).
    assert sorted(added) == sorted(
        [
            "column added: consumable_tariffs.acc_code",
            "column added: consumable_tariffs.stock_factor",
            "column added: users.staff_id",
            "index added: idx_expenses_spent_on",
            "index added: idx_invoices_closed_at",
            "index added: idx_injections_doctor_work_date",
            "index added: idx_injections_nurse_work_date",
            "index added: idx_visits_doctor_work_date",
            "index added: idx_procedures_doctor_work_date",
            "index added: idx_procedures_nurse_work_date",
            "index added: idx_invoices_patient_work_date",
            "index added: idx_invoices_work_date_status",
            "index added: idx_payment_lines_closed_at",
            "index added: idx_tariff_changes_changed_at",
            "index added: idx_users_staff_id",
            "table added: expenses",
            "table added: invoice_payment_lines",
            "table added: invoice_voids",
            "table added: tariff_changes",
            "view added: v_shift_cash_receipts_v1",
            "view added: v_shift_consumption_v1",
            "view added: v_shift_invoice_lookup_v1",
            "view added: v_shift_open_invoices_v1",
            "view added: v_shift_patient_registrations_v1",
            "view added: v_shift_payment_lines_v1",
            "view added: v_shift_vitals_services_v1",
        ]
    )
    assert after["user_version"] == LATEST
    assert report.applied == [f"{v:04d}_{n}" for v, n, _ in migrations.MIGRATIONS]
    conn = _open(v0_db)
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_backup_is_taken_before_migrating_an_existing_database(v0_db, tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    monkeypatch.setattr(Config, "BACKUP_FOLDER", str(backups))
    report = core.ensure_database_ready(v0_db)
    assert report.backup and report.backup.startswith("backup_pre_migration_")
    saved = _open(str(backups / report.backup))
    assert saved.execute("PRAGMA user_version").fetchone()[0] == 0  # the untouched original
    assert saved.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    saved.close()


def test_migrations_are_idempotent_and_back_up_only_once(v0_db, tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    monkeypatch.setattr(Config, "BACKUP_FOLDER", str(backups))
    core.ensure_database_ready(v0_db)
    schema = describe(v0_db)
    second = core.ensure_database_ready(v0_db)
    assert second.applied == [] and second.backup is None
    assert describe(v0_db) == schema
    assert len(list(backups.glob("*.db"))) == 1


def test_failed_migration_rolls_back_and_raises(v0_db, monkeypatch):
    def broken(conn):
        conn.execute("ALTER TABLE users ADD COLUMN half_done INTEGER")
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS + [(LATEST + 1, "broken", broken)])
    conn = _open(v0_db)
    with pytest.raises(MigrationError, match="هیچ تغییری ثبت نشد"):
        run_migrations(conn)
    # earlier steps committed, the failing one left no trace
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert "half_done" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    conn.close()


def test_backup_failure_stops_the_upgrade(v0_db):
    def no_backup():
        raise OSError("disk full")

    conn = _open(v0_db)
    with pytest.raises(MigrationError, match="پشتیبان"):
        run_migrations(conn, backup=no_backup)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


def test_database_newer_than_program_is_left_alone(v0_db):
    conn = _open(v0_db)
    conn.execute(f"PRAGMA user_version = {LATEST + 5}")
    before = describe(v0_db)
    report = run_migrations(conn, backup=lambda: pytest.fail("must not back up"))
    assert report.newer_than_code and report.applied == []
    conn.close()
    assert describe(v0_db) == before


def test_previous_program_version_still_opens_a_migrated_database(v0_db, tmp_path, monkeypatch):
    """ADR-002 rule 5: roll back = run the old exe on the new database."""
    from legacy.core_v1_startup import run_legacy_startup

    monkeypatch.setattr(Config, "BACKUP_FOLDER", str(tmp_path / "backups"))
    core.ensure_database_ready(v0_db)
    migrated = describe(v0_db)
    conn = _open(v0_db)
    run_legacy_startup(conn)  # what HesabdariSib 1.x does at every start
    conn.execute("SELECT id, staff_id FROM users").fetchall()
    conn.execute("SELECT work_date, shift FROM invoices WHERE status='closed'").fetchall()
    conn.close()
    bad, _ = diff(migrated, describe(v0_db))
    assert not bad


def test_get_db_runs_migrations_once_per_process(synthetic_app):
    with synthetic_app.app_context():
        db = core.get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == LATEST
        assert core._migrations_done


def test_locked_database_gives_a_clear_migration_error(v0_db):
    blocker = sqlite3.connect(v0_db)
    blocker.execute("BEGIN EXCLUSIVE")
    conn = sqlite3.connect(v0_db, timeout=0.1)
    try:
        with pytest.raises(MigrationError, match="قفل|مرحلهٔ"):
            run_migrations(conn)
    finally:
        blocker.rollback()
        blocker.close()
        conn.close()
