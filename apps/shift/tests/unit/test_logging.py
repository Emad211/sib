"""The audit sink is separate from the public log and survives reconfiguration."""

import logging

from shift.config.logging import AUDIT_LOGGER, PUBLIC_LOGGER, configure_logging, get_audit_logger
from shift.config.settings import Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "shift.db",
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        secret_file=tmp_path / "secret.txt",
    )


def _flush() -> None:
    for name in (PUBLIC_LOGGER, AUDIT_LOGGER):
        for handler in logging.getLogger(name).handlers:
            handler.flush()


def test_audit_records_never_reach_the_public_log(tmp_path) -> None:
    settings = _settings(tmp_path)
    configure_logging(settings)

    logging.getLogger("shift.probe").info("public-marker")
    get_audit_logger().info("audit-marker")
    _flush()

    public = (settings.log_dir / "shift.log").read_text(encoding="utf-8")
    audit = settings.audit_log_path.read_text(encoding="utf-8")
    assert "public-marker" in public
    assert "audit-marker" not in public
    assert "audit-marker" in audit
    assert "public-marker" not in audit


def test_reconfiguring_does_not_duplicate_handlers(tmp_path) -> None:
    settings = _settings(tmp_path)

    configure_logging(settings)
    configure_logging(settings)
    logging.getLogger("shift.probe").info("once")
    _flush()

    public = (settings.log_dir / "shift.log").read_text(encoding="utf-8")
    assert public.count("once") == 1
    assert len(logging.getLogger(PUBLIC_LOGGER).handlers) == 1
    assert len(logging.getLogger(AUDIT_LOGGER).handlers) == 1
