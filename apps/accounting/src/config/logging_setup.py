"""File logging for the desktop program (ACC-1.8, DEBT-03).

The exe has no console (console=False), so before this every error was invisible.
Logs go to `<exe folder>/logs/hesabdari.log`, rotated at 2 MB, 10 files kept.
Patient data must never be logged: log ids, not names (docs/engineering/E02).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_FILE_NAME = "hesabdari.log"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured_dir: str | None = None


def configure_logging(log_dir: str, level: int = logging.INFO) -> str:
    """Idempotent: attach one rotating file handler to the root logger."""
    global _configured_dir
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, LOG_FILE_NAME)
    if _configured_dir == log_dir:
        return path
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_hesabdari", False):
            root.removeHandler(h)
            h.close()
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=10, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler._hesabdari = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no per-request access log noise
    _configured_dir = log_dir
    return path
