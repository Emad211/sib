"""Database backups and restores (ADR-008, ACC-1.2–1.4).

Every copy goes through SQLite's online backup API, so it is consistent even while
the application is writing, and every copy is verified with `PRAGMA integrity_check`.
File names inside the backup folder are validated before any read/write/delete, so a
name can never point outside that folder (SEC-02).

Review hardening (2026-10-26):
* a locked database never hangs a copy — `sqlite3.backup()` alone retries forever;
* interrupted copies never leave `.partial` files behind;
* SQLite URIs are built with `Path.as_uri()` so folders containing `#`, `?` or `%` work;
* uploads get unique names and a free-space check;
* a file locked by Windows (antivirus, download in progress) does not abort rotation.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import time
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
PARTIAL_SUFFIX = ".partial"

# Retention for rotated kinds (auto + startup): newest per day/week/month.
# 7 + 4 + 6 = at most 17 rotated copies (~1.8 GB at today's 108 MB database).
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 7, 4, 6
KEEP_PRE_MIGRATION = 5
# Never start a backup that would leave less than this much free disk space.
MIN_FREE_AFTER_BACKUP = 500 * 1024 * 1024
DEFAULT_LOCK_TIMEOUT = 30.0

BUSY_MESSAGE = "دیتابیس در این لحظه مشغول است. چند ثانیه بعد دوباره امتحان کنید؛ هیچ داده‌ای تغییر نکرد."
DISK_MESSAGE = "فضای خالی دیسک برای پشتیبان کافی نیست. لطفاً فضای دیسک را آزاد کنید."


class BackupError(Exception):
    """A backup or restore could not be completed safely. Message is user-facing Persian."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    path: Path
    size_bytes: int
    created_at: datetime


def ro_uri(path: str | os.PathLike) -> str:
    """Read-only SQLite URI that survives '#', '?', '%' and spaces in the path."""
    return Path(path).resolve().as_uri() + "?mode=ro"


def _timestamp(now: datetime | None = None) -> str:
    return (now or iran_now()).strftime("%Y%m%d_%H%M%S")


def _unique_target(out_dir: Path, kind: str, now: datetime | None) -> Path:
    stamp = _timestamp(now)
    target = out_dir / f"backup_{kind}_{stamp}.db"
    n = 1
    while target.exists() or target.with_name(target.name + PARTIAL_SUFFIX).exists():
        target = out_dir / f"backup_{kind}_{stamp}_{n}.db"
        n += 1
    return target


def _ensure_space(out_dir: Path, needed_bytes: int) -> None:
    if shutil.disk_usage(out_dir).free < needed_bytes + MIN_FREE_AFTER_BACKUP:
        log.error("backup refused: not enough free disk space in %s", out_dir)
        raise BackupError(DISK_MESSAGE)


def copy_database(src_uri: str, dst_path: str | os.PathLike, timeout: float) -> None:
    """Online backup src -> dst with a hard deadline.

    `sqlite3.Connection.backup()` sleeps and retries forever while a database is locked;
    the progress callback (called after every step, including busy retries) enforces
    the deadline, and the error becomes a user-facing BackupError.
    """
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() > deadline:
            raise TimeoutError("database locked")

    src = sqlite3.connect(src_uri, uri=True, timeout=timeout)
    dst = sqlite3.connect(dst_path, timeout=timeout)
    try:
        src.backup(dst, pages=1024, progress=progress, sleep=0.05)
    except (TimeoutError, sqlite3.OperationalError) as e:
        if isinstance(e, TimeoutError) or "locked" in str(e) or "busy" in str(e):
            raise BackupError(BUSY_MESSAGE) from e
        raise
    finally:
        dst.close()
        src.close()


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
    try:
        conn = sqlite3.connect(ro_uri(p), uri=True)
    except sqlite3.Error as e:
        raise BackupError("فایل پشتیبان باز نشد") from e
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
    db_path: str | os.PathLike,
    backup_dir: str | os.PathLike,
    kind: str,
    now: datetime | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Path:
    """Consistent copy of the live database into `backup_dir`, verified before returning."""
    src_path = Path(db_path)
    if not src_path.exists():
        raise BackupError("فایل دیتابیس پیدا نشد")
    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _ensure_space(out_dir, src_path.stat().st_size)
    target = _unique_target(out_dir, kind, now)
    tmp = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        copy_database(ro_uri(src_path), tmp, lock_timeout)
        verify_database_file(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
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
        try:
            st = p.stat()
        except OSError:
            continue
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
    """Age of the newest backup that is not dated in the future (a clock change must not
    suppress backups)."""
    now = now or iran_now()
    past = [b for b in list_backups(backup_dir) if b.created_at <= now + timedelta(minutes=5)]
    if not past:
        return None
    return max(timedelta(0), now - past[0].created_at)


def _unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        log.warning("could not delete %s (in use?); will retry later", path.name)
        return False


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
    removed = [f.name for f in rotated + pre if f.name not in keep and _unlink(f.path)]
    if removed:
        log.info("pruned %d old automatic backups", len(removed))
    return removed


def cleanup_leftovers(backup_dir: str | os.PathLike) -> list[str]:
    """Remove temp files left by a crash or power cut (`*.partial`, `.reset_*.tmp`)."""
    d = Path(backup_dir)
    if not d.exists():
        return []
    leftovers = list(d.glob("*" + PARTIAL_SUFFIX)) + list(d.glob(".reset_*.tmp"))
    return [p.name for p in leftovers if _unlink(p)]


def restore_backup(
    db_path: str | os.PathLike,
    backup_dir: str | os.PathLike,
    name: str,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Path:
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
    safety = create_backup(db_path, backup_dir, KIND_PRE_RESTORE, lock_timeout=lock_timeout)
    copy_database(ro_uri(source), db_path, lock_timeout)
    verify_database_file(db_path)
    log.warning("database restored from %s (previous state saved as %s)", name, safety.name)
    return safety


def delete_backup(backup_dir: str | os.PathLike, name: str) -> None:
    path = safe_backup_path(backup_dir, name)
    if not path.exists():
        raise BackupError("فایل پشتیبان پیدا نشد")
    if not _unlink(path):
        raise BackupError("فایل در حال استفاده است و حذف نشد. چند لحظه بعد دوباره امتحان کنید.")


def save_uploaded_backup(backup_dir: str | os.PathLike, stream, size_hint: int | None = None) -> Path:
    """Store an uploaded file only if it is a healthy accounting database."""
    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _ensure_space(out_dir, size_hint or 0)
    target = _unique_target(out_dir, KIND_UPLOADED, None)
    tmp = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        stream.save(tmp)
        verify_database_file(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
