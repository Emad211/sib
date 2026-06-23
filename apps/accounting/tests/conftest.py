"""Pytest harness for the webapp (accounting) app — the characterization-test safety net.

DATA SAFETY (the #1 project rule): webapp's `core.get_db()` reads
`Config.DATABASE_PATH` (the class attribute, hard-coded to the real `clinic_new.db` —
it is NOT env / app.config overridable). So EVERY test must run against a fresh temp DB,
and we must force-point `Config.DATABASE_PATH` at that temp file BEFORE any `get_db()`.
This conftest does exactly that, hard-guards that the real DB is never the active path,
and (session-scoped) re-asserts the real `clinic_new.db` is byte-for-byte unchanged across
the whole run. The accounting money logic WRITES (update_invoice_totals/close_invoice), so
this isolation is non-negotiable.
"""
import os
import sys
import hashlib
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEBAPP = os.path.dirname(_HERE)
sys.path.insert(0, _WEBAPP)  # make `src` importable when run from anywhere

from src.config.settings import Config          # noqa: E402
import src.adapters.sqlite.core as core         # noqa: E402

_REAL_DB = os.path.join(_WEBAPP, "clinic_new.db")


def _sha(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _guard_real_db():
    """Prove the real production accounting DB is byte-unchanged across the whole run."""
    before = _sha(_REAL_DB)
    yield
    after = _sha(_REAL_DB)
    assert before == after, (
        "SAFETY VIOLATION: webapp/clinic_new.db changed during tests! "
        "A test wrote to the real production DB.")


@pytest.fixture()
def webapp_db(tmp_path):
    """A fresh, isolated, schema-initialised webapp DB bound via Config.DATABASE_PATH.

    Yields the live sqlite3 connection (inside a pushed app context). All accounting repos
    call core.get_db() which now resolves to this temp file — never the real clinic_new.db.
    """
    temp_db = str(tmp_path / "test_clinic.db")
    assert os.path.abspath(temp_db) != os.path.abspath(_REAL_DB)  # hard guard

    old_path = Config.DATABASE_PATH
    old_flag = core._migrations_done
    # Point the hard-coded resolver at the temp DB BEFORE create_app / any get_db.
    Config.DATABASE_PATH = temp_db
    core._migrations_done = False
    assert os.path.abspath(Config.DATABASE_PATH) != os.path.abspath(_REAL_DB)

    from src.app import create_app
    app = create_app({"TESTING": True, "DATABASE_PATH": temp_db})

    ctx = app.app_context()
    ctx.push()
    try:
        db = core.get_db()   # users table missing -> loads schema.sql + runs migrations
        yield db
    finally:
        try:
            core.close_connection(None)
        except Exception:
            pass
        ctx.pop()
        Config.DATABASE_PATH = old_path
        core._migrations_done = old_flag
