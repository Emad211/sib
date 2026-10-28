"""Auth routes: the login page, login/logout/me, and manager credential recovery.

Login is the only route that runs without a session, so it is protected by the
double-submit cookie issued by `GET /login`; every other route declares one guard.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from shift.api import deps
from shift.api.templating import templates
from shift.common.clock import utc_iso
from shift.domain.permissions import Permission
from shift.services.auth import SESSION_COOKIE, SESSION_TTL, Principal, StaffSummary

router = APIRouter()
PREAUTH_CSRF_TTL_SECONDS = 3600

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


class PinResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: str = Field(min_length=1, max_length=32)


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
    }


def _safe_next_path(value: str | None) -> str:
    """Only same-origin absolute paths may be used as a post-login destination."""
    if not value or not value.startswith("/") or value.startswith("//"):
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


@router.post("/api/v1/auth/login", tags=["auth"], dependencies=[Depends(deps.login_csrf)])
def login(request: Request, body: LoginRequest) -> JSONResponse:
    """Verify mobile + PIN on an enrolled device and open a 12-hour session."""
    issued = deps.auth_service(request).login(mobile=body.mobile, pin=body.pin, device_id=body.device_id)
    response = JSONResponse(_session_payload(issued.principal), headers={"Cache-Control": "no-store"})
    _set_session_cookie(response, issued.token, request)
    response.delete_cookie(deps.PREAUTH_CSRF_COOKIE, path="/")
    return response


@router.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, principal: SessionUser) -> Response:
    """Revoke the current session and clear the cookie."""
    deps.auth_service(request).logout(token=request.cookies.get(SESSION_COOKIE, ""))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/api/v1/auth/me", tags=["auth"])
def me(principal: SessionUser) -> dict[str, object]:
    """Who am I: the roles, permissions and CSRF token the page needs."""
    return _session_payload(principal)


@router.get("/api/v1/staff", tags=["staff"])
def list_staff(request: Request, _: StaffManager) -> list[dict[str, object]]:
    """Staff directory for the credential administration screen."""
    return [_staff_payload(entry) for entry in deps.auth_service(request).directory()]


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
