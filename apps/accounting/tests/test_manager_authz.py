"""Manager blueprint access control (ACC-2.6): every /manager route is guarded by a decorator.

`manager_required` (HTML: redirect to reception) and `manager_api_required` (JSON/export: 403)
replaced the inline `if g.user['role'] != 'manager'` checks. These tests make sure that no route
lost its guard and that a reception user still gets exactly what `route_matrix.json` records.
"""

import json

from test_route_matrix import MATRIX_PATH, _cells, _observe

from src.api.manager._common import GUARD_ATTR


def _manager_rules(app):
    return [r for r in app.url_map.iter_rules() if r.endpoint.startswith("manager.")]


def test_every_manager_route_has_a_guard_decorator(synthetic_app):
    rules = _manager_rules(synthetic_app)
    assert len(rules) > 30
    unguarded = [
        r.endpoint
        for r in rules
        if getattr(synthetic_app.view_functions[r.endpoint], GUARD_ATTR, None) not in ("html", "api")
    ]
    assert not unguarded, f"manager routes without manager_required / manager_api_required: {unguarded}"


def test_guard_sits_below_login_required(synthetic_app):
    """The guard needs g.user, so it must be wrapped by login_required (never the other way round)."""
    for rule in _manager_rules(synthetic_app):
        view = synthetic_app.view_functions[rule.endpoint]
        chain = []
        while view is not None:
            chain.append(view)
            view = getattr(view, "__wrapped__", None)
        # [login_required wrapper, manager guard wrapper, the raw view]
        assert len(chain) == 3, rule.endpoint
        login_wrapper, guard_wrapper, raw = chain
        assert guard_wrapper.__code__.co_filename.endswith("_common.py"), rule.endpoint
        assert login_wrapper.__code__.co_filename.endswith("auth.py"), rule.endpoint
        assert GUARD_ATTR not in vars(raw), rule.endpoint


def test_reception_user_gets_the_recorded_answer_on_every_manager_route(synthetic_app):
    with open(MATRIX_PATH, encoding="utf-8") as f:
        expected = json.load(f)
    checked = 0
    for key, method, url in _cells(synthetic_app):
        if "[manager." not in key:
            continue
        got = _observe(synthetic_app, method, url, "reception1")
        assert got == expected[key]["reception"], key
        checked += 1
    assert checked > 40


def test_guard_kind_matches_the_denial_style(synthetic_app):
    """HTML guard <-> redirect to reception, API guard <-> 403 JSON."""
    with open(MATRIX_PATH, encoding="utf-8") as f:
        expected = json.load(f)
    for key, _method, _url in _cells(synthetic_app):
        if "[manager." not in key:
            continue
        endpoint = key.split("[", 1)[1].rstrip("]")
        kind = getattr(synthetic_app.view_functions[endpoint], GUARD_ATTR)
        cell = expected[key]["reception"]
        if kind == "api":
            assert cell["status"] == 403, key
        else:
            assert cell["status"] == 302 and cell["location"] == "/reception/", key


def test_api_guard_bodies_are_unchanged(synthetic_app):
    """The 403 JSON bodies differ per route on purpose (they were inline before)."""
    from conftest import login_as

    cases = {
        "/manager/api/chart-data": {"error": "Unauthorized"},
        "/manager/export/invoices/csv": {"error": "Unauthorized"},
        "/manager/insurance_arrears/export": {"error": "دسترسی محدود"},
        "/manager/logs/export": {"error": "دسترسی غیرمجاز"},
        "/manager/logs/stats": {"error": "دسترسی غیرمجاز"},
    }
    client = synthetic_app.test_client()
    login_as(client, synthetic_app, "reception1")
    for url, body in cases.items():
        resp = client.get(url)
        assert resp.status_code == 403, url
        assert resp.get_json() == body, url
    resp = client.post("/manager/settings/reset-database", data={})
    assert resp.status_code == 403
    assert resp.get_json() == {"success": False, "error": "دسترسی غیرمجاز"}


def test_flash_messages_of_html_guards(synthetic_app):
    from conftest import login_as

    client = synthetic_app.test_client()
    login_as(client, synthetic_app, "reception1")
    client.get("/manager/tariffs/history")
    with client.session_transaction() as s:
        assert s["_flashes"] == [("error", "دسترسی محدود")]
