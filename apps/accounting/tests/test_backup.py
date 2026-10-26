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
    c.post("/manager/settings", data={"action": "delete_backup", "backup_name": files[0].name})
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
    c.post("/manager/settings", data={"action": "delete_backup", "backup_name": evil})
    c.post("/manager/settings", data={"action": "restore_backup", "backup_name": evil})
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
    c.post("/manager/settings", data={"action": "restore_backup", "backup_name": name})
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
