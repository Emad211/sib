import os
import sys

# The weak fallback secret for local/.exe/LAN runs. In a real server deployment
# (PRODUCTION=1 or FLASK_ENV/APP_ENV=production) create_app() refuses to start with it.
DEFAULT_SECRET_KEY = "dev-secret-key-change-in-prod"


SECRET_FILE_NAME = ".hesabdari_secret_key"


def load_or_create_install_secret(folder) -> str:
    """Per-install session key stored next to the database (SEC-01, ACC-1.5).

    Created once with 256 bits of randomness; reused on every start so sessions survive
    restarts. A present-but-invalid file is an error (never silently fall back).
    """
    import secrets
    from pathlib import Path

    path = Path(folder) / SECRET_FILE_NAME
    if path.exists():
        key = path.read_text(encoding="ascii", errors="replace").strip()
        if len(key) < 64 or any(c not in "0123456789abcdef" for c in key):
            raise RuntimeError(f"invalid session key file: {path}. Delete it to generate a new one.")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(key + "\n", encoding="ascii")  # complete before it becomes visible
    try:
        os.link(tmp, path)  # atomic, and never overwrites a key created concurrently
    except FileExistsError:
        return load_or_create_install_secret(folder)
    except OSError:  # filesystem without hard links (e.g. FAT32 stick): still atomic, not exclusive
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return key


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _is_production() -> bool:
    if _env_flag("PRODUCTION"):
        return True
    env = (os.environ.get("FLASK_ENV") or os.environ.get("APP_ENV") or "").strip().lower()
    return env == "production"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or DEFAULT_SECRET_KEY

    # True only in a real (cloud/server) deployment — honours PRODUCTION=1 and the existing
    # FLASK_ENV/APP_ENV=production convention. Gates the security hardening so local .exe /
    # LAN / dev / test runs are unchanged (0.0.0.0 bind, default secret, http cookies).
    PRODUCTION = _is_production()

    # Determine project root and paths in both source and frozen (PyInstaller) modes.
    if getattr(sys, "frozen", False):
        # When bundled by PyInstaller the executable lives in sys.executable
        # Use the executable directory as the project root so DB and backups
        # are created next to the exe (and are writable on typical installs).
        PROJECT_ROOT = os.path.dirname(sys.executable)
        BASE_DIR = PROJECT_ROOT
    else:
        # Regular source layout: src/config -> src -> webapp
        BASE_DIR = os.path.abspath(os.path.dirname(__file__))
        PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))

    # Database file location
    DATABASE_PATH = os.path.join(PROJECT_ROOT, "clinic_new.db")

    DEBUG = _env_flag("DEBUG")
    TESTING = False

    # Folder where automatic backups are stored (used by scheduler)
    BACKUP_FOLDER = os.path.join(PROJECT_ROOT, "backups")


class TestConfig(Config):
    TESTING = True
    DATABASE_PATH = ":memory:"
