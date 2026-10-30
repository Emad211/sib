"""Web routes: serves the PWA shell, service worker, manifest and offline fallback."""

from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from shift.__version__ import __version__
from shift.api.principal import CSRF_COOKIE_NAME, CSRF_MAX_AGE_SECONDS, csrf_cookie_token

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


@router.get("/", response_class=HTMLResponse, summary="Serve PWA home shell")
def get_home(request: Request) -> HTMLResponse:
    """Render the main PWA shell and issue the double-submit CSRF cookie."""
    token, is_new = csrf_cookie_token(request)
    settings = request.app.state.settings
    response = templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "request": request,
            "csrf_token": token,
            "show_dev_events": settings.environment == "development",
        },
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
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Vary"] = "Cookie"
    return response


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
