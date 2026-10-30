"""CSRF token helpers for the cookie-authenticated PWA.

The one authenticated principal is :class:`shift.services.auth.Principal`; routes
declare it through ``shift.api.deps.require``. This module owns only the CSRF
tokens the browser carries:

* :func:`csrf_cookie_token` issues the double-submit cookie rendered into the
  shell's ``<meta name="csrf-token">``; the same token comes back in the
  ``X-CSRFToken`` header (the spelling the accounting app uses).
* :func:`csrf_token_matches` is the one constant-time comparison the guards use
  for both the session token and that cookie.
"""

import secrets

from fastapi import Request

CSRF_COOKIE_NAME = "shift_csrf"
CSRF_HEADER_NAME = "X-CSRFToken"
CSRF_MAX_AGE_SECONDS = 12 * 60 * 60
_CSRF_TOKEN_MIN_LENGTH = 32


def new_csrf_token() -> str:
    """Mint a CSRF token for the double-submit cookie."""
    return secrets.token_urlsafe(32)


def is_csrf_token(value: str | None) -> bool:
    """Return whether the value looks like a token this module issued."""
    return bool(value) and len(value) >= _CSRF_TOKEN_MIN_LENGTH and not any(ch.isspace() for ch in value)


def csrf_token_matches(sent: str | None, expected: str | None) -> bool:
    """Constant-time comparison of a header token with one expected proof."""
    return is_csrf_token(sent) and is_csrf_token(expected) and secrets.compare_digest(sent, expected)


def csrf_cookie_token(request: Request) -> tuple[str, bool]:
    """Return the existing CSRF token and whether a new cookie must be set."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if is_csrf_token(token):
        return token, False
    return new_csrf_token(), True
