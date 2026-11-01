"""Application configuration and the per-install secret file."""

import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PORT = 8443
# ADR-008 caps retention at 17 snapshots (7 daily, 4 weekly, 6 monthly); `BackupService` keeps a
# flat count until the tiered rotation of O01 §2 is implemented.
DEFAULT_BACKUP_KEEP = 17


def _as_count(value: str, *, minimum: int = 1) -> int:
    count = int(value)
    if count < minimum:
        raise ValueError(f"count must be at least {minimum}: {count}")
    return count


def _as_bool(value: str) -> bool:
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def _as_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port


def _as_origin(value: str) -> str | None:
    """An `https://host[:port]` origin with nothing after it, or None when unset."""
    text = value.strip().rstrip("/")
    if not text:
        return None
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d{1,5})?", text):
        raise ValueError(f"SHIFT_PUBLIC_ORIGIN must be https://host[:port], got {value!r}")
    return text


@dataclass(frozen=True, slots=True)
class Settings:
    """Paths and runtime switches; `from_env` is the only production constructor."""

    database_path: Path
    log_dir: Path
    audit_log_path: Path
    secret_file: Path
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    hash_chain: bool = True
    environment: str = "production"
    # The accounting copy the read bridge polls; not configured means the bridge never runs.
    accounting_db_path: Path | None = None
    backup_dir: Path | None = None
    backup_mirror_dir: Path | None = None
    backup_keep: int = DEFAULT_BACKUP_KEEP
    # The receipt photos (SHF-7): files on the clinic PC, next to the database unless SHIFT_PHOTOS_DIR
    # says otherwise. They are content-addressed, so the backup copies them once and keeps them.
    photos_dir: Path | None = None
    # Off by default so a test or a one-off script never starts a timer by accident; the
    # production entry point turns them on through `SHIFT_BACKGROUND_JOBS` (see `from_env`).
    background_jobs: bool = False
    # The address the head and the manager reach from outside the clinic (the relay's public name, with
    # its Let's Encrypt certificate). A pairing QR made "for outside" carries it; LAN phones keep the
    # LAN address so the clinic never depends on the internet (O05).
    public_origin: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Read `SHIFT_*` variables, defaulting to files next to the app checkout."""
        env = os.environ if environ is None else environ
        root = Path(env.get("SHIFT_HOME", APP_ROOT)).resolve()
        log_dir = Path(env.get("SHIFT_LOG_DIR", root / "logs")).resolve()
        accounting = env.get("SHIFT_ACCOUNTING_DB", "").strip()
        mirror = env.get("SHIFT_BACKUP_MIRROR", "").strip()
        return cls(
            database_path=Path(env.get("SHIFT_DB_PATH", root / "shift.db")).resolve(),
            log_dir=log_dir,
            audit_log_path=Path(env.get("SHIFT_AUDIT_LOG", log_dir / "audit.log")).resolve(),
            secret_file=Path(env.get("SHIFT_SECRET_FILE", root / ".shift_secret_key")).resolve(),
            host=env.get("SHIFT_HOST", "127.0.0.1"),
            port=_as_port(env.get("SHIFT_PORT", str(DEFAULT_PORT))),
            hash_chain=_as_bool(env.get("SHIFT_HASH_CHAIN", "1")),
            environment=env.get("SHIFT_ENV", "production"),
            accounting_db_path=Path(accounting).resolve() if accounting else None,
            backup_dir=Path(env.get("SHIFT_BACKUP_DIR", root / "backups")).resolve(),
            backup_mirror_dir=Path(mirror).resolve() if mirror else None,
            backup_keep=_as_count(env.get("SHIFT_BACKUP_KEEP", str(DEFAULT_BACKUP_KEEP))),
            photos_dir=Path(env.get("SHIFT_PHOTOS_DIR", root / "photos")).resolve(),
            background_jobs=_as_bool(env.get("SHIFT_BACKGROUND_JOBS", "1")),
            public_origin=_as_origin(env.get("SHIFT_PUBLIC_ORIGIN", "")),
        )

    @property
    def resolved_backup_dir(self) -> Path:
        """Where snapshots land: next to the database unless `SHIFT_BACKUP_DIR` says otherwise."""
        return self.backup_dir if self.backup_dir is not None else self.database_path.parent / "backups"

    @property
    def resolved_photos_dir(self) -> Path:
        """Where receipt photos live: next to the database unless `SHIFT_PHOTOS_DIR` says otherwise."""
        return self.photos_dir if self.photos_dir is not None else self.database_path.parent / "photos"

    def read_or_create_secret(self) -> str:
        """Return the signing secret, creating it atomically on first use."""
        if self.secret_file.exists():
            value = self.secret_file.read_text(encoding="ascii").strip()
            if not value:
                raise ValueError(f"secret file is empty: {self.secret_file}")
            return value
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(32)
        try:
            descriptor = os.open(self.secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.read_or_create_secret()
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(value)
        return value
