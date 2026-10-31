"""PWA shell assets: precache coverage, versioning, CSP and the real home shell (review S1/S6/S9/S11/S12)."""

import json
import re
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from shift.__version__ import __version__
from shift.api.deps import CSRF_HEADER
from shift.api.principal import CSRF_COOKIE_NAME
from shift.app import create_app
from shift.config.settings import Settings

_STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "shift" / "static"


def _service_worker(client: TestClient) -> str:
    response = client.get("/sw.js")
    assert response.status_code == 200
    return response.text


def _precache_urls(sw_text: str) -> list[str]:
    match = re.search(r"const PRECACHE_URLS = \[(.*?)\];", sw_text, re.S)
    assert match is not None
    return re.findall(r'"([^"]+)"', match.group(1))


def test_manifest_start_url_is_precached_and_every_precache_url_serves(client: TestClient) -> None:
    """The OS launch URL must be in the cache; one renamed asset must fail tests."""
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["start_url"] == "/?src=pwa"
    assert manifest["id"] == "/"

    sw_text = _service_worker(client)
    precache = _precache_urls(sw_text)
    assert manifest["start_url"] in precache
    assert "/" in precache
    assert "cache.addAll" not in sw_text, "atomic addAll silently disables the whole cache"
    assert "Promise.allSettled" in sw_text

    for url in precache:
        response = client.get(url)
        assert response.status_code == 200, url


def test_service_worker_headers_and_home_document(client: TestClient) -> None:
    """The worker keeps root scope and is never HTTP-cached."""
    sw = client.get("/sw.js")
    assert sw.headers["service-worker-allowed"] == "/"
    assert "no-cache" in sw.headers["cache-control"]

    home = client.get("/")
    assert 'dir="rtl"' in home.text
    assert 'rel="manifest"' in home.text


def test_service_worker_version_is_injected_from_app_version(client: TestClient) -> None:
    """A version bump busts the cache without editing sw.js by hand."""
    sw_text = _service_worker(client)

    assert f'const VERSION = "{__version__}"' in sw_text
    assert "shift-cache-v${VERSION}" in sw_text
    assert "__SHIFT_VERSION__" not in sw_text


def test_service_worker_never_fabricates_a_queued_offline_post(client: TestClient) -> None:
    """The dead POST branch claimed durability it never provided (S12)."""
    sw_text = _service_worker(client)

    assert 'status: "queued"' not in sw_text
    assert "offline: true" not in sw_text
    assert 'req.method !== "GET"' in sw_text


def test_service_worker_serves_api_answers_network_only(client: TestClient) -> None:
    """ADR-016/R-02: a handover answer never waits in a cache; only the shell may be cached."""
    sw_text = _service_worker(client)

    assert 'url.pathname.startsWith("/api/")' in sw_text
    assert "/static/handover.js" in _precache_urls(sw_text)
    assert "/static/handover.css" in _precache_urls(sw_text)


def test_service_worker_never_stores_a_manager_page(client: TestClient) -> None:
    """S01 §6: the card is manager-only, so no manager path may enter the phone's cache."""
    sw_text = _service_worker(client)

    assert 'url.pathname === "/manager"' in sw_text
    assert 'url.pathname.startsWith("/manager/")' in sw_text
    assert not [url for url in _precache_urls(sw_text) if url.startswith("/manager")]


def test_declared_font_weights_are_all_precached(client: TestClient) -> None:
    """Every @font-face URL in app.css must survive an offline reload."""
    css = (_STATIC_DIR / "app.css").read_text(encoding="utf-8")
    declared = set(re.findall(r"url\('(/static/fonts/[^']+\.woff2)'\)", css))
    precache = set(_precache_urls(_service_worker(client)))

    assert declared
    assert declared <= precache


def test_shell_pages_send_baseline_security_headers(client: TestClient) -> None:
    """CSP replaces inline handlers; no sniffing, framing or referrer leaks."""
    for path in ("/", "/offline.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
        assert "onclick=" not in response.text

    offline = client.get("/offline.html").text
    assert re.search(r"<script(?![^>]*\bsrc=)[^>]*>", offline) is None


def test_home_issues_a_double_submit_csrf_token(client: TestClient) -> None:
    """The cookie and the meta tag must carry the same token."""
    response = client.get("/")
    cookie_token = client.cookies.get(CSRF_COOKIE_NAME)
    meta_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.text)

    assert cookie_token
    assert meta_match is not None
    assert meta_match.group(1) == cookie_token
    assert len(cookie_token) >= 32


def test_csrf_client_uses_the_accounting_header_name() -> None:
    """A shared server-side check must not reject the PWA's header spelling."""
    script = (_STATIC_DIR / "csrf.js").read_text(encoding="utf-8")

    assert CSRF_HEADER == "X-CSRFToken"
    assert "X-CSRFToken" in script
    assert "X-CSRF-Token" not in script


def test_production_home_is_a_shell_not_a_demo_form(settings: Settings) -> None:
    """Production renders shift state and ADR-016 guidance, never demo identities."""
    with TestClient(create_app(replace(settings, environment="production"))) as client:
        text = client.get("/").text

    assert "شیفت فعال درمانگاه" in text
    assert "تحویل شیفت" in text
    for forbidden in (
        "staff_user_1",
        "device_web_client",
        "shift_demo_1",
        "item_demo",
        "loc_demo",
        "handover.cash_counted",
        "handover.item_counted",
        'id="event-actor"',
        'id="quick-event-form"',
    ):
        assert forbidden not in text


def test_development_home_form_collects_no_identity_and_only_offline_types(client: TestClient) -> None:
    """The dev form cannot forge actor/device/shift and offers no handover events."""
    text = client.get("/").text

    assert 'id="quick-event-form"' in text
    assert "stock.waste_reported" in text
    assert "stock.emergency_withdrawal" in text
    for forbidden in ("staff_user_1", "device_web_client", "shift_demo_1", 'id="event-actor"', "handover."):
        assert forbidden not in text


def test_manifest_is_served_as_json_with_install_metadata(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")
    data = json.loads(response.text)

    assert response.headers["content-type"].startswith("application/manifest+json")
    assert data["display"] == "standalone"
    assert data["display_override"] == ["standalone", "minimal-ui"]
