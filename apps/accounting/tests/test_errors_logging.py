"""Error pages, file logging and startup safety (ACC-1.8)."""

import logging
import sys
from pathlib import Path

import pytest
from conftest import login_as

APP_ROOT = Path(__file__).resolve().parent.parent


def test_unhandled_error_shows_persian_page_with_reference_and_is_logged(synthetic_app, caplog):
    synthetic_app.config["PROPAGATE_EXCEPTIONS"] = False

    @synthetic_app.route("/__boom")
    def boom():
        raise ValueError("secret detail that must not reach the browser")

    with caplog.at_level(logging.ERROR, logger="hesabdari.errors"):
        r = synthetic_app.test_client().get("/__boom")
    body = r.get_data(as_text=True)
    assert r.status_code == 500
    assert "خطای پیش‌بینی‌نشده" in body and 'dir="rtl"' in body
    assert "secret detail" not in body  # no internals leaked to the user
    ref = body.split('class="ref">')[1].split("<")[0]
    assert any(ref in rec.getMessage() for rec in caplog.records)
    assert any(rec.exc_info for rec in caplog.records)


def test_unknown_page_shows_persian_404(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    r = c.get("/this/does/not/exist")
    assert r.status_code == 404 and "صفحه پیدا نشد" in r.get_data(as_text=True)


def test_client_errors_keep_their_status(synthetic_app):
    """400/403 JSON answers from routes must not be turned into the 500 page."""
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    assert c.post("/manager/settings/reset-database", data={}).status_code == 400


def test_configure_logging_writes_rotating_file(tmp_path):
    from src.config import logging_setup

    logging_setup._configured_dir = None
    path = logging_setup.configure_logging(str(tmp_path))
    logging.getLogger("hesabdari.test").warning("hello-log")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "hello-log" in Path(path).read_text(encoding="utf-8")
    # idempotent: a second call does not duplicate handlers
    n = len(logging.getLogger().handlers)
    logging_setup.configure_logging(str(tmp_path))
    assert len(logging.getLogger().handlers) == n
    for h in list(logging.getLogger().handlers):
        if getattr(h, "_hesabdari", False):
            logging.getLogger().removeHandler(h)
            h.close()
    logging_setup._configured_dir = None


@pytest.fixture()
def start_module(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(APP_ROOT))
    import start
    from src.config import logging_setup
    from src.config.settings import Config

    monkeypatch.setattr(Config, "DATABASE_PATH", str(tmp_path / "db" / "clinic_new.db"))
    yield start
    for h in list(logging.getLogger().handlers):
        if getattr(h, "_hesabdari", False):
            logging.getLogger().removeHandler(h)
            h.close()
    logging_setup._configured_dir = None
    sys.modules.pop("start", None)


def test_startup_refuses_to_run_when_database_cannot_be_prepared(start_module, monkeypatch):
    from src.adapters.sqlite import core
    from src.adapters.sqlite.migrations import MigrationError

    shown = []
    monkeypatch.setattr(start_module, "show_fatal", shown.append)
    monkeypatch.setattr(core, "ensure_database_ready", lambda *a: (_ for _ in ()).throw(MigrationError("x")))
    assert start_module.main() == 1
    assert shown and "دیتابیس" in shown[0]


def test_startup_prepares_a_fresh_database(start_module, monkeypatch):
    from src.adapters.sqlite import core

    monkeypatch.setattr(core, "_migrations_done", False)
    assert start_module.prepare_database() is True
    from src.config.settings import Config

    assert Path(Config.DATABASE_PATH).exists()
    assert (Path(Config.DATABASE_PATH).parent / "logs" / "hesabdari.log").exists()


def test_version_is_visible_on_login_and_settings(synthetic_app):
    from src.__version__ import __version__
    from src.adapters.sqlite.migrations import LATEST

    fa = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    c = synthetic_app.test_client()
    assert f"نسخه {__version__.translate(fa)}" in c.get("/auth/login").get_data(as_text=True)
    login_as(c, synthetic_app, "manager1")
    page = c.get("/manager/settings").get_data(as_text=True)
    assert f"نسخهٔ دیتابیس {str(LATEST).translate(fa)}" in page
