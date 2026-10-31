"""Backups, restores and the manager's backup screen (ACC-1.2–1.4; DATA-01..03, SEC-02)."""

import io
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import login_as

from src.services import backup as bk
from tools.make_synthetic_db import TEST_PASSWORD

# --------------------------------------------------------------------------- service


def _db_with_rows(path, n=10):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);"
        "CREATE TABLE patients(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE invoices(id INTEGER PRIMARY KEY, total REAL);"
    )
    conn.executemany("INSERT INTO invoices(total) VALUES (?)", [(i,) for i in range(n)])
    conn.commit()
    conn.close()


@pytest.mark.parametrize(
    "name",
    [
        "..\\clinic_new.db",
        "../clinic_new.db",
        "C:\\Windows\\x.db",
        "a/b.db",
        "a\\b.db",
        "",
        "x.txt",
        ".db",
        "..db",
        "x..db",
    ],
)
def test_unsafe_backup_names_are_rejected(tmp_path, name):
    with pytest.raises(bk.BackupError):
        bk.safe_backup_path(tmp_path, name)


def test_safe_name_resolves_inside_folder(tmp_path):
    assert bk.safe_backup_path(tmp_path, "backup_auto_20260924_040000.db").parent == tmp_path.resolve()


def test_backup_is_consistent_while_the_database_is_being_written(tmp_path):
    db = str(tmp_path / "live.db")
    _db_with_rows(db, 1000)
    stop = threading.Event()

    def writer():
        conn = sqlite3.connect(db, timeout=10)
        while not stop.is_set():
            conn.execute("INSERT INTO invoices(total) VALUES (1)")
            conn.commit()
        conn.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        made = [bk.create_backup(db, tmp_path / "b", bk.KIND_MANUAL) for _ in range(5)]
    finally:
        stop.set()
        t.join()
    for p in made:
        bk.verify_database_file(p)  # raises if not healthy


def test_backup_of_missing_database_fails_cleanly(tmp_path):
    with pytest.raises(bk.BackupError):
        bk.create_backup(tmp_path / "nope.db", tmp_path / "b", bk.KIND_MANUAL)


def test_backup_refuses_when_disk_space_is_low(tmp_path, monkeypatch):
    db = str(tmp_path / "live.db")
    _db_with_rows(db)
    real = bk.shutil.disk_usage
    monkeypatch.setattr(bk.shutil, "disk_usage", lambda p: real(p)._replace(free=1024))
    with pytest.raises(bk.BackupError, match="فضای خالی دیسک"):
        bk.create_backup(db, tmp_path / "b", bk.KIND_MANUAL)
    assert not list((tmp_path / "b").glob("*.db*"))


def test_verify_rejects_non_sqlite_and_foreign_databases(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all")
    with pytest.raises(bk.BackupError):
        bk.verify_database_file(junk)
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t(x)").connection.close()
    with pytest.raises(bk.BackupError, match="حسابداری"):
        bk.verify_database_file(other)


def test_restore_brings_back_old_content_and_keeps_a_safety_copy(tmp_path):
    db = str(tmp_path / "live.db")
    folder = tmp_path / "b"
    _db_with_rows(db, 3)
    saved = bk.create_backup(db, folder, bk.KIND_MANUAL)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM invoices")
    conn.commit()
    conn.close()
    safety = bk.restore_backup(db, folder, saved.name)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 3
    assert sqlite3.connect(safety).execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_restore_refuses_a_corrupt_backup_and_leaves_live_db_untouched(tmp_path):
    db = str(tmp_path / "live.db")
    folder = tmp_path / "b"
    folder.mkdir()
    _db_with_rows(db, 3)
    (folder / "backup_manual_20260101_000000.db").write_bytes(b"garbage")
    with pytest.raises(bk.BackupError):
        bk.restore_backup(db, folder, "backup_manual_20260101_000000.db")
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 3


def test_prune_keeps_daily_weekly_monthly_and_never_touches_manual(tmp_path):
    start = datetime(2025, 1, 1, 4, 0, 0)
    for d in range(400):
        ts = (start + timedelta(days=d)).strftime("%Y%m%d_%H%M%S")
        (tmp_path / f"backup_auto_{ts}.db").write_bytes(b"x")
    for i in range(3):
        (tmp_path / f"backup_manual_2025010{i + 1}_120000.db").write_bytes(b"x")
        (tmp_path / f"backup_uploaded_2025010{i + 1}_120000.db").write_bytes(b"x")
    for i in range(8):
        (tmp_path / f"backup_pre_migration_2025020{i + 1}_120000.db").write_bytes(b"x")
    bk.prune(tmp_path)
    names = {p.name for p in tmp_path.glob("*.db")}
    auto = sorted(n for n in names if n.startswith("backup_auto_"))
    assert len(auto) <= bk.KEEP_DAILY + bk.KEEP_WEEKLY + bk.KEEP_MONTHLY
    assert auto[-1].startswith("backup_auto_" + (start + timedelta(days=399)).strftime("%Y%m%d"))
    assert sum(n.startswith("backup_manual_") for n in names) == 3
    assert sum(n.startswith("backup_uploaded_") for n in names) == 3
    assert sum(n.startswith("backup_pre_migration_") for n in names) == bk.KEEP_PRE_MIGRATION


# --------------------------------------------------------------------------- scheduler


def test_scheduler_takes_startup_backup_when_last_one_is_old(tmp_path, monkeypatch):
    from src.services.scheduler import BackupScheduler

    db = str(tmp_path / "live.db")
    _db_with_rows(db)
    sched = BackupScheduler.for_paths(db, str(tmp_path / "b"))
    made = sched.on_startup(now=datetime(2026, 9, 24, 10, 0))
    assert made and made.name.startswith("backup_startup_")
    assert sched.on_startup(now=datetime(2026, 9, 24, 11, 0)) is None  # recent backup exists


def test_scheduler_daily_backup_after_four_and_only_once(tmp_path):
    from src.services.scheduler import BackupScheduler

    db = str(tmp_path / "live.db")
    _db_with_rows(db)
    sched = BackupScheduler.for_paths(db, str(tmp_path / "b"))
    assert sched.tick(now=datetime(2026, 9, 24, 3, 59)) is None
    first = sched.tick(now=datetime(2026, 9, 24, 4, 0))
    assert first and first.name.startswith("backup_auto_")
    assert sched.tick(now=datetime(2026, 9, 24, 23, 0)) is None


def test_scheduler_respects_auto_backup_setting(tmp_path):
    from src.services.scheduler import BackupScheduler

    db = str(tmp_path / "live.db")
    _db_with_rows(db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE settings(key TEXT UNIQUE, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('auto_backup', '0')")
    conn.commit()
    conn.close()
    sched = BackupScheduler.for_paths(db, str(tmp_path / "b"))
    assert sched.tick(now=datetime(2026, 9, 24, 5, 0)) is None


# --------------------------------------------------------------------------- manager screen


def _manager(app):
    c = app.test_client()
    login_as(c, app, "manager1")
    return c


def _backup_dir(app) -> Path:
    return Path(app.config["BACKUP_FOLDER"])


def test_manager_can_create_list_download_and_delete_a_backup(synthetic_app):
    c = _manager(synthetic_app)
    assert c.post("/manager/settings", data={"action": "create_backup"}).status_code == 302
    files = list(_backup_dir(synthetic_app).glob("backup_manual_*.db"))
    assert len(files) == 1
    page = c.get("/manager/settings").get_data(as_text=True)
    assert files[0].name in page
    r = c.get(f"/manager/settings/download/{files[0].name}")
    assert r.status_code == 200 and r.data[:16] == b"SQLite format 3\x00"
    r.close()  # release the file handle (Windows) before deleting
    c.post(
        "/manager/settings", data={"action": "delete_backup", "backup_name": files[0].name, "password": TEST_PASSWORD}
    )
    assert not files[0].exists()


@pytest.mark.parametrize("evil", ["..\\synthetic.db", "../synthetic.db", "..%5Csynthetic.db"])
def test_backup_actions_cannot_reach_the_live_database(synthetic_app, evil):
    live = Path(synthetic_app.config["DATABASE_PATH"])
    _backup_dir(synthetic_app).mkdir(exist_ok=True)
    c = _manager(synthetic_app)

    def snapshot():
        conn = sqlite3.connect(live)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            return [
                conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("patients", "invoices", "injections")
            ]
        finally:
            conn.close()

    before = snapshot()
    c.post("/manager/settings", data={"action": "delete_backup", "backup_name": evil, "password": TEST_PASSWORD})
    c.post("/manager/settings", data={"action": "restore_backup", "backup_name": evil, "password": TEST_PASSWORD})
    assert live.exists() and snapshot() == before
    r = c.get(f"/manager/settings/download/{evil}")
    assert r.status_code in (302, 404) and r.data[:16] != b"SQLite format 3\x00"


def test_upload_accepts_only_healthy_accounting_databases(synthetic_app):
    c = _manager(synthetic_app)
    bad = c.post(
        "/manager/settings/upload",
        data={"backup_file": (io.BytesIO(b"not sqlite"), "evil.db")},
        content_type="multipart/form-data",
    )
    assert bad.status_code == 302
    assert not list(_backup_dir(synthetic_app).glob("*.db"))
    with open(synthetic_app.config["DATABASE_PATH"], "rb") as f:
        good = f.read()
    c.post(
        "/manager/settings/upload",
        data={"backup_file": (io.BytesIO(good), "real.db")},
        content_type="multipart/form-data",
    )
    saved = list(_backup_dir(synthetic_app).glob("backup_uploaded_*.db"))
    assert len(saved) == 1  # in BACKUP_FOLDER (next to the exe), not inside the bundle (DATA-03)


def test_restore_from_screen_restores_data_and_keeps_pre_restore_copy(synthetic_app):
    c = _manager(synthetic_app)
    db = synthetic_app.config["DATABASE_PATH"]
    c.post("/manager/settings", data={"action": "create_backup"})
    name = next(_backup_dir(synthetic_app).glob("backup_manual_*.db")).name
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    conn.execute("DELETE FROM activity_logs")
    conn.execute("INSERT INTO patients(name, family_name) VALUES ('تازه', 'بعد از پشتیبان')")
    conn.commit()
    conn.close()
    c.post("/manager/settings", data={"action": "restore_backup", "backup_name": name, "password": TEST_PASSWORD})
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == n
    conn.close()
    assert list(_backup_dir(synthetic_app).glob("backup_pre_restore_*.db"))


def test_non_manager_cannot_touch_backups(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    c.post("/manager/settings", data={"action": "create_backup"})
    assert not os.path.exists(synthetic_app.config["BACKUP_FOLDER"]) or not list(
        _backup_dir(synthetic_app).glob("*.db")
    )


def test_restoring_an_old_schema_backup_is_migrated_immediately(synthetic_app):
    """Review finding: restoring a pre-migration (v0) backup must not leave the running
    program on an old schema until the next restart."""
    from src.adapters.sqlite.migrations import LATEST

    c = _manager(synthetic_app)
    folder = _backup_dir(synthetic_app)
    folder.mkdir(exist_ok=True)
    old = folder / "backup_manual_20260101_000000.db"
    src = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    dst = sqlite3.connect(old)
    src.backup(dst)
    src.close()
    dst.execute("DROP INDEX idx_invoices_work_date_status")
    dst.execute("PRAGMA user_version = 0")
    dst.commit()
    dst.close()
    c.post("/manager/settings", data={"action": "restore_backup", "backup_name": old.name, "password": TEST_PASSWORD})
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_invoices_work_date_status'").fetchone()
    conn.close()


def test_restore_while_database_is_locked_fails_cleanly(tmp_path):
    db = str(tmp_path / "live.db")
    folder = tmp_path / "b"
    _db_with_rows(db, 3)
    saved = bk.create_backup(db, folder, bk.KIND_MANUAL)
    blocker = sqlite3.connect(db)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(bk.BackupError, match="مشغول"):
            bk.restore_backup(db, folder, saved.name, lock_timeout=0.2)
    finally:
        blocker.rollback()
        blocker.close()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 3


# --------------------------------------------------------------------------- review hardening


def test_paths_with_hash_percent_and_spaces_work(tmp_path):
    folder = tmp_path / "Clinic #1 100% ?" if os.name != "nt" else tmp_path / "Clinic #1 100%"
    folder.mkdir()
    db = str(folder / "live.db")
    _db_with_rows(db, 4)
    made = bk.create_backup(db, folder / "b", bk.KIND_MANUAL)
    bk.verify_database_file(made)
    bk.restore_backup(db, folder / "b", made.name)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 4


def test_failed_copy_leaves_no_partial_file(tmp_path):
    db = str(tmp_path / "live.db")
    _db_with_rows(db)
    blocker = sqlite3.connect(db)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(bk.BackupError, match="مشغول"):
            bk.create_backup(db, tmp_path / "b", bk.KIND_MANUAL, lock_timeout=0.2)
    finally:
        blocker.rollback()
        blocker.close()
    assert not list((tmp_path / "b").iterdir())


def test_leftover_temp_files_are_cleaned(tmp_path):
    (tmp_path / "backup_auto_20260101_040000.db.partial").write_bytes(b"x")
    (tmp_path / ".reset_20260101_040000_ab12cd34.tmp").write_bytes(b"x")
    (tmp_path / "backup_manual_20260101_040000.db").write_bytes(b"keep")
    assert len(bk.cleanup_leftovers(tmp_path)) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["backup_manual_20260101_040000.db"]


def test_future_dated_backup_does_not_suppress_startup_backup(tmp_path):
    (tmp_path / "backup_auto_20300101_040000.db").write_bytes(b"x")  # clock was wrong once
    assert bk.latest_backup_age(tmp_path, now=datetime(2026, 9, 24, 10, 0)) is None
    (tmp_path / "backup_auto_20260924_040000.db").write_bytes(b"x")
    assert bk.latest_backup_age(tmp_path, now=datetime(2026, 9, 24, 10, 0)) == timedelta(hours=6)


def test_two_uploads_in_the_same_second_both_survive(tmp_path, monkeypatch, synthetic_app):
    monkeypatch.setattr(bk, "_timestamp", lambda now=None: "20260101_120000")
    with open(synthetic_app.config["DATABASE_PATH"], "rb") as f:
        good = f.read()

    class Upload:
        def save(self, path):
            Path(path).write_bytes(good)

    a = bk.save_uploaded_backup(tmp_path, Upload())
    b = bk.save_uploaded_backup(tmp_path, Upload())
    assert a != b and a.exists() and b.exists()


def test_scheduler_backs_off_after_a_failure(tmp_path, monkeypatch):
    from src.services.scheduler import BackupScheduler

    db = str(tmp_path / "live.db")
    _db_with_rows(db)
    sched = BackupScheduler.for_paths(db, str(tmp_path / "b"))
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise bk.BackupError("disk")

    monkeypatch.setattr(bk, "create_backup", boom)
    with pytest.raises(bk.BackupError):
        sched.tick(now=datetime(2026, 9, 24, 5, 0))
    assert sched.tick(now=datetime(2026, 9, 24, 5, 1)) is None
    assert sched.tick(now=datetime(2026, 9, 24, 5, 20)) is None
    assert len(calls) == 1
    with pytest.raises(bk.BackupError):
        sched.tick(now=datetime(2026, 9, 24, 5, 31))
    assert len(calls) == 2


def test_reset_failure_answers_json_and_keeps_data(synthetic_app, monkeypatch):
    from tools.make_synthetic_db import TEST_PASSWORD

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(bk, "copy_database", locked)
    c = _manager(synthetic_app)
    r = c.post("/manager/settings/reset-database", data={"confirm_code": "DELETE_ALL_DATA", "password": TEST_PASSWORD})
    assert r.status_code == 500 and r.is_json and "دست نخورده" in r.get_json()["error"]
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] > 0
    conn.close()
    assert not list(_backup_dir(synthetic_app).glob(".reset_*"))
