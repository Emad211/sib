"""Request guards: session cookie resolution, CSRF enforcement and `require()`.

Every JSON route declares exactly one guard through `require(permission)` or
`require_session()`; `tests/integration/test_route_matrix.py` walks the dependency tree
and fails the build when a route declares none (or a different one than its matrix row).
"""

import hmac
from collections.abc import Callable

from fastapi import Request

from shift.api.errors import CsrfRejected, Forbidden, LoginRequired, Unauthorized
from shift.domain.permissions import Permission
from shift.services.auth import SESSION_COOKIE, AuthService, Principal, SessionInvalid

PREAUTH_CSRF_COOKIE = "shift_csrf"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
GUARD_ATTRIBUTE = "shift_guard"
AUTHENTICATED = "authenticated"
LOGIN_REQUIRED = "ورود لازم است."
NO_PERMISSION = "نقش شما اجازهٔ این کار را ندارد."
BAD_CSRF = "توکن امنیتی درخواست معتبر نیست؛ صفحه را دوباره باز کنید."


def auth_service(request: Request) -> AuthService:
    """The app-wide auth service created in the lifespan."""
    return request.app.state.auth


def principal_or_none(request: Request) -> Principal | None:
    """Resolve the session cookie, treating every refusal as "not logged in"."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    try:
        return auth_service(request).authenticate(token)
    except SessionInvalid:
        return None


def current_principal(request: Request) -> Principal:
    """The caller, or `401` when there is no usable session."""
    principal = principal_or_none(request)
    if principal is None:
        raise Unauthorized(LOGIN_REQUIRED)
    return principal


def enforce_csrf(request: Request, principal: Principal) -> None:
    """Unsafe verbs must echo the session's CSRF token in `X-CSRF-Token`."""
    if request.method not in UNSAFE_METHODS:
        return
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent or not hmac.compare_digest(sent, principal.csrf_token):
        raise CsrfRejected(BAD_CSRF)


def login_csrf(request: Request) -> None:
    """Login has no session yet: the double-submit cookie is the CSRF proof there."""
    cookie = request.cookies.get(PREAUTH_CSRF_COOKIE, "")
    sent = request.headers.get(CSRF_HEADER, "")
    if not cookie or not sent or not hmac.compare_digest(cookie, sent):
        raise CsrfRejected(BAD_CSRF)


def require(permission: Permission) -> Callable[[Request], Principal]:
    """Dependency: a session that holds `permission`, CSRF-checked on unsafe verbs."""

    def guard(request: Request) -> Principal:
        principal = current_principal(request)
        enforce_csrf(request, principal)
        if permission not in principal.permissions:
            raise Forbidden(NO_PERMISSION)
        return principal

    setattr(guard, GUARD_ATTRIBUTE, f"permission:{permission.value}")
    return guard


def require_session() -> Callable[[Request], Principal]:
    """Dependency: any live session (used by `me` and `logout`)."""

    def guard(request: Request) -> Principal:
        principal = current_principal(request)
        enforce_csrf(request, principal)
        return principal

    setattr(guard, GUARD_ATTRIBUTE, AUTHENTICATED)
    return guard


def require_page(permission: Permission) -> Callable[[Request], Principal]:
    """Dependency for HTML pages: no session means a redirect, not a JSON 401."""

    def guard(request: Request) -> Principal:
        principal = principal_or_none(request)
        if principal is None:
            raise LoginRequired(request.url.path)
        enforce_csrf(request, principal)
        if permission not in principal.permissions:
            raise Forbidden(NO_PERMISSION)
        return principal

    setattr(guard, GUARD_ATTRIBUTE, f"permission:{permission.value}")
    return guard


def declared_guards(dependant: object) -> frozenset[str]:
    """Every guard marker in one route's dependency tree (used by the matrix meta-test)."""
    found: set[str] = set()
    pending = [dependant]
    while pending:
        node = pending.pop()
        marker = getattr(getattr(node, "call", None), GUARD_ATTRIBUTE, None)
        if isinstance(marker, str):
            found.add(marker)
        pending.extend(getattr(node, "dependencies", []))
    return frozenset(found)
