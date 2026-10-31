"""Installable PWA contract (final-pwa): manifest identity, real icon pixels and the offline shell.

Lean by policy: one test per behaviour that can break the install or the phone's offline work,
nothing about template cosmetics. `test_pwa_shell.py` keeps the shell/security side.
"""

import json
import re
import struct
from pathlib import Path

from fastapi.testclient import TestClient

_STATIC = Path(__file__).resolve().parents[2] / "src" / "shift" / "static"
_TEMPLATES = _STATIC.parent / "templates"
_STATIC_PREFIX = "/static/"


def _manifest(client: TestClient) -> dict:
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    return json.loads(response.text)


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def _precache_urls(sw_text: str) -> list[str]:
    match = re.search(r"const PRECACHE_URLS = \[(.*?)\];", sw_text, re.S)
    assert match is not None
    return re.findall(r'"([^"]+)"', match.group(1))


def test_manifest_names_the_app_in_persian_and_takes_its_colours_from_the_tokens(
    client: TestClient,
) -> None:
    """The launcher label is the app, never the clinic's name; colours stay one source (app.css)."""
    manifest = _manifest(client)
    css = (_STATIC / "app.css").read_text(encoding="utf-8").split("@media (prefers-color-scheme: dark)")[0]
    theme_color = re.search(r"--color-primary-button:\s*(#[0-9a-fA-F]{6})", css)
    background_color = re.search(r"--color-bg:\s*(#[0-9a-fA-F]{6})", css)

    assert manifest["name"] == "شیفت"
    assert manifest["short_name"] == "شیفت"
    assert manifest["description"]
    assert manifest["dir"] == "rtl"
    assert manifest["lang"].startswith("fa")
    assert manifest["id"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["start_url"] == "/?src=pwa"
    assert manifest["display"] == "standalone"
    assert manifest["orientation"].startswith("portrait")
    assert theme_color is not None and background_color is not None
    assert manifest["theme_color"].lower() == theme_color.group(1).lower()
    assert manifest["background_color"].lower() == background_color.group(1).lower()
    # A shortcut the API would refuse by permission is worse than no shortcut at all.
    assert not manifest.get("shortcuts")


def test_every_manifest_icon_is_a_real_png_at_its_declared_size(client: TestClient) -> None:
    """An icon whose pixels do not match its `sizes` is silently dropped by Android."""
    manifest = _manifest(client)
    declared = {(icon["sizes"], icon.get("purpose", "any")) for icon in manifest["icons"]}

    for size in ("192x192", "512x512"):
        assert (size, "any") in declared
        assert (size, "maskable") in declared

    for icon in manifest["icons"]:
        assert icon["type"] == "image/png"
        assert icon["src"].startswith(_STATIC_PREFIX)
        path = _STATIC / icon["src"][len(_STATIC_PREFIX) :]
        assert _png_size(path) == tuple(int(part) for part in icon["sizes"].split("x")), path
        assert client.get(icon["src"]).status_code == 200


def test_both_shells_install_cleanly_with_a_local_font(client: TestClient) -> None:
    """Login and the app carry the Apple tags and preload the vendored font, so no CDN is needed."""
    for path in ("/", "/login"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "viewport-fit=cover" in page.text, path
        assert "/static/icons/apple-180.png" in page.text, path
        assert 'rel="preload"' in page.text and "Vazirmatn-Regular.woff2" in page.text, path
        assert 'name="apple-mobile-web-app-capable"' in page.text, path

    apple_icon = client.get("/static/icons/apple-180.png")
    assert apple_icon.status_code == 200
    assert _png_size(_STATIC / "icons" / "apple-180.png") == (180, 180)


def test_no_template_or_static_file_reaches_the_internet(client: TestClient) -> None:
    """Inside Iran a CDN is a blank screen: every asset must be same-origin."""
    external = re.compile(r"(?:https?:)?//(?!127\.0\.0\.1|localhost)[a-z0-9.-]+\.[a-z]{2,}", re.I)
    sources = [
        *_TEMPLATES.rglob("*.html"),
        *_STATIC.rglob("*.html"),
        *_STATIC.rglob("*.css"),
        *_STATIC.rglob("*.js"),
    ]

    assert sources
    for path in sources:
        assert external.search(path.read_text(encoding="utf-8")) is None, path


def test_precache_covers_the_shell_and_survives_one_missing_file(client: TestClient) -> None:
    """One renamed asset must not cost the whole offline shell; every listed URL is real."""
    sw_text = client.get("/sw.js").text
    precache = _precache_urls(sw_text)

    assert "/static/app.css" in precache
    assert "/static/pwa.js" in precache
    assert "/static/fonts/Vazirmatn-Regular.woff2" in precache
    assert "cache.addAll" not in sw_text, "atomic addAll silently disables the whole cache"
    assert "Promise.allSettled" in sw_text
    assert "throw new Error" not in sw_text, "a failed precache must not reject the install"

    for url in precache:
        assert client.get(url).status_code == 200, url


def test_the_update_banner_is_persian_and_the_outbox_survives_the_reload(client: TestClient) -> None:
    """A new version waits for the person: no silent switch, no outbox reset."""
    sw_text = client.get("/sw.js").text
    pwa_text = client.get("/static/pwa.js").text
    home = client.get("/").text

    assert sw_text.count("self.skipWaiting()") == 1
    assert "SKIP_WAITING" in sw_text
    assert "SKIP_WAITING" in pwa_text
    assert "نسخهٔ تازه آماده است" in home
    assert 'id="update-reload"' in home
    assert "indexedDB" not in pwa_text, "the updater must never open or clear the outbox store"


def test_the_install_offer_is_home_only_standalone_aware_and_dismissed_once(client: TestClient) -> None:
    """Install UI stays out of an installed app and out of every inner screen."""
    pwa_text = client.get("/static/pwa.js").text
    home = client.get("/").text

    assert "beforeinstallprompt" in pwa_text
    assert "display-mode: standalone" in pwa_text
    assert 'pathname === "/"' in pwa_text
    assert "shift:install-dismissed" in pwa_text
    assert 'id="install-btn"' in home
    assert "onclick=" not in home, "CSP forbids inline handlers; pwa.js binds the button"


def test_the_stylesheet_keeps_the_phone_layout_usable(client: TestClient) -> None:
    """360px phone: no sideways scroll, no zoom on focus, 44px targets, keyboard-safe actions."""
    css = (_STATIC / "app.css").read_text(encoding="utf-8")

    assert re.search(r"html\s*\{[^}]*overflow-x:\s*hidden", css, re.S)
    assert re.search(r"input,\s*select,\s*textarea\s*\{[^}]*font-size:\s*16px", css, re.S)
    assert "--touch-target: 44px" in css
    assert re.search(r"\.action-bar\s*\{[^}]*position:\s*sticky", css, re.S)
    assert "safe-area-inset-bottom" in css
