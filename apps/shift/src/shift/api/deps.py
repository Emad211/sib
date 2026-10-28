"""Request guards: session resolution, CSRF enforcement and `require()`.

Every JSON route declares exactly one guard through `require(permission)` or
`require_session()`; `tests/integration/test_route_matrix.py` walks the dependency tree
and fails the build when a route declares none (or a different one than its matrix row).

Unsafe requests must echo a CSRF proof in `X-CSRFToken`: the session's own token from
`GET /api/v1/auth/me`, or the `shift_csrf` double-submit cookie the shell renders into
its meta tag (the PWA has no session yet when it queues its first event).
"""

from collections.abc import Callable

from fastapi import Request

from shift.api.errors import CsrfRejected, Forbidden, LoginRequired, Unauthorized
from shift.api.principal import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, csrf_token_matches
from shift.common.clock import to_tehran
from shift.domain.permissions import Permission
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering
from shift.services.auth import SESSION_COOKIE, AuthService, Principal, SessionInvalid

CSRF_HEADER = CSRF_HEADER_NAME
PREAUTH_CSRF_COOKIE = CSRF_COOKIE_NAME
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
GUARD_ATTRIBUTE = "shift_guard"
AUTHENTICATED = "authenticated"
LOGIN_REQUIRED = "ورود لازم است."
NO_PERMISSION = "نقش شما اجازهٔ این کار را ندارد."
BAD_CSRF = "توکن امنیتی درخواست معتبر نیست؛ صفحه را دوباره باز کنید."


def auth_service(request: Request) -> AuthService:
    """The app-wide auth service created in the lifespan; fail closed when unwired."""
    service: AuthService | None = getattr(request.app.state, "auth", None)
    if service is None:
        raise Unauthorized(LOGIN_REQUIRED)
    return service


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
    """Unsafe verbs must echo either the session token or the double-submit cookie."""
    if request.method not in UNSAFE_METHODS:
        return
    sent = request.headers.get(CSRF_HEADER, "")
    if csrf_token_matches(sent, principal.csrf_token):
        return
    if csrf_token_matches(sent, request.cookies.get(PREAUTH_CSRF_COOKIE)):
        return
    raise CsrfRejected(BAD_CSRF)


def login_csrf(request: Request) -> None:
    """Login has no session yet: the double-submit cookie is the CSRF proof there."""
    cookie = request.cookies.get(PREAUTH_CSRF_COOKIE, "")
    sent = request.headers.get(CSRF_HEADER, "")
    if not csrf_token_matches(sent, cookie):
        raise CsrfRejected(BAD_CSRF)


def running_shift(request: Request) -> ShiftKey:
    """The shift the clinic clock says is running now (routes inject it for R-09)."""
    return covering(to_tehran(request.app.state.clock.now_utc()))


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
