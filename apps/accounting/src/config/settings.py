import os
import sys


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-prod'

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

    DEBUG = True
    TESTING = False

    # Folder where automatic backups are stored (used by scheduler)
    BACKUP_FOLDER = os.path.join(PROJECT_ROOT, 'backups')


class TestConfig(Config):
    TESTING = True
    DATABASE_PATH = ':memory:'
