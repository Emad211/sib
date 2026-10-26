"""Every rendered page's inline JavaScript must parse (ACC-1.8 follow-up).

A single syntax error in a page's <script> silently disables every button on that page
(it happened once while editing manager/settings.html). This renders each GET page for
each role on the synthetic DB and runs `node --check` on every inline script.
"""

import re
import shutil
import subprocess

import pytest
from conftest import login_as
from test_route_matrix import PATH_VALUES

NODE = shutil.which("node")
SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _pages(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint in ("static", "auth.logout") or "GET" not in rule.methods:
            continue
        if "export" in rule.rule or "download" in rule.rule or "/api/" in rule.rule or rule.rule.endswith("/stats"):
            continue  # CSV / JSON / file responses
        yield re.sub(r"<(?:\w+:)?(\w+)>", lambda m: PATH_VALUES[m.group(1)], rule.rule)


def test_inline_scripts_parse_on_every_page(synthetic_app, tmp_path):
    checked, broken = 0, []
    for username in ("manager1", "reception1", "doctor1"):
        client = synthetic_app.test_client()
        for url in sorted(set(_pages(synthetic_app))):
            login_as(client, synthetic_app, username)  # a page must never be judged while logged out
            resp = client.get(url, follow_redirects=True)
            if resp.status_code != 200 or resp.mimetype != "text/html":
                continue
            for n, body in enumerate(SCRIPT.findall(resp.get_data(as_text=True))):
                if not body.strip():
                    continue
                f = tmp_path / f"s{checked}.js"
                f.write_text(body, encoding="utf-8")
                checked += 1
                r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, encoding="utf-8")
                if r.returncode != 0:
                    lines = r.stderr.strip().splitlines()
                    first = next((ln for ln in lines if "Error" in ln), lines[0] if lines else "?")
                    broken.append(f"{url} [{username}] script #{n}: {first}")
    assert checked > 60, f"only {checked} inline scripts checked — pages are not being reached"
    assert not broken, "inline JavaScript does not parse:\n" + "\n".join(sorted(set(broken)))
