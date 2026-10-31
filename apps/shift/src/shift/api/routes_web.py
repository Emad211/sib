"""Web routes: serves the PWA shell, the handover screen, service worker, manifest and offline fallback."""

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response

from shift.__version__ import __version__
from shift.adapters.db import handover_repo
from shift.api import deps
from shift.api.principal import CSRF_COOKIE_NAME, CSRF_MAX_AGE_SECONDS, csrf_cookie_token
from shift.api.templating import templates
from shift.domain.checklist_rules import Frequency
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.handover_steps import FLOW_COUNT_LOCATIONS, FLOW_OWNER_ROLES
from shift.domain.item_icons import icon_for
from shift.domain.on_shift import held_roles, off_shift_notice
from shift.domain.permissions import Permission
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, ItemKind
from shift.services.purchase import PURCHASE_PERMISSIONS

router = APIRouter(tags=["web"])

_SRC_DIR = Path(__file__).resolve().parent.parent
_STATIC_DIR = _SRC_DIR / "static"

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
BaselineOwner = Annotated[Principal, Depends(deps.require_page(Permission.HANDOVER_BASELINE_OWN))]


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
            # Caddy terminates HTTPS and talks plain HTTP to 127.0.0.1, so production decides, not the hop.
            secure=request.url.scheme == "https" or request.app.state.settings.environment == "production",
        )
    response.headers.update(_SECURITY_HEADERS)
    response.headers["Cache-Control"] = cache_control
    response.headers["Vary"] = "Cookie"
    return response


# One big button per job on the home page, in the order of a shift (R-10). Permissions decide, never role names (R-07).
HOME_TILES: tuple[dict[str, Any], ...] = (
    {
        "needs": frozenset({Permission.HANDOVER_START}),
        "href": "/handover",
        "icon": "🔄",
        "title": "تحویل شیفت",
        "hint": "شمارش صندوق و قفسه، چک‌لیست‌ها و امضا",
    },
    {
        "needs": frozenset({Permission.EVENTS_APPEND}),
        "href": "/events",
        "icon": "📝",
        "title": "ثبت رویداد",
        "hint": "کارت‌به‌کارت، برداشت، ضایعات، خرابی و بقیه",
    },
    {
        "needs": frozenset(PURCHASE_PERMISSIONS),
        "href": "/purchase",
        "icon": "📦",
        "title": "خرید و انبار",
        "hint": "سفارش، دریافت بار، شارژ قفسه و شمارش هفتگی",
    },
    {
        "needs": frozenset({Permission.ACCOUNTANT_QUEUE_VIEW}),
        "href": "/accountant/queue",
        "icon": "🧾",
        "title": "صف حسابدار",
        "hint": "کارت‌به‌کارت، برداشت‌ها، مرجوعی و پاداش ماه",
    },
    {
        "needs": frozenset({Permission.ACCOUNTANT_QUEUE_VIEW}),
        "href": "/accountant/prices",
        "icon": "💹",
        "title": "تاریخچهٔ قیمت",
        "hint": "قیمت هر قلم به تفکیک فروشنده، از فاکتورها",
    },
    {
        "needs": frozenset({Permission.CAMPAIGN_REPORT_VIEW}),
        "href": "/accountant/campaigns",
        "icon": "🏷️",
        "title": "گزارش کمپین",
        "hint": "هر کمپین: چند تخفیف و چه جمعی",
    },
    {
        "needs": frozenset({Permission.REWARD_VIEW}),
        "href": "/rewards",
        "icon": "⭐",
        "title": "پاداش من",
        "hint": "مبلغ و رتبهٔ این ماه و هدف تیمی",
    },
    {
        "needs": frozenset({Permission.DAILY_CARD_VIEW}),
        "href": "/manager/card",
        "icon": "📊",
        "title": "کارت روزانه",
        "hint": "تصمیم‌های باز، مغایرت‌ها و هشدارها",
    },
    {
        "needs": frozenset({Permission.HANDOVER_BASELINE}),
        "href": "/manager/baseline",
        "icon": "🧮",
        "title": "شمارش اولیه",
        "hint": "شروع کار: صندوق و اقلام هر جریان",
    },
    {
        "needs": frozenset({Permission.CATALOG_MANAGE}),
        "href": "/manager/items",
        "icon": "💊",
        "title": "اقلام و کد کالا",
        "hint": "حداقل و کد کالای هر قلم",
    },
    {
        "needs": frozenset({Permission.SETTINGS_CHANGE}),
        "href": "/manager/settings",
        "icon": "⚙️",
        "title": "تنظیمات",
        "hint": "سقف‌ها، ساعت‌ها و کمپین‌ها",
    },
    {
        "needs": frozenset({Permission.STAFF_MANAGE}),
        "href": "/manager/staff",
        "icon": "👥",
        "title": "کارکنان",
        "hint": "افزودن کارمند، نقش‌ها و غیرفعال‌کردن",
    },
    {
        "needs": frozenset({Permission.REWARD_GRANT}),
        "href": "/manager/rewards",
        "icon": "🏆",
        "title": "پاداش کارکنان",
        "hint": "تأیید پاداش، نرخ‌ها و هدف تیمی",
    },
    {
        "needs": frozenset({Permission.SETTINGS_CHANGE}),
        "href": "/manager/campaigns",
        "icon": "🏷️",
        "title": "کمپین‌های تخفیف",
        "hint": "بازهٔ تخفیف و مبلغ هر کمپین",
    },
    {
        "needs": frozenset({Permission.DEVICE_ENROLL}),
        "href": "/admin/devices",
        "icon": "📱",
        "title": "گوشی‌ها",
        "hint": "ثبت گوشی تازه و لغو دسترسی",
    },
)


# Owner decision 9 (ADR-022 item 5): the flow's own person records its initial count, so the home
# screen offers that one job while the flow still has none. The manager keeps /manager/baseline.
_FLOW_LABELS = {HandoverFlow.RECEPTION: "پذیرش", HandoverFlow.NURSING: "پرستاری"}


def _own_baseline_flows(request: Request, principal: Principal | None) -> list[dict[str, Any]]:
    """The flows this person may start from scratch: their own role's flows with no first count yet.

    Only the counted items each flow actually asks for in a handover come back (`every_shift`), so the
    initial count covers exactly the shelves whose later blind count compares against it. Nothing here
    carries an expected figure: the person counts and types their own numbers (owner decision 9).
    """
    if principal is None or not principal.roles:
        return []
    held = held_roles(principal.roles)
    owned = [flow for flow, role in FLOW_OWNER_ROLES.items() if role in held]
    if not owned:
        return []
    shift = deps.running_shift(request)
    catalog: CatalogService = request.app.state.catalog
    with request.app.state.database.read() as connection:
        pending = [flow for flow in owned if not handover_repo.has_baseline(connection, flow)]
    if not pending:
        return []
    places = {place.location_id: place.name for place in catalog.locations(shift)}
    flows: list[dict[str, Any]] = []
    for flow in pending:
        locations = [
            {
                "location_id": location_id,
                "name": places.get(location_id, location_id),
                "items": [
                    {"item_id": item.item_id, "name": item.name, "unit": item.unit, "icon": icon_for(item)}
                    for item in catalog.items(shift, location_id=location_id)
                    if item.kind is ItemKind.COUNTED and item.frequency is Frequency.EVERY_SHIFT
                ],
            }
            for location_id in FLOW_COUNT_LOCATIONS[flow]
        ]
        flows.append(
            {
                "id": flow.value,
                "label": _FLOW_LABELS[flow],
                "cash": flow is HandoverFlow.RECEPTION,
                "locations": [location for location in locations if location["items"]],
            }
        )
    return flows


@router.get("/", response_class=HTMLResponse, summary="Serve PWA home shell")
def get_home(request: Request) -> HTMLResponse:
    """Render the main PWA shell and issue the double-submit CSRF cookie."""
    principal = deps.principal_or_none(request)
    # A visitor with no session keeps no tile; the sections inside each screen are cut down by the API.
    granted = principal.permissions if principal is not None else frozenset()
    tiles = [tile for tile in HOME_TILES if granted & tile["needs"]]
    # ADR-021: an off-shift person reads that their shift is not open and keeps the handover tile.
    notice = None if principal is None else off_shift_notice(principal.roles, principal.on_shift_roles)
    # Owner decision 9: the first count sits right under the handover tile, and only while it is missing.
    pending = _own_baseline_flows(request, principal)
    if pending:
        labels = " و ".join(flow["label"] for flow in pending)
        tiles.insert(
            next((index + 1 for index, tile in enumerate(tiles) if tile["href"] == "/handover"), len(tiles)),
            {
                "needs": frozenset({Permission.HANDOVER_BASELINE_OWN}),
                "href": "/handover/baseline",
                "icon": "🧮",
                "title": "شمارش اولیه",
                "hint": f"اولین بار: صندوق و اقلام {labels} را خودتان بشمارید",
            },
        )
    return _shell_page(
        request,
        "home.html",
        {
            "tiles": tiles,
            "shift_notice": notice,
            # ADR-021: the second receptionist and the shift doctor come on shift with one button.
            "presence_options": _presence_options(principal),
        },
        # The guest page must not become what the installed app shows offline.
        **({"cache_control": "no-store"} if principal is None else {}),
    )


# ADR-021: the two roles whose shift opens with «حضور در شیفت» instead of taking the box over.
_PRESENCE_LABELS = {"reception": "پذیرش (شیفت دوم)", "shift_doctor": "پزشک شیفت"}


def _presence_options(principal: Principal | None) -> list[dict[str, str]]:
    """The «حضور در شیفت» buttons this person may still press, one per bound role not yet on shift."""
    if principal is None or Permission.SHIFT_PRESENCE_DECLARE not in principal.permissions:
        return []
    working = set(principal.on_shift_roles)
    return [
        {"role": role, "label": label}
        for role, label in _PRESENCE_LABELS.items()
        if role in principal.roles and role not in working
    ]


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


@router.get("/handover/baseline", response_class=HTMLResponse, summary="The flow's own initial count")
def get_own_baseline_page(request: Request, principal: BaselineOwner) -> HTMLResponse:
    """Owner decision 9 (ADR-022 item 5): one task per screen, on the person's own phone.

    The page carries only the counted items of the flows this person's role owns and that still have no
    first count; a flow that has one is refused by the API even if the page is open. It asks for what
    the person counted, never for an expected figure, and the flow's own blind count compares against
    it afterwards. `no-store`: the list is the clinic's shelf content, not a shell.
    """
    return _shell_page(
        request,
        "handover_baseline.html",
        {"baseline_json": _json_for_script({"flows": _own_baseline_flows(request, principal)})},
        cache_control="no-store",
    )


@router.get("/purchase", response_class=HTMLResponse, summary="Serve the purchasing and warehouse screen")
def get_purchase_page(request: Request, _principal: EventsAppender) -> HTMLResponse:
    """One job per role on one screen (R-10, S01 §4).

    The page itself carries no clinic data: its script asks `GET /api/v1/purchase/state` and draws only
    the sections that answer carries, so an order, a parcel and an invoice never sit in a cached shell.
    The response asks for `no-store` because the accountant's section holds prices (R-02, ADR-016).
    """
    return _shell_page(request, "purchase.html", cache_control="no-store")


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
