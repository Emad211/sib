"""Auth routes: the login page, login/logout/me, and manager credential recovery.

Login is the only route that runs without a session, so it is protected by the
double-submit cookie issued by `GET /login`; every other route declares one guard.
"""

import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from shift.api import deps
from shift.api.errors import BadRequest, TooManyAttempts
from shift.api.templating import templates
from shift.common.clock import utc_iso
from shift.domain.on_shift import off_shift_notice
from shift.domain.permissions import Permission, Role
from shift.domain.pin_policy import normalise_digits
from shift.domain.policy import PolicyError
from shift.services.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    Principal,
    StaffNotFound,
    StaffSummary,
)
from shift.services.catalog import CatalogError

router = APIRouter()
PREAUTH_CSRF_TTL_SECONDS = 3600
# Control characters, whitespace and the backslash: a URL parser may turn any of them into a
# path separator or drop them, which is how `?next=` values escape the origin.
_UNSAFE_NEXT_CHARACTERS = re.compile(r"[\x00-\x20\x7f\\]")

SessionUser = Annotated[Principal, Depends(deps.require_session())]
StaffManager = Annotated[Principal, Depends(deps.require(Permission.STAFF_MANAGE))]
LockManager = Annotated[Principal, Depends(deps.require(Permission.STAFF_UNLOCK))]
PinManager = Annotated[Principal, Depends(deps.require(Permission.STAFF_PIN_RESET))]


class LoginRequest(BaseModel):
    """`device_id` comes from the phone's local storage after QR pairing."""

    model_config = ConfigDict(extra="forbid")

    mobile: str = Field(min_length=1, max_length=32)
    pin: str = Field(min_length=1, max_length=32)
    device_id: str = Field(min_length=1, max_length=64)
    # ADR-027: the authenticator's six digits, sent once the server asked for them.
    otp: str | None = Field(default=None, max_length=12)


class PinResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: str = Field(min_length=1, max_length=32)


class StaffCreateRequest(BaseModel):
    """A new person: name, mobile, a first PIN and one or more starting roles (ADR-022)."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=80)
    mobile: str = Field(min_length=1, max_length=32)
    pin: str = Field(min_length=1, max_length=32)
    roles: list[Role] = Field(min_length=1)


class StaffActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class StaffRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granted: bool


def _cookies_are_secure(request: Request) -> bool:
    return request.app.state.settings.environment == "production"


def _set_preauth_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        deps.PREAUTH_CSRF_COOKIE,
        token,
        max_age=PREAUTH_CSRF_TTL_SECONDS,
        httponly=False,
        secure=_cookies_are_secure(request),
        samesite="strict",
        path="/",
    )


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=_cookies_are_secure(request),
        samesite="strict",
        path="/",
    )


def _session_payload(principal: Principal) -> dict[str, object]:
    return {
        "staff": {"id": principal.staff_id, "name": principal.full_name},
        "roles": list(principal.roles),
        "permissions": sorted(permission.value for permission in principal.permissions),
        # ADR-021: which bound roles are on shift now, and the line an off-shift person reads.
        "on_shift": list(principal.on_shift_roles),
        "shift_notice": off_shift_notice(principal.roles, principal.on_shift_roles),
        "device": {"id": principal.device_id, "label": principal.device_label},
        "csrf_token": principal.csrf_token,
        "expires_at": utc_iso(principal.expires_at),
    }


def _staff_payload(entry: StaffSummary) -> dict[str, object]:
    return {
        "id": entry.staff_id,
        "name": entry.full_name,
        "mobile": entry.mobile,
        "roles": list(entry.roles),
        "active": entry.active,
        "locked_until": entry.locked_until,
        "next_roles": list(entry.next_roles),
    }


def _safe_next_path(value: str | None) -> str:
    """Only same-origin absolute paths may be used as a post-login destination.

    Browsers fold `\\` into `/` and drop tabs and newlines before they parse a URL, so
    `//evil.example`, `/\\evil.example` and `/<tab>/evil.example` all leave the origin: any
    value carrying one of those characters is refused, not just the `//` spelling.
    """
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if _UNSAFE_NEXT_CHARACTERS.search(value):
        return "/"
    return value


@router.get("/login", response_class=HTMLResponse, tags=["auth"])
def login_page(request: Request) -> HTMLResponse:
    """Serve the login form and hand it a CSRF proof for the POST that follows."""
    token = request.cookies.get(deps.PREAUTH_CSRF_COOKIE) or secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"csrf_token": token, "next_path": _safe_next_path(request.query_params.get("next"))},
    )
    response.headers["Cache-Control"] = "no-store"
    _set_preauth_cookie(response, token, request)
    return response


def _from_outside(request: Request) -> bool:
    """A login that came through the relay's public name rather than the clinic's own address (O05)."""
    public = request.app.state.settings.public_origin
    if not public:
        return False
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return host == public.split("://", 1)[1].split(":")[0].lower()


TOO_MANY_LOGINS = "تلاش‌های ورود پشت‌سرهم زیاد بود؛ یک دقیقه بعد دوباره امتحان کنید."


def _throttle_key(mobile: str) -> str:
    """The same person however the digits were typed; an invented mobile gets its own counter too."""
    digits = "".join(ch for ch in normalise_digits(mobile) if ch.isdigit())
    return digits or mobile.strip()


@router.post("/api/v1/auth/login", tags=["auth"], dependencies=[Depends(deps.login_csrf)])
def login(request: Request, body: LoginRequest) -> JSONResponse:
    """Verify mobile + PIN on an enrolled device and open a 12-hour session."""
    if not request.app.state.login_throttle.hit(_throttle_key(body.mobile)) or not request.app.state.login_ceiling.hit(
        "*"
    ):
        raise TooManyAttempts(TOO_MANY_LOGINS)
    issued = deps.auth_service(request).login(
        mobile=body.mobile, pin=body.pin, device_id=body.device_id, otp=body.otp, remote=_from_outside(request)
    )
    response = JSONResponse(_session_payload(issued.principal), headers={"Cache-Control": "no-store"})
    _set_session_cookie(response, issued.token, request)
    # Expired with the same path/secure/samesite it was set with, so strict browsers accept it.
    response.delete_cookie(deps.PREAUTH_CSRF_COOKIE, path="/", secure=_cookies_are_secure(request), samesite="strict")
    return response


@router.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, principal: SessionUser) -> Response:
    """Revoke the current session and clear the cookie."""
    deps.auth_service(request).logout(token=request.cookies.get(SESSION_COOKIE, ""))
    response = Response(status_code=204)
    # Same attributes as `_set_session_cookie`: a mismatched deletion is ignored by some
    # strict browser engines, which would leave a dead cookie on the phone.
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=_cookies_are_secure(request),
        samesite="strict",
    )
    return response


@router.get("/api/v1/auth/me", tags=["auth"])
def me(principal: SessionUser) -> dict[str, object]:
    """Who am I: the roles, permissions and CSRF token the page needs."""
    return _session_payload(principal)


@router.get("/api/v1/staff", tags=["staff"])
def list_staff(request: Request, _: StaffManager) -> list[dict[str, object]]:
    """Staff directory for the credential administration screen."""
    return [_staff_payload(entry) for entry in deps.auth_service(request).directory()]


@router.post("/api/v1/staff", status_code=201, tags=["staff"])
def create_staff(body: StaffCreateRequest, request: Request, principal: StaffManager) -> dict[str, object]:
    """Add a person who can sign in at once; the starting roles apply immediately (ADR-022)."""
    created = deps.auth_service(request).create_staff(
        full_name=body.full_name,
        mobile=body.mobile,
        pin=body.pin,
        roles=[role.value for role in body.roles],
        actor_id=principal.staff_id,
    )
    return _staff_payload(created)


@router.post("/api/v1/staff/{staff_id}/active", status_code=204, tags=["staff"])
def set_staff_active(staff_id: str, body: StaffActiveRequest, request: Request, principal: StaffManager) -> Response:
    """Deactivate or reactivate one person; a deactivated person loses every session at once."""
    deps.auth_service(request).set_active(staff_id=staff_id, active=body.active, actor_id=principal.staff_id)
    return Response(status_code=204)


@router.put("/api/v1/staff/{staff_id}/roles/{role}", tags=["staff"])
def set_staff_role(
    staff_id: str, role: Role, body: StaffRoleRequest, request: Request, principal: StaffManager
) -> dict[str, object]:
    """Grant or take back one role from the next shift on; login and permissions read the same answer."""
    auth = deps.auth_service(request)
    if not any(entry.staff_id == staff_id for entry in auth.directory()):
        raise StaffNotFound(staff_id)
    if not body.granted:
        auth.require_manager_remains(staff_id=staff_id, role=role.value)
    try:
        change = request.app.state.catalog.set_role(
            staff_id,
            role.value,
            granted=body.granted,
            actor_id=principal.staff_id,
            running_shift=deps.running_shift(request),
        )
    except (CatalogError, PolicyError) as error:
        raise BadRequest(str(error)) from error
    return {
        "role": role.value,
        "granted": change.value.granted,
        "effective_from_shift": change.value.effective_from_shift.text,
    }


@router.post("/api/v1/staff/{staff_id}/unlock", status_code=204, tags=["staff"])
def unlock(staff_id: str, request: Request, principal: LockManager) -> Response:
    """Clear a lockout after the manager has spoken to the person holding the PIN."""
    deps.auth_service(request).unlock(staff_id=staff_id, actor_id=principal.staff_id)
    return Response(status_code=204)


@router.post("/api/v1/staff/{staff_id}/pin-reset", status_code=204, tags=["staff"])
def reset_pin(staff_id: str, body: PinResetRequest, request: Request, principal: PinManager) -> Response:
    """Set a new policy-compliant PIN and drop every session of that person."""
    deps.auth_service(request).reset_pin(staff_id=staff_id, pin=body.pin, actor_id=principal.staff_id)
    return Response(status_code=204)
