"""Authenticated principal contract and CSRF guard for the events API.

The auth lane owns ``shift.api.deps.require``; this module owns the narrow
contract the PWA slice needs so the endpoint can ship fail-closed today:

* :class:`Principal` carries the staff, enrolled device and active shift that
  the event log must record instead of client-asserted body values.
* :class:`PrincipalGuard` is the callable shape ``install_principal_guard``
  accepts.  Wiring the real dependency is one line in the coordinator's
  ``create_app`` hunk::

      install_principal_guard(app, require(Permission.EVENTS_APPEND))

  A guard may return a :class:`Principal` or a session object exposing
  ``actor_id``/``staff_id``, ``device_id``, ``shift_id`` and ``work_date``;
  :func:`as_principal` performs the (explicit) coercion.
* :func:`require_principal` is the FastAPI dependency used by the route.  When
  no guard is installed it raises 503, so an unwired deployment never accepts
  anonymous writes.
* :func:`require_csrf_token` implements double-submit validation for the
  cookie-authenticated POST.  The companion cookie is issued by
  ``routes_web.get_home`` and the header name matches the accounting app.
"""

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request, status

CSRF_COOKIE_NAME = "shift_csrf"
CSRF_HEADER_NAME = "X-CSRFToken"
CSRF_MAX_AGE_SECONDS = 12 * 60 * 60
_CSRF_TOKEN_MIN_LENGTH = 32


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated staff member on an enrolled device in an active shift."""

    actor_id: str
    device_id: str
    shift_id: str | None = None
    work_date: date | None = None


class PrincipalGuard(Protocol):
    """Callable that resolves the request session into a principal."""

    def __call__(self, request: Request) -> Principal | object | Awaitable[Principal | object]:
        """Return a principal or a session object describing it; raise on anonymous."""


def as_principal(value: Any) -> Principal:
    """Coerce a guard result into a `Principal`, rejecting empty identity fields."""
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="نشست معتبر نیست؛ دوباره وارد شوید.",
        )
    if isinstance(value, Principal):
        principal = value
    else:
        actor_id = getattr(value, "actor_id", None) or getattr(value, "staff_id", None)
        device_id = getattr(value, "device_id", None)
        if not isinstance(actor_id, str) or not isinstance(device_id, str):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="authentication guard returned no principal",
            )
        shift_id = getattr(value, "shift_id", None)
        work_date = getattr(value, "work_date", None)
        principal = Principal(
            actor_id=actor_id,
            device_id=device_id,
            shift_id=shift_id if isinstance(shift_id, str) else None,
            work_date=work_date if isinstance(work_date, date) and not isinstance(work_date, datetime) else None,
        )
    if not principal.actor_id.strip() or not principal.device_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="نشست معتبر نیست؛ دوباره وارد شوید.",
        )
    return principal


async def require_principal(request: Request) -> Principal:
    """Resolve the authenticated principal, failing closed when auth is unwired."""
    guard: Callable[[Request], Any] | None = getattr(request.app.state, "principal_guard", None)
    if guard is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured; event ingestion is closed",
        )
    result = guard(request)
    if hasattr(result, "__await__"):
        result = await result
    return as_principal(result)


def install_principal_guard(app: FastAPI, guard: Callable[[Request], Any]) -> None:
    """Install the auth dependency that resolves a request into a `Principal`."""
    app.state.principal_guard = guard


def new_csrf_token() -> str:
    """Mint a CSRF token for the double-submit cookie."""
    return secrets.token_urlsafe(32)


def is_csrf_token(value: str | None) -> bool:
    """Return whether the value looks like a token this module issued."""
    return bool(value) and len(value) >= _CSRF_TOKEN_MIN_LENGTH and not any(ch.isspace() for ch in value)


def csrf_cookie_token(request: Request) -> tuple[str, bool]:
    """Return the existing CSRF token and whether a new cookie must be set."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if is_csrf_token(token):
        return token, False
    return new_csrf_token(), True


def require_csrf_token(request: Request) -> None:
    """Reject unsafe requests whose header does not match the double-submit cookie."""
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    header = request.headers.get(CSRF_HEADER_NAME)
    if not is_csrf_token(cookie) or not is_csrf_token(header) or not secrets.compare_digest(cookie, header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )
