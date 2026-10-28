"""Web routes: serves PWA shell, service worker, manifest, and offline fallback."""

from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["web"])

_SRC_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _SRC_DIR / "templates"
_STATIC_DIR = _SRC_DIR / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse, summary="Serve PWA home shell")
def get_home(request: Request) -> HTMLResponse:
    """Render the main PWA shell."""
    csrf_token = request.cookies.get("shift_csrf", "")
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"request": request, "csrf_token": csrf_token},
    )


@router.get("/offline.html", summary="Serve offline fallback page")
def get_offline() -> FileResponse:
    """Serve the static offline page."""
    offline_file = _STATIC_DIR / "offline.html"
    return FileResponse(
        path=offline_file,
        media_type="text/html; charset=utf-8",
        status_code=status.HTTP_200_OK,
    )


@router.get("/manifest.webmanifest", summary="Serve PWA web manifest")
def get_manifest() -> FileResponse:
    """Serve the installable PWA manifest."""
    manifest_file = _STATIC_DIR / "manifest.webmanifest"
    return FileResponse(
        path=manifest_file,
        media_type="application/manifest+json",
        status_code=status.HTTP_200_OK,
    )


@router.get("/sw.js", summary="Serve service worker with root scope and no-cache")
def get_service_worker() -> FileResponse:
    """Serve sw.js with root scope permissions and no-cache header."""
    sw_file = _STATIC_DIR / "sw.js"
    headers = {
        "Service-Worker-Allowed": "/",
        "Cache-Control": "no-cache, no-store, must-revalidate",
    }
    return FileResponse(
        path=sw_file,
        media_type="application/javascript; charset=utf-8",
        headers=headers,
        status_code=status.HTTP_200_OK,
    )
