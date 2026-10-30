"""Static accessibility gates for the PWA stylesheet (review S10 and NICE 5/6)."""

import re
from pathlib import Path

_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "shift" / "static" / "app.css"


def _css() -> str:
    return _CSS_PATH.read_text(encoding="utf-8")


def _block(css: str, header: str) -> str:
    start = css.index(header)
    brace = css.index("{", start)
    depth = 0
    for index in range(brace, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : index]
    raise AssertionError(f"unbalanced CSS block: {header}")


def _color(block: str, token: str) -> str:
    match = re.search(rf"--{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}})", block)
    assert match is not None, token
    return match.group(1)


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    red, green, blue = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def _contrast(foreground: str, background: str) -> float:
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_primary_button_meets_wcag_aa_in_both_themes() -> None:
    """White 15px/600 button text needs 4.5:1, including hover in dark mode."""
    css = _css()
    root = _block(css, ":root {")
    dark = _block(css, "@media (prefers-color-scheme: dark)")

    text_color = _color(root, "color-on-primary")
    assert _contrast(text_color, _color(root, "color-primary-button")) >= 4.5
    assert _contrast(text_color, _color(root, "color-primary-button-hover")) >= 4.5
    assert _contrast(text_color, _color(dark, "color-primary-button")) >= 4.5
    assert _contrast(text_color, _color(dark, "color-primary-button-hover")) >= 4.5


def test_stylesheet_declares_native_color_scheme() -> None:
    """Native selects and number inputs must follow the dark theme."""
    assert re.search(r"color-scheme:\s*light dark", _css())


def test_mobile_layout_uses_dynamic_viewport_and_safe_areas() -> None:
    """iOS home-indicator and notch areas must not hide the shell or the action bar."""
    css = _css()

    assert "100dvh" in css
    assert "safe-area-inset-top" in css
    assert "safe-area-inset-bottom" in css
    assert re.search(r"\.brand\s*\{[^}]*min-height:\s*var\(--touch-target\)", css, re.S)
