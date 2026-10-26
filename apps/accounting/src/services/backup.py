"""Database backups and restores (ADR-008, ACC-1.2–1.4).

Every copy goes through SQLite's online backup API, so it is consistent even while
the application is writing, and every copy is verified with `PRAGMA integrity_check`.
File names inside the backup folder are validated before any read/write/delete, so a
name can never point outside that folder (SEC-02).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from src.common.utils import iran_now

log = logging.getLogger(__name__)

# Kinds written by the application. Legacy names (backup_auto_*, backup_*, pre_restore_*,
# uploaded_*) produced by older versions are still listed and restorable.
KIND_AUTO = "auto"  # daily, rotated
KIND_STARTUP = "startup"  # when the app starts and the last backup is old, rotated
KIND_MANUAL = "manual"  # manager clicked "backup now", never rotated
KIND_PRE_MIGRATION = "pre_migration"
KIND_PRE_RESTORE = "pre_restore"
KIND_PRE_RESET = "pre_reset"
KIND_UPLOADED = "uploaded"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,150}\.db$")
_STAMP = re.compile(r"(\d{8})_(\d{6})")

# Retention for rotated kinds (auto + startup): newest per day/week/month.
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 14, 8, 12
KEEP_PRE_MIGRATION = 5


class BackupError(Exception):
    """A backup or restore could not be completed safely. Message is user-facing Persian."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    path: Path
    size_bytes: int
    created_at: datetime


def _timestamp(now: datetime | None = None) -> str:
    return (now or iran_now()).strftime("%Y%m%d_%H%M%S")


def safe_backup_path(backup_dir: str | os.PathLike, name: str) -> Path:
    """Resolve `name` inside `backup_dir`, refusing anything that could escape it."""
    if not name or not _SAFE_NAME.match(name) or ".." in name:
        raise BackupError("نام فایل پشتیبان نامعتبر است")
    base = Path(backup_dir).resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise BackupError("نام فایل پشتیبان نامعتبر است")
    return path


def verify_database_file(path: str | os.PathLike) -> None:
    """Raise BackupError unless `path` is a healthy accounting database."""
    p = Path(path)
    try:
        with open(p, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                raise BackupError("فایل، دیتابیس SQLite نیست")
    except OSError as e:
        raise BackupError("فایل پشتیبان خوانده نشد") from e
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("فایل پشتیبان سالم نیست (integrity_check)")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "invoices", "patients"} <= tables:
            raise BackupError("این فایل دیتابیس حسابداری نیست")
    except sqlite3.DatabaseError as e:
        raise BackupError("فایل پشتیبان خراب است") from e
    finally:
        conn.close()


def create_backup(
    db_path: str | os.PathLike, backup_dir: str | os.PathLike, kind: str, now: datetime | None = None
) -> Path:
    """Consistent copy of the live database into `backup_dir`, verified before returning."""
    src_path = Path(db_path)
    if not src_path.exists():
        raise BackupError("فایل دیتابیس پیدا نشد")
    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp(now)
    target = out_dir / f"backup_{kind}_{stamp}.db"
    n = 1
    while target.exists():
        target = out_dir / f"backup_{kind}_{stamp}_{n}.db"
        n += 1
    tmp = target.with_suffix(".db.partial")
    src = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    try:
        verify_database_file(tmp)
    except BackupError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, target)
    log.info("backup created: %s", target.name)
    return target


def list_backups(backup_dir: str | os.PathLike) -> list[BackupFile]:
    d = Path(backup_dir)
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.db"):
        if not _SAFE_NAME.match(p.name):
            continue
        st = p.stat()
        out.append(BackupFile(p.name, p, st.st_size, _created_at(p, st.st_mtime)))
    return sorted(out, key=lambda b: b.created_at, reverse=True)


def _created_at(p: Path, mtime: float) -> datetime:
    m = _STAMP.search(p.name)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(mtime)


def _kind(name: str) -> str | None:
    for kind in (KIND_STARTUP, KIND_PRE_MIGRATION, KIND_AUTO):
        if name.startswith(f"backup_{kind}_"):
            return kind
    return None


def latest_backup_age(backup_dir: str | os.PathLike, now: datetime | None = None) -> timedelta | None:
    files = list_backups(backup_dir)
    if not files:
        return None
    return (now or iran_now()) - files[0].created_at


def prune(backup_dir: str | os.PathLike) -> list[str]:
    """Rotate automatic backups; never touches manual, uploaded or pre-restore/reset copies."""
    files = list_backups(backup_dir)
    rotated = [f for f in files if _kind(f.name) in (KIND_AUTO, KIND_STARTUP)]
    keep: set[str] = set()
    for key_fn, limit in (
        (lambda t: t.date(), KEEP_DAILY),
        (lambda t: t.isocalendar()[:2], KEEP_WEEKLY),
        (lambda t: (t.year, t.month), KEEP_MONTHLY),
    ):
        seen = []
        for f in rotated:  # newest first
            k = key_fn(f.created_at)
            if k not in seen:
                seen.append(k)
                if len(seen) > limit:
                    break
                keep.add(f.name)
    pre = [f for f in files if _kind(f.name) == KIND_PRE_MIGRATION]
    keep.update(f.name for f in pre[:KEEP_PRE_MIGRATION])
    removed = []
    for f in rotated + pre:
        if f.name not in keep:
            f.path.unlink(missing_ok=True)
            removed.append(f.name)
    if removed:
        log.info("pruned %d old automatic backups", len(removed))
    return removed


def restore_backup(db_path: str | os.PathLike, backup_dir: str | os.PathLike, name: str) -> Path:
    """Replace the live database content with a verified backup.

    A pre-restore backup of the current database is taken first. The copy uses the
    backup API *into* the live file, so connections held by other requests see either
    the old or the new database, never a half-written file.
    Returns the path of the pre-restore backup.
    """
    source = safe_backup_path(backup_dir, name)
    if not source.exists():
        raise BackupError("فایل پشتیبان پیدا نشد")
    verify_database_file(source)
    safety = create_backup(db_path, backup_dir, KIND_PRE_RESTORE)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(db_path, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    verify_database_file(db_path)
    log.warning("database restored from %s (previous state saved as %s)", name, safety.name)
    return safety


def delete_backup(backup_dir: str | os.PathLike, name: str) -> None:
    path = safe_backup_path(backup_dir, name)
    if not path.exists():
        raise BackupError("فایل پشتیبان پیدا نشد")
    path.unlink()


def save_uploaded_backup(backup_dir: str | os.PathLike, stream) -> Path:
    """Store an uploaded file only if it is a healthy accounting database."""
    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"backup_{KIND_UPLOADED}_{_timestamp()}.db"
    tmp = target.with_suffix(".db.partial")
    stream.save(tmp)
    try:
        verify_database_file(tmp)
    except BackupError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, target)
    return target
