"""Web routes: serves the PWA shell, the handover screen, service worker, manifest and offline fallback."""

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from shift.__version__ import __version__
from shift.api import deps
from shift.api.principal import CSRF_COOKIE_NAME, CSRF_MAX_AGE_SECONDS, csrf_cookie_token
from shift.domain.permissions import Permission
from shift.services.auth import Principal

router = APIRouter(tags=["web"])

_SRC_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _SRC_DIR / "templates"
_STATIC_DIR = _SRC_DIR / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
_BASELINE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

# The worker text is rendered from the file with the app version substituted so
# the cache name busts on every version bump (__version__ is the single source).
_SW_BODY = (_STATIC_DIR / "sw.js").read_text(encoding="utf-8").replace("__SHIFT_VERSION__", __version__)

# A page guard: no session means a redirect to the login screen, not a JSON 401.
HandoverCounter = Annotated[Principal, Depends(deps.require_page(Permission.HANDOVER_COUNT_SUBMIT))]
EventsAppender = Annotated[Principal, Depends(deps.require_page(Permission.EVENTS_APPEND))]


def _json_for_script(data: dict[str, Any]) -> str:
    """JSON that stays inside its `<script>` block: a value can never close the tag."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _shell_page(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    cache_control: str = "no-cache",
) -> HTMLResponse:
    """Render one authenticated page with the CSRF cookie, the CSP headers and no HTTP cache.

    The response stays out of the HTTP cache but not out of the worker's shell cache: a handover
    screen opened offline is the gate that says the flow needs the clinic server (ADR-016). A page
    that carries the manager's figures asks for `no-store` instead, so nothing shared keeps it.
    """
    token, is_new = csrf_cookie_token(request)
    response = templates.TemplateResponse(
        request=request,
        name=name,
        context={"request": request, "csrf_token": token, **(context or {})},
    )
    if is_new:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            max_age=CSRF_MAX_AGE_SECONDS,
            path="/",
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
    response.headers.update(_SECURITY_HEADERS)
    response.headers["Cache-Control"] = cache_control
    response.headers["Vary"] = "Cookie"
    return response


@router.get("/", response_class=HTMLResponse, summary="Serve PWA home shell")
def get_home(request: Request) -> HTMLResponse:
    """Render the main PWA shell and issue the double-submit CSRF cookie."""
    settings = request.app.state.settings
    principal = deps.principal_or_none(request)
    # The permission decides who sees the card link, never a role name (R-07, SHF-6.1).
    show_manager_link = principal is not None and Permission.DAILY_CARD_VIEW in principal.permissions
    # Same rule for the shift-event screen: a visitor with no session keeps no link (SHF-3).
    show_events_link = principal is not None and Permission.EVENTS_APPEND in principal.permissions
    return _shell_page(
        request,
        "home.html",
        {
            "show_dev_events": settings.environment == "development",
            "show_manager_link": show_manager_link,
            "show_events_link": show_events_link,
        },
    )


@router.get("/events", response_class=HTMLResponse, summary="Serve the staff shift-event screen")
def get_events_page(request: Request, principal: EventsAppender) -> HTMLResponse:
    """One job on the phone (R-10): a big button per shift event, one question at a time.

    The catalog comes from the service, built from the same registry the API validates against and
    the manager's card labels rows with, so the phone never draws a button the API would refuse
    (R-07). Nothing personal is rendered: approvals and the outbox are filled by the page's own
    script, which keeps the cached shell free of another person's work (ADR-016).
    """
    shift = deps.running_shift(request)
    wizard = request.app.state.shift_events.page_context(principal, shift)
    return _shell_page(request, "shift_events.html", {"wizard_json": _json_for_script(wizard)})


@router.get("/handover", response_class=HTMLResponse, summary="Serve the phone handover screen")
def get_handover_page(request: Request, _principal: HandoverCounter) -> HTMLResponse:
    """The screen a tired receptionist or nurse uses at 2 am (R-10).

    Both the receptionist and the nurse hold the count permission, so both may open the screen; which
    flow they may complete is decided by the API (H-10: a nurse never counts the cash box).
    """
    return _shell_page(request, "handover.html")


@router.get("/offline.html", summary="Serve offline fallback page")
def get_offline() -> FileResponse:
    """Serve the static offline page."""
    return FileResponse(
        path=_STATIC_DIR / "offline.html",
        media_type="text/html; charset=utf-8",
        headers=_SECURITY_HEADERS,
        status_code=status.HTTP_200_OK,
    )


@router.get("/manifest.webmanifest", summary="Serve PWA web manifest")
def get_manifest() -> FileResponse:
    """Serve the installable PWA manifest."""
    return FileResponse(
        path=_STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers=_BASELINE_HEADERS,
        status_code=status.HTTP_200_OK,
    )


@router.get("/sw.js", summary="Serve service worker with root scope and no-cache")
def get_service_worker() -> Response:
    """Serve sw.js with root scope permissions and a version-stamped cache name."""
    headers = {
        "Service-Worker-Allowed": "/",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        **_BASELINE_HEADERS,
    }
    return Response(
        content=_SW_BODY,
        media_type="application/javascript; charset=utf-8",
        headers=headers,
        status_code=status.HTTP_200_OK,
    )
