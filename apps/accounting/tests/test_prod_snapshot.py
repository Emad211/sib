"""Production-snapshot gate (ACC-0.6, ACC-0.7). LOCAL ONLY — never runs in CI.

Uses a copy of the real database from sib/_data/prod_snapshot/ (or $SIB_PROD_SNAPSHOT).
The snapshot itself is opened read-only; every test works on its own temporary copy.

    pytest -m prod_snapshot apps/accounting/tests/test_prod_snapshot.py
"""

import glob
import os
import sqlite3

import pytest
from conftest import Config, core

pytestmark = pytest.mark.prod_snapshot

_SIB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _snapshot_path():
    env = os.environ.get("SIB_PROD_SNAPSHOT")
    if env:
        return env
    found = sorted(glob.glob(os.path.join(_SIB_ROOT, "_data", "prod_snapshot", "*.db")))
    return found[-1] if found else None


@pytest.fixture(scope="module")
def snapshot():
    path = _snapshot_path()
    if not path or not os.path.exists(path):
        pytest.skip("no production snapshot available (local-only test)")
    return path


@pytest.fixture(scope="module")
def migrated_copy(snapshot, tmp_path_factory):
    """ONE copy per run (disk: ~110 MB), migrated by the app. Tests must not write to it."""
    tmp_path = tmp_path_factory.mktemp("prod_copy")
    copy = str(tmp_path / "prod_copy.db")
    src = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    dst = sqlite3.connect(copy)
    src.backup(dst)
    dst.close()
    src.close()
    old_path, old_flag = Config.DATABASE_PATH, core._migrations_done
    Config.DATABASE_PATH = copy
    core._migrations_done = False
    from src.app import create_app

    cfg = {k: getattr(Config, k) for k in dir(Config) if k.isupper()}
    cfg.update(
        {
            "TESTING": True,
            "DATABASE_PATH": copy,
            "SECRET_KEY": "prod-copy-test",
            "BACKUP_FOLDER": str(tmp_path / "backups"),
            "PROPAGATE_EXCEPTIONS": False,
        }
    )
    app = create_app(cfg)
    with app.app_context():
        core.get_db()  # opening the DB is what runs the migrations
        core.close_connection(None)
    try:
        yield app, copy
    finally:
        Config.DATABASE_PATH, core._migrations_done = old_path, old_flag
        for f in tmp_path.glob("**/*.db"):
            f.unlink(missing_ok=True)


def test_snapshot_file_is_never_modified(snapshot, migrated_copy):
    import hashlib

    with open(snapshot, "rb") as f:
        before = hashlib.sha256(f.read()).hexdigest()
    _app, copy = migrated_copy
    assert os.path.abspath(copy) != os.path.abspath(snapshot)
    with open(snapshot, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == before


def test_migrations_on_production_are_additive_only(snapshot, migrated_copy):
    from tools.schema_diff import describe, diff

    _app, copy = migrated_copy
    bad, added = diff(describe(snapshot), describe(copy))
    assert not bad, "non-additive schema change on production copy:\n" + "\n".join(bad)


def test_migrations_keep_every_row(snapshot, migrated_copy):
    _app, copy = migrated_copy
    old = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    new = sqlite3.connect(copy)
    tables = [
        r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    ]
    for t in tables:
        assert (
            old.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()
            == new.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()
        ), t
    assert new.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    old.close()
    new.close()


def test_production_schema_matches_committed_baseline(snapshot):
    """If this fails, the snapshot is newer than the committed schema baseline: re-dump it."""
    from tools.schema_diff import dump

    name = os.path.splitext(os.path.basename(snapshot))[0]
    baseline = os.path.join(os.path.dirname(__file__), "golden", f"{name}.schema.sql")
    if not os.path.exists(baseline):
        pytest.skip(f"no committed schema baseline for {name}")
    with open(baseline, encoding="utf-8") as f:
        assert dump(snapshot) == f.read()


def test_every_page_works_on_production_data(migrated_copy):
    """All GET pages for manager and reception: no server error except the known bugs."""
    import re

    from test_route_matrix import KNOWN_SERVER_ERRORS, PATH_VALUES, _observe

    app, copy = migrated_copy
    conn = sqlite3.connect(copy)
    users = {
        role: conn.execute(
            "SELECT username FROM users WHERE role=? AND is_active=1 ORDER BY id LIMIT 1", (role,)
        ).fetchone()[0]
        for role in ("manager", "reception")
    }
    conn.close()
    errors = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or "GET" not in rule.methods:
            continue
        url = re.sub(r"<(?:\w+:)?(\w+)>", lambda m: PATH_VALUES[m.group(1)], rule.rule)
        for role, username in users.items():
            cell = _observe(app, "GET", url, username)
            if cell["status"] >= 500:
                errors.append(f"GET {rule.rule} [{rule.endpoint}] [{role}]")
    unexpected = sorted(set(errors) - set(KNOWN_SERVER_ERRORS))
    assert not unexpected, "server errors on production data:\n" + "\n".join(unexpected)


def test_golden_master_unchanged(snapshot):
    """Every number of every report/export/chart/payroll is identical to the recorded baseline."""
    from tools.golden_master import _baseline_path, compare

    if not os.path.exists(_baseline_path(snapshot)):
        pytest.skip("no golden baseline recorded for this snapshot (tools/golden_master.py record)")
    hard, _soft = compare(snapshot)
    assert not hard, "golden master differences:\n" + "\n".join(hard)
