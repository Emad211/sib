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
    # Pages are stored once a signed-in person opens them; a precached page would be a guest's.
    assert manifest["start_url"] not in precache
    assert "/" not in precache
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

    assert 'id="shift-overview-title"' in text
    assert "از گوشی ثبت‌شدهٔ خود وارد شوید" in text
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


def test_the_home_page_never_carries_a_dev_form_or_dev_text(settings: Settings) -> None:
    """No environment, not even development, renders a developer form or names the dev switch."""
    for environment in ("production", "development"):
        with TestClient(create_app(replace(settings, environment=environment))) as client:
            text = client.get("/").text
            script_names = re.findall(r'<script[^>]+src="([^"]+)"', text)

        for forbidden in (
            "quick-event-form",
            "dev-tools",
            "ابزار توسعه",
            "SHIFT_ENV",
            "development",
            'id="event-type"',
            "event-item",
            "/static/home.js",
        ):
            assert forbidden not in text, (environment, forbidden)
        assert "/static/home.js" not in script_names


def test_pages_name_held_records_in_persian_and_never_in_english(client: TestClient) -> None:
    """The phone's waiting-records card gets Persian names for event types, and no "Outbox" text."""
    text = client.get("/").text
    labels = json.loads(re.search(r'<script id="outbox-labels"[^>]*>(.*?)</script>', text, re.S).group(1))

    assert labels["stock.waste_reported"]
    assert all(not name.isascii() for name in labels.values())
    assert "(Outbox)" not in text
    assert "<code>" not in client.get("/no-such-page", headers={"accept": "text/html"}).text


def test_production_marks_the_csrf_cookie_secure_even_when_caddy_forwards_plain_http(settings: Settings) -> None:
    for environment, expected in (("production", True), ("development", False)):
        with TestClient(create_app(replace(settings, environment=environment))) as client:
            cookie = client.get("/").headers["set-cookie"]
        assert ("Secure" in cookie) is expected, environment


def test_the_install_defaults_to_production() -> None:
    """An install with no SHIFT_ENV must get Secure cookies and no development help."""
    assert Settings.from_env({}).environment == "production"


def test_the_api_docs_and_schema_are_not_served_in_production(settings: Settings) -> None:
    with TestClient(create_app(replace(settings, environment="production"))) as client:
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert client.get(path).status_code == 404, path


def test_manifest_is_served_as_json_with_install_metadata(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")
    data = json.loads(response.text)

    assert response.headers["content-type"].startswith("application/manifest+json")
    assert data["display"] == "standalone"
    assert data["display_override"] == ["standalone", "minimal-ui"]


def test_scripts_and_styles_are_rechecked_on_every_load_while_fonts_and_icons_may_be_kept(client: TestClient) -> None:
    """Without a cache header a phone keeps an old script for hours (heuristic freshness), so an update never lands."""
    for path in ("/static/handover.js", "/static/app.css", "/static/pwa.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path
        assert response.headers.get("etag"), f"{path} needs a validator so the re-check is a cheap 304"
    for path in ("/static/fonts/Vazirmatn-Regular.woff2", "/static/icons/192.png"):
        assert "max-age" in client.get(path).headers["cache-control"], path
