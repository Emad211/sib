"""Application configuration and the per-install secret file."""

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PORT = 8443


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
    environment: str = "development"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Read `SHIFT_*` variables, defaulting to files next to the app checkout."""
        env = os.environ if environ is None else environ
        root = Path(env.get("SHIFT_HOME", APP_ROOT)).resolve()
        log_dir = Path(env.get("SHIFT_LOG_DIR", root / "logs")).resolve()
        return cls(
            database_path=Path(env.get("SHIFT_DB_PATH", root / "shift.db")).resolve(),
            log_dir=log_dir,
            audit_log_path=Path(env.get("SHIFT_AUDIT_LOG", log_dir / "audit.log")).resolve(),
            secret_file=Path(env.get("SHIFT_SECRET_FILE", root / ".shift_secret_key")).resolve(),
            host=env.get("SHIFT_HOST", "127.0.0.1"),
            port=_as_port(env.get("SHIFT_PORT", str(DEFAULT_PORT))),
            hash_chain=_as_bool(env.get("SHIFT_HASH_CHAIN", "1")),
            environment=env.get("SHIFT_ENV", "development"),
        )

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
