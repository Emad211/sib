"""Public and audit log sinks; audit records never enter the public log."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from shift.config.settings import Settings

PUBLIC_LOGGER = "shift"
AUDIT_LOGGER = "shift.audit"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(settings: Settings) -> None:
    """Point the public and audit loggers at separate files (idempotent)."""
    public_path = settings.log_dir / "shift.log"
    if settings.audit_log_path == public_path:
        raise ValueError("audit log must be a separate file from the public log")
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    _configure_logger(logging.getLogger(PUBLIC_LOGGER), public_path)
    _configure_logger(logging.getLogger(AUDIT_LOGGER), settings.audit_log_path)


def get_audit_logger() -> logging.Logger:
    """Return the dedicated audit logger; callers must not add a public handler."""
    return logging.getLogger(AUDIT_LOGGER)


def _configure_logger(logger: logging.Logger, path: Path) -> None:
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
