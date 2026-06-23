import os
import sys


# The weak fallback secret for local/.exe/LAN runs. In a real server deployment
# (PRODUCTION=1 or FLASK_ENV/APP_ENV=production) create_app() refuses to start with it.
DEFAULT_SECRET_KEY = 'dev-secret-key-change-in-prod'


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def _is_production() -> bool:
    if _env_flag('PRODUCTION'):
        return True
    env = (os.environ.get('FLASK_ENV') or os.environ.get('APP_ENV') or '').strip().lower()
    return env == 'production'


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or DEFAULT_SECRET_KEY

    # True only in a real (cloud/server) deployment — honours PRODUCTION=1 and the existing
    # FLASK_ENV/APP_ENV=production convention. Gates the security hardening so local .exe /
    # LAN / dev / test runs are unchanged (0.0.0.0 bind, default secret, http cookies).
    PRODUCTION = _is_production()

    # Determine project root and paths in both source and frozen (PyInstaller) modes.
    if getattr(sys, 'frozen', False):
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
    DATABASE_PATH = os.path.join(PROJECT_ROOT, 'clinic_new.db')

    DEBUG = _env_flag('DEBUG')
    TESTING = False

    # Folder where automatic backups are stored (used by scheduler)
    BACKUP_FOLDER = os.path.join(PROJECT_ROOT, 'backups')


class TestConfig(Config):
    TESTING = True
    DATABASE_PATH = ':memory:'
