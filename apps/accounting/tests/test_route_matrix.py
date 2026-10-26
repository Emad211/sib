"""Route × role matrix (ACC-0.5): every URL rule, every method, every role.

The expected outcome of each cell lives in `tests/route_matrix.json`. It was recorded
from the *current* behaviour (characterization), including behaviour we consider wrong
(see SEC-06 in docs/accounting/A01_CURRENT_STATE_AUDIT.md). Changing a cell is a
deliberate access-control decision and must be reviewed.

* A new route that is not in the JSON fails `test_every_route_is_in_the_matrix`.
* Re-record after a deliberate change:  UPDATE_ROUTE_MATRIX=1 pytest tests/test_route_matrix.py
"""
import json
import os
import re
from urllib.parse import urlparse

import pytest

from conftest import login_as

MATRIX_PATH = os.path.join(os.path.dirname(__file__), "route_matrix.json")
ROLES = {"anon": None, "reception": "reception1", "doctor": "doctor1", "manager": "manager1"}
SKIP_ENDPOINTS = {"static"}

# Values for URL parameters; they exist in the synthetic DB (tools/make_synthetic_db.py).
PATH_VALUES = {
    "invoice_id": "1",
    "patient_id": "1",
    "insurance_type": "تامین اجتماعی",
    "backup_name": "does-not-exist.db",
}


def _url_for_rule(rule):
    return re.sub(r"<(?:\w+:)?(\w+)>", lambda m: PATH_VALUES[m.group(1)], rule.rule)


def _cells(app):
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        if rule.endpoint in SKIP_ENDPOINTS:
            continue
        for method in sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS")):
            yield f"{method} {rule.rule} [{rule.endpoint}]", method, _url_for_rule(rule)


def _observe(app, method, url, username):
    """Status (+ redirect path, + exception origin for 5xx) as production would see it."""
    import traceback
    from flask import got_request_exception

    app.config["PROPAGATE_EXCEPTIONS"] = False
    caught = []

    def _on_exc(sender, exception, **extra):
        tb = traceback.extract_tb(exception.__traceback__)
        frame = next((f for f in reversed(tb) if os.sep + "src" + os.sep in f.filename), tb[-1])
        caught.append(f"{type(exception).__name__} @ {os.path.basename(frame.filename)}:{frame.lineno}")

    client = app.test_client()
    if username:
        login_as(client, app, username)
    with got_request_exception.connected_to(_on_exc, app):
        resp = client.open(url, method=method, data={})
    out = {"status": resp.status_code}
    if 300 <= resp.status_code < 400:
        out["location"] = urlparse(resp.headers.get("Location", "")).path
    if caught:
        out["error"] = caught[0]
    return out


def _record(app):
    result = {}
    for key, method, url in _cells(app):
        result[key] = {role: _observe(app, method, url, user) for role, user in ROLES.items()}
    return result


def test_route_matrix_matches_recorded_behaviour(synthetic_app):
    observed = _record(synthetic_app)
    if os.environ.get("UPDATE_ROUTE_MATRIX") == "1":
        with open(MATRIX_PATH, "w", encoding="utf-8") as f:
            json.dump(observed, f, ensure_ascii=False, indent=1, sort_keys=True)
        pytest.skip("route matrix re-recorded")
    with open(MATRIX_PATH, encoding="utf-8") as f:
        expected = json.load(f)
    diffs = [f"{k} [{role}]: expected {expected.get(k, {}).get(role)} got {v[role]}"
             for k, v in observed.items() for role in ROLES
             if expected.get(k, {}).get(role) != v[role]]
    assert not diffs, "route behaviour changed:\n" + "\n".join(diffs)


def test_every_route_is_in_the_matrix(synthetic_app):
    with open(MATRIX_PATH, encoding="utf-8") as f:
        expected = json.load(f)
    missing = [k for k, _, _ in _cells(synthetic_app) if k not in expected]
    stale = [k for k in expected if k not in {c[0] for c in _cells(synthetic_app)}]
    assert not missing, f"routes missing from route_matrix.json: {missing}"
    assert not stale, f"routes in route_matrix.json that no longer exist: {stale}"


def test_no_route_returns_server_error_for_logged_in_roles(synthetic_app):
    """Known 500s are listed explicitly; the list may only shrink."""
    with open(MATRIX_PATH, encoding="utf-8") as f:
        expected = json.load(f)
    errors = sorted(f"{k} [{role}]" for k, v in expected.items() for role, cell in v.items()
                    if cell["status"] >= 500)
    assert errors == KNOWN_SERVER_ERRORS, (
        "server errors changed; if one was fixed, remove it from KNOWN_SERVER_ERRORS:\n"
        + "\n".join(sorted(set(errors) ^ set(KNOWN_SERVER_ERRORS))))


# Recorded 2026-10-26. Each entry is a bug tracked in docs/accounting/A01 (BUG-01..03).
# Fixing one = remove it here in the same PR; adding one is never allowed.
KNOWN_SERVER_ERRORS: list[str] = sorted([
    "GET /manager/insurance_arrears/export [manager.export_insurance_arrears] [manager]",  # BUG-01
    "GET /manager/staff [manager.staff] [manager]",                                        # BUG-02
    "GET /reception/my_shifts [reception.my_shifts_report] [doctor]",                      # BUG-03
    "GET /reception/my_shifts [reception.my_shifts_report] [manager]",                     # BUG-03
    "GET /reception/my_shifts [reception.my_shifts_report] [reception]",                   # BUG-03
])
