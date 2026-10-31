"""Manager pages: the daily card, its history and the settings screen (S01 §6, R-10).

Server-rendered and phone-first (R-10): the manager reads one screen in the morning, so each page does
one job, the emergency block leads the card, and no page carries a reference number (R-02, ADR-011).
Every page declares exactly one page guard through `deps.require_page`, so a non-manager receives a
403 whose body holds no card data at all, and these pages answer `Cache-Control: no-store` because a
manager's figures must not sit in a shared cache.

The card is the document `DailyCardService` stored, not a fresh read of the log: the page only formats
what the scheduler already built (SHF-6.1). A section the stored card does not have is not rendered at
all; a section that exists but has no rows for the day gets one muted line and no figure.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from shift.adapters.db import handover_repo, staff_repo
from shift.api import deps
from shift.api.routes_web import _SECURITY_HEADERS, _shell_page
from shift.api.templating import templates
from shift.common.clock import TEHRAN, parse_iso, to_tehran
from shift.common.jalali import format_jalali
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.permissions import Permission
from shift.domain.policy import SETTING_SPECS, PolicyError, SettingKey, SettingKind, ShiftKey
from shift.domain.shift_events import FieldKind, KindSpec, kind_for
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, ItemKind
from shift.services.daily_card import (
    SHORTAGE,
    STATUS_APPROVED,
    STATUS_ASSIGNED,
    STATUS_FIXED,
    STATUS_PENDING,
    STATUS_RECORDED,
    STATUS_REJECTED,
    STATUS_REPORTED,
    SURPLUS,
    Card,
    DailyCardService,
    Person,
    ShiftEventLine,
)
from shift.services.handover import HandoverError, HandoverService
from shift.services.rewards import CONFIRMED, PENDING, REVOKED
from shift.services.settings import SettingsService, SettingValue

CARD_PATH = "/manager/card"
HISTORY_PATH = "/manager/cards"
SETTINGS_PATH = "/manager/settings"
BASELINE_PATH = "/manager/baseline"
ITEMS_PATH = "/manager/items"
BASELINE_API_PATH = "/api/v1/manager/baseline"
NO_STORE = "no-store"
FRIDAY = 4
SATURDAY = 5
# SHF-7.1: which shelves each handover flow counts (H-12 splits them by custodian, not by shelf).
FLOW_LOCATIONS: dict[HandoverFlow, tuple[str, ...]] = {
    HandoverFlow.RECEPTION: ("shelf_reception",),
    HandoverFlow.NURSING: ("cpr_room",),
}
FLOW_IDS = {HandoverFlow.RECEPTION: "reception", HandoverFlow.NURSING: "nursing"}
ITEM_KIND_LABELS = {
    ItemKind.COUNTED: "شمارشی",
    ItemKind.YES_NO: "بله/خیر",
    ItemKind.READING: "عدد خواندنی",
    ItemKind.PHOTO: "عکس",
}
BASELINE_ALREADY = "شمارش اولیهٔ این جریان قبلاً ثبت شده است."
BASELINE_REFUSED = "شمارش اولیه ثبت نشد: "
BASELINE_CUSTODIAN = "نگه‌دارنده باید از کارکنان فعال درمانگاه باشد."
BASELINE_ITEMS = "اقلام شمارش اولیه باید همان اقلام شمارشی همان جریان باشند."

CARD_NOT_FOUND = "کارتی برای این روز پیدا نشد."


class BaselineItemIn(BaseModel):
    """One shelf of the manager's opening count: what the shelf holds right now, zero included."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=128)
    location_id: str = Field(min_length=1, max_length=128)
    quantity: int = Field(ge=0)


class BaselineIn(BaseModel):
    """The whole opening count of one flow, sent once by the manager's phone (SHF-7.1)."""

    model_config = ConfigDict(extra="forbid")

    flow: HandoverFlow
    custodian_id: str = Field(min_length=1, max_length=64)
    opening_cash: int | None = Field(default=None, ge=0)
    items: list[BaselineItemIn] = Field(min_length=1)


class BaselineOut(BaseModel):
    """What the baseline fixed: the flow, who holds it, the box and how many shelves it set."""

    flow: str
    custodian_id: str
    opening_cash: int | None
    shelves: int
    recorded_at_tehran: str


FLOW_LABELS = {"reception": "پذیرش", "nursing": "پرستاری"}
# Which way a gap went, so the manager reads a shortage from a surplus and never a bare figure.
DIRECTION_LABELS = {SHORTAGE: "کسری", SURPLUS: "مازاد"}
# The one status each shift-event row can carry, in the manager's words (SHF-3).
SHIFT_EVENT_STATUS_LABELS = {
    STATUS_PENDING: "در انتظار تأیید",
    STATUS_APPROVED: "تأییدشده",
    STATUS_REJECTED: "ردشده",
    STATUS_REPORTED: "باز",
    STATUS_ASSIGNED: "ارجاع‌شده",
    STATUS_FIXED: "تعمیرشده",
    STATUS_RECORDED: "ثبت‌شده",
}
BOOLEAN_LABELS = {True: "بله", False: "خیر"}
MULTI_SEPARATOR = "، "
TOMAN = "تومان"
# The three states a reward row can carry on the card, in the manager's words (S01 §5).
REWARD_STATUS_LABELS = {PENDING: "در انتظار تأیید", CONFIRMED: "تأییدشده", REVOKED: "ابطال‌شده"}
# A campaign the calendar no longer holds keeps its count and has no price; the card never invents one.
UNKNOWN_FIGURE = "نامعلوم"
SHIFT_LABELS = {1: "شیفت صبح", 2: "شیفت عصر", 3: "شیفت شب"}
WEEKDAY_LABELS = {
    0: "دوشنبه",
    1: "سه‌شنبه",
    2: "چهارشنبه",
    3: "پنجشنبه",
    4: "جمعه",
    5: "شنبه",
    6: "یکشنبه",
}
# The Persian week starts on Saturday; `date.weekday()` counts Monday as 0 (see `SettingKind.WEEKDAY`).
WEEK_ORDER = (5, 6, 0, 1, 2, 3, 4)
SETTING_LABELS = {
    SettingKey.CARD_TIME: "ساعت کارت روزانه",
    SettingKey.TAKEOVER_WINDOW_MINUTES: "مهلت تحویل گرفتن (دقیقه)",
    SettingKey.WITHDRAWAL_CAP_TOMAN: "سقف برداشت از صندوق (تومان)",
    SettingKey.SESSION_TTL_HOURS: "اعتبار نشست (ساعت)",
    SettingKey.WEEKLY_COUNT_TIME: "ساعت هشدار شمارش هفتگی",
    SettingKey.WEEKLY_COUNT_WEEKDAY: "روز شمارش هفتگی",
}

# A page guard each: a signed-out browser is sent to the login screen, a non-manager gets a 403.
CardViewer = Annotated[Principal, Depends(deps.require_page(Permission.DAILY_CARD_VIEW))]
SettingsEditor = Annotated[Principal, Depends(deps.require_page(Permission.SETTINGS_CHANGE))]
BaselineEditor = Annotated[Principal, Depends(deps.require_page(Permission.HANDOVER_BASELINE))]
ItemsEditor = Annotated[Principal, Depends(deps.require_page(Permission.CATALOG_MANAGE))]

CardsService = Callable[[Request], DailyCardService]
SettingsServiceSource = Callable[[Request], SettingsService]


def _handover_service(request: Request) -> HandoverService:
    """The handover service created in the lifespan (the manager's opening count writes through it)."""
    return request.app.state.handover


def _catalog_service(request: Request) -> CatalogService:
    """The catalog service created in the lifespan (the item pages read it)."""
    return request.app.state.catalog


def build_manager_router(*, cards: CardsService, settings: SettingsServiceSource) -> APIRouter:
    """Build the manager pages; the two services come from the app state, never from a global."""
    pages = APIRouter(tags=["manager"])
    CardsDep = Annotated[DailyCardService, Depends(cards)]
    SettingsDep = Annotated[SettingsService, Depends(settings)]
    HandoverDep = Annotated[HandoverService, Depends(_handover_service)]
    CatalogDep = Annotated[CatalogService, Depends(_catalog_service)]

    @pages.get(CARD_PATH, response_class=HTMLResponse, summary="The newest daily card")
    def get_manager_card(request: Request, _viewer: CardViewer, service: CardsDep, policy: SettingsDep) -> HTMLResponse:
        """The newest stored card, or when the next one arrives on an install that has none yet."""
        card = service.latest()
        if card is not None:
            return _card_page(request, card)
        return _shell_page(
            request,
            "manager_card.html",
            {"card": None, "next_card": _next_card_view(request, policy)},
            cache_control=NO_STORE,
        )

    @pages.get(f"{CARD_PATH}/{{card_date}}", response_class=HTMLResponse, summary="One daily card by date")
    def get_manager_card_day(card_date: str, request: Request, _viewer: CardViewer, service: CardsDep) -> HTMLResponse:
        """One stored card by its ISO date; an unknown or malformed day is the shared 404 page."""
        try:
            day = date.fromisoformat(card_date)
        except ValueError:
            return _not_found()
        card = service.get(day)
        if card is None:
            return _not_found()
        return _card_page(request, card)

    @pages.get(HISTORY_PATH, response_class=HTMLResponse, summary="Past daily cards")
    def get_manager_cards(request: Request, _viewer: CardViewer, service: CardsDep) -> HTMLResponse:
        """The stored cards, newest first, each one a link to its own page."""
        return _shell_page(
            request,
            "manager_cards.html",
            {"cards": [_history_row(card) for card in service.history()]},
            cache_control=NO_STORE,
        )

    @pages.get(SETTINGS_PATH, response_class=HTMLResponse, summary="The clinic settings screen")
    def get_manager_settings(request: Request, _editor: SettingsEditor, service: SettingsDep) -> HTMLResponse:
        """Every setting a shift sees, with the value waiting for the next shift (R-09)."""
        snapshot = service.snapshot(deps.running_shift(request))
        rows = [_setting_row(key, snapshot[key], service.history(key)[0]) for key in SettingKey]
        return _shell_page(request, "manager_settings.html", {"settings": rows}, cache_control=NO_STORE)

    @pages.get(BASELINE_PATH, response_class=HTMLResponse, summary="The manager's initial count")
    def get_manager_baseline(
        request: Request,
        _editor: BaselineEditor,
        handover: HandoverDep,
        catalog: CatalogDep,
    ) -> HTMLResponse:
        """One flow at a time: the box (reception only), its shelves and who holds them now (SHF-7.1).

        A flow that already has its baseline shows a one-line note instead of the form: the first
        count is what every later blind count compares against, so it is recorded once and never
        rewritten (the service refuses a second one).
        """
        shift = deps.running_shift(request)
        places = {place.location_id: place.name for place in catalog.locations(shift)}
        with request.app.state.database.read() as connection:
            people = staff_repo.list_with_roles(connection)
            recorded = {flow: handover_repo.has_baseline(connection, flow) for flow in FLOW_LOCATIONS}
        flows = []
        for flow, location_ids in FLOW_LOCATIONS.items():
            groups = [
                {
                    "location_id": location_id,
                    "name": places.get(location_id, location_id),
                    "rows": [
                        {"item_id": item.item_id, "name": item.name, "unit": item.unit}
                        for item in catalog.items(shift, location_id=location_id)
                        if item.kind is ItemKind.COUNTED
                    ],
                }
                for location_id in location_ids
            ]
            flows.append(
                {
                    "id": FLOW_IDS[flow],
                    "label": FLOW_LABELS.get(FLOW_IDS[flow], FLOW_IDS[flow]),
                    "cash": flow is HandoverFlow.RECEPTION,
                    "recorded": recorded[flow],
                    "locations": groups,
                }
            )
        staff = [
            {"id": person.staff.staff_id, "name": person.staff.full_name} for person in people if person.staff.active
        ]
        return _shell_page(request, "manager_baseline.html", {"flows": flows, "staff": staff}, cache_control=NO_STORE)

    @pages.post(BASELINE_API_PATH, response_model=BaselineOut, status_code=status.HTTP_201_CREATED)
    def post_manager_baseline(
        body: BaselineIn,
        request: Request,
        manager: Annotated[Principal, Depends(deps.require(Permission.HANDOVER_BASELINE))],
        handover: HandoverDep,
        catalog: CatalogDep,
    ) -> BaselineOut:
        """Record the opening count through the service that owns it; the CLI stays the fallback."""
        if body.flow is HandoverFlow.RECEPTION and body.opening_cash is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "شمارش اولیهٔ پذیرش باید موجودی صندوق را هم داشته باشد.")
        if body.flow is HandoverFlow.NURSING and body.opening_cash is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "جریان پرستاری صندوق نقدی ندارد.")
        shift = deps.running_shift(request)
        allowed = {
            (item.item_id, item.location_id)
            for location_id in FLOW_LOCATIONS[body.flow]
            for item in catalog.items(shift, location_id=location_id)
            if item.kind is ItemKind.COUNTED
        }
        if any((entry.item_id, entry.location_id) not in allowed for entry in body.items):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, BASELINE_ITEMS)
        with request.app.state.database.read() as connection:
            custodian = staff_repo.find(connection, body.custodian_id)
        if custodian is None or not custodian.active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, BASELINE_CUSTODIAN)
        try:
            record = handover.record_baseline(
                flow=body.flow,
                custodian_id=body.custodian_id,
                manager_id=manager.staff_id,
                opening_cash=body.opening_cash,
                items=[entry.model_dump() for entry in body.items],
            )
        except HandoverError as error:
            message = BASELINE_ALREADY if "already recorded" in str(error) else BASELINE_REFUSED + str(error)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from error
        return BaselineOut(
            flow=record.flow.value,
            custodian_id=record.custodian_id,
            opening_cash=record.opening_cash,
            shelves=record.shelves,
            recorded_at_tehran=to_tehran(record.recorded_at).isoformat(),
        )

    @pages.get(ITEMS_PATH, response_class=HTMLResponse, summary="The clinic's items and their codes")
    def get_manager_items(
        request: Request,
        _editor: ItemsEditor,
        catalog: CatalogDep,
    ) -> HTMLResponse:
        """Every item as the **next** shift will see it, the ones without an accounting code first.

        A catalog change takes effect from the next shift and never in the middle of one (R-09), so
        the page reads the next shift: after the manager saves a code, the reload must show the code
        it just typed. Reading the running shift here showed the old version and looked like a
        refusal (review F1).
        """
        target = deps.running_shift(request).following()
        places = {place.location_id: place.name for place in catalog.locations(target)}
        items = catalog.items(target)
        counted = [item for item in items if item.kind is ItemKind.COUNTED]
        rows = [_item_row(item, places) for item in sorted(items, key=_item_order)]
        missing = sum(1 for item in counted if not item.acc_code)
        return _shell_page(
            request,
            "manager_items.html",
            {"items": rows, "counted": len(counted), "missing": missing},
            cache_control=NO_STORE,
        )

    return pages


def _card_page(request: Request, card: Card) -> HTMLResponse:
    """One card, formatted once here so the template carries Persian text and no figures logic."""
    return _shell_page(request, "manager_card.html", {"card": _card_view(card)}, cache_control=NO_STORE)


def _not_found() -> HTMLResponse:
    """The shared 404 page, keeping the security headers and the no-store rule of these pages."""
    page = templates.get_template("error.html")
    response = HTMLResponse(
        page.render(status=404, code="not_found", message=CARD_NOT_FOUND),
        status_code=404,
    )
    response.headers.update(_SECURITY_HEADERS)
    response.headers["Cache-Control"] = NO_STORE
    return response


# --- formatting: one layer, the clinic's own helpers -------------------------------------------------


def _moment_fa(moment: datetime) -> str:
    """`1405/08/04 14:10` for an instant, in the clinic's Jalali calendar and Tehran time."""
    local = to_tehran(moment)
    return f"{format_jalali(local.date())} {local:%H:%M}"


def _stamp_fa(iso_text: str) -> str:
    """The same stamp for the Tehran ISO text the card stores."""
    return _moment_fa(parse_iso(iso_text))


def _shift_fa(text: str) -> str:
    """A stored shift key as `1405/08/04 · شیفت صبح`; the install-time baseline has no date."""
    try:
        key = ShiftKey.parse(text)
    except PolicyError:
        return text
    if key.work_date == date.min:
        return "از آغاز نصب"
    return f"{format_jalali(key.work_date)} · {SHIFT_LABELS.get(key.rank, key.text)}"


def _person(person: Person) -> str:
    """The name the roster holds now, or the id of someone who has left it."""
    return person.name or person.id


def _flow(flow: str) -> str:
    return FLOW_LABELS.get(flow, flow)


def _coverage(card: Card) -> str:
    """Which day (or two, on Saturday) the card covers — the manager's first question."""
    if card.covered_from == card.covered_to:
        return format_jalali(card.covered_from)
    return f"{format_jalali(card.covered_from)} تا {format_jalali(card.covered_to)}"


def _card_view(card: Card) -> dict[str, Any]:
    return {
        "date": format_jalali(card.card_date),
        "coverage": _coverage(card),
        "generated_at": _moment_fa(card.generated_at),
        "sections": _sections(card),
    }


def _sections(card: Card) -> dict[str, list[dict[str, Any]]]:
    """Every section of the card as rows of text; a section the stored card lacks is left out."""
    builders: dict[str, Callable[[Card], list[dict[str, Any]]]] = {
        "emergency_starts": _emergency_rows,
        "over_cap_withdrawals": _withdrawal_rows,
        "late_cash": _late_cash_rows,
        "held_events": _held_rows,
        "cash_exceptions": _cash_rows,
        "discrepancies": _discrepancy_rows,
        "flows": _flow_rows,
        "open_flows": _open_flow_rows,
        "shift_events": _shift_event_rows,
        "open_events": _open_event_rows,
        "rewards": _reward_rows,
        "campaign_discounts": _campaign_rows,
        # SHF-4: the purchasing chain's open decisions and the warehouse's unfinished work (M-19..M-21).
        "purchase_orders": _purchase_order_rows,
        "flagged_deliveries": _flagged_delivery_rows,
        "parcels_in_transit": _parcel_rows,
        "weekly_count": _weekly_count_rows,
        "below_minimum": _below_minimum_rows,
        # C1: counts per code with their details, overdue repairs and the control-call sample.
        "event_counts": _event_count_rows,
        "overdue_faults": _overdue_fault_rows,
        "call_sample": _call_sample_rows,
    }
    return {name: build(card) for name, build in builders.items() if name in card.present_sections}


def _emergency_rows(card: Card) -> list[dict[str, str]]:
    """H-08: who started the handover alone, which manager confirmed it by phone, and when."""
    return [
        {
            "flow": _flow(entry.flow),
            "handover_id": entry.handover_id,
            "recorded_by": _person(entry.recorded_by),
            "manager": _person(entry.manager),
            "at": _stamp_fa(entry.at_tehran),
        }
        for entry in card.emergency_starts
    ]


def _cash_rows(card: Card) -> list[dict[str, str]]:
    """A receipt stamped inside a flow's own window: its handover, its flow and its amount."""
    return [
        {
            "handover_id": entry.handover_id,
            "flow": _flow(entry.flow),
            "amount": f"{entry.amount:,}",
            "outflow": f"{entry.outflow:,}",
            "at": _stamp_fa(entry.at_tehran),
        }
        for entry in card.cash_exceptions
    ]


def _discrepancy_rows(card: Card) -> list[dict[str, str]]:
    """The gap, which way it went, and its person; `culprit` None is the unnamed path (H-07/H-08)."""
    return [
        {
            "flow": _flow(entry.flow),
            "difference": f"{entry.difference:,}",
            "direction": DIRECTION_LABELS.get(entry.direction, ""),
            "culprit": None if entry.culprit is None else _person(entry.culprit),
            "at": _stamp_fa(entry.at_tehran),
        }
        for entry in card.discrepancies
    ]


def _flow_rows(card: Card) -> list[dict[str, str]]:
    """The day's flows: kind, shift, open or closed, and the two people the log names."""
    return [
        {
            "flow": _flow(entry.flow),
            "shift": _shift_fa(entry.shift),
            "state": "بسته" if entry.closed else "باز",
            "outgoing": _person(entry.outgoing),
            "incoming": _person(entry.incoming),
            "at": _stamp_fa(entry.opened_at_tehran),
        }
        for entry in card.flows
    ]


def _open_flow_rows(card: Card) -> list[dict[str, str]]:
    """The flows still waiting for a signature, oldest first, with how long they have waited."""
    return [
        {
            "flow": _flow(entry.flow),
            "shift": _shift_fa(entry.shift),
            "age_hours": f"{entry.age_hours:,}",
            "outgoing": _person(entry.outgoing),
            "incoming": _person(entry.incoming),
            "at": _stamp_fa(entry.opened_at_tehran),
        }
        for entry in card.open_flows
    ]


def _shift_event_rows(card: Card) -> list[dict[str, Any]]:
    """The day's shift events, newest first: what was recorded, by whom, and what it says now."""
    return [_shift_event_row(entry) for entry in card.shift_events]


def _open_event_rows(card: Card) -> list[dict[str, Any]]:
    """What is still waiting, oldest first, with how long the clinic has waited for it."""
    return [{**_shift_event_row(entry.row), "age_hours": f"{entry.age_hours:,}"} for entry in card.open_events]


def _event_count_rows(card: Card) -> list[dict[str, Any]]:
    """«رویدادهای ۲۴ ساعت»: the count of each code, with the day's rows of that code to open (C1)."""
    return [
        {
            "code": entry.code,
            "label": entry.label_fa,
            "count": str(entry.count),
            "rows": [_shift_event_row(row) for row in card.shift_events if row.code == entry.code],
        }
        for entry in card.event_counts
    ]


def _overdue_fault_rows(card: Card) -> list[dict[str, str]]:
    """An assigned repair past its deadline, highlighted: who owes it and how many days late (C1)."""
    return [
        {
            "device": entry.device,
            "assignee": entry.assignee,
            "due": format_jalali(date.fromisoformat(entry.due_on)),
            "days_late": f"{entry.days_late:,}",
        }
        for entry in card.overdue_faults
    ]


def _call_sample_rows(card: Card) -> list[dict[str, str]]:
    """The registrations drawn for a control call: the file number, the day and who registered (C1)."""
    return [
        {
            "patient_number": entry.patient_number,
            "day": format_jalali(date.fromisoformat(entry.registered_on)),
            "registrar": entry.registrar,
        }
        for entry in card.call_sample
    ]


def _shift_event_row(entry: ShiftEventLine) -> dict[str, Any]:
    """One shift-event row: the kind's label and code, its actor, its instant and its own fields."""
    photo = entry.summary.get("photo_sha256")
    row: dict[str, Any] = {
        "event_id": entry.event_id or "",
        "decidable": entry.status == "pending" and entry.event_id is not None,
        "code": entry.code,
        "label": entry.label_fa,
        "actor": _person(entry.actor),
        "at": _stamp_fa(entry.at_tehran),
        "status": SHIFT_EVENT_STATUS_LABELS.get(entry.status, entry.status),
        "fields": _event_fields(kind_for(entry.event_type), entry.summary),
    }
    # SHF-7: the receipt's hash becomes a link, never a figure. The route that serves the bytes is
    # guarded to the manager and the approvers, exactly the people who read this card.
    if isinstance(photo, str) and photo:
        row["photo"] = f"/api/v1/photos/{photo}"
    return row


def _event_fields(kind: KindSpec | None, summary: dict[str, Any]) -> list[dict[str, str]]:
    """The row's values, labelled by the kind's own `KindSpec` so the page matches the phone."""
    specs = {field.key: field for field in kind.fields} if kind is not None else {}
    order = [key for key in (kind.card_fields if kind is not None else ()) if key in summary]
    order += [key for key in summary if key not in order]
    fields: list[dict[str, str]] = []
    for key in order:
        value = summary[key]
        # The receipt photo is a link (`photo` on the row), never a 64-character figure on the card.
        if key.endswith("_sha256"):
            continue
        if value is None:
            continue
        spec = specs.get(key)
        fields.append(
            {
                "label": key if spec is None else spec.label_fa,
                "value": _field_fa(None if spec is None else spec.kind, value),
            }
        )
    return fields


def _field_fa(field_kind: FieldKind | None, value: Any) -> str:
    """One value in the clinic's words: an amount in Toman, a quantity as counted, a multi joined."""
    if field_kind is FieldKind.AMOUNT:
        return f"{int(value):,} {TOMAN}"
    if field_kind is FieldKind.BOOLEAN:
        return BOOLEAN_LABELS[bool(value)]
    if field_kind is FieldKind.MULTI:
        return MULTI_SEPARATOR.join(str(item) for item in value)
    if field_kind is FieldKind.DATE:
        return _date_fa(str(value))
    return str(value)


def _date_fa(text: str) -> str:
    """A stored ISO date as Jalali; text that is not a date is kept as the card holds it."""
    try:
        return format_jalali(date.fromisoformat(text))
    except ValueError:
        return text


def _history_row(card: Card) -> dict[str, str]:
    return {
        "href": f"{CARD_PATH}/{card.card_date.isoformat()}",
        "date": format_jalali(card.card_date),
        "coverage": _coverage(card),
        "generated_at": _moment_fa(card.generated_at),
    }


def _next_card_view(request: Request, settings: SettingsService) -> dict[str, str]:
    """When the scheduler stores the next card: the setting's own time, and no card on Friday."""
    at = settings.clock_time(SettingKey.CARD_TIME)
    upcoming = _next_card_at(request.app.state.clock.now_utc(), at)
    note = "کارت شنبه دو روز (پنجشنبه و جمعه) را پوشش می‌دهد." if upcoming.weekday() == SATURDAY else ""
    return {"at": f"{format_jalali(upcoming.date())} ساعت {upcoming:%H:%M}", "note": note}


def _next_card_at(now: datetime, at: time) -> datetime:
    """The first instant the daily card is due at or after `now`; Friday is skipped (ADR-018)."""
    local = to_tehran(now)
    candidate = datetime.combine(local.date(), at, tzinfo=TEHRAN)
    if candidate <= local:
        candidate += timedelta(days=1)
    while candidate.weekday() == FRIDAY:
        candidate += timedelta(days=1)
    return candidate


def _setting_row(key: SettingKey, current: SettingValue, newest: SettingValue) -> dict[str, Any]:
    """One setting: its current value, when that value took effect, and the value waiting (R-09)."""
    spec = SETTING_SPECS[key]
    pending = newest if newest.version != current.version else None
    row: dict[str, Any] = {
        "key": key.value,
        "label": SETTING_LABELS[key],
        "value": current.value,
        "kind": spec.kind.value,
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "effective_from": _shift_fa(current.effective_from_shift.text),
        "hint": _hint(spec.kind, spec.minimum, spec.maximum),
        "pending": None
        if pending is None
        else {"value": pending.value, "from": _shift_fa(pending.effective_from_shift.text)},
    }
    if spec.kind is SettingKind.WEEKDAY:
        row["options"] = [
            {"value": str(day), "label": WEEKDAY_LABELS[day], "selected": str(day) == current.value}
            for day in WEEK_ORDER
        ]
    return row


def _hint(kind: SettingKind, minimum: int | None, maximum: int | None) -> str:
    """The value the field accepts, in the manager's words, taken from the policy bounds."""
    if kind is SettingKind.CLOCK_TIME:
        return "ساعت را ۲۴ ساعته بنویسید، مثل 07:30."
    if kind is SettingKind.WEEKDAY or minimum is None or maximum is None:
        return ""
    return f"عددی بین {minimum:,} و {maximum:,}."


def _withdrawal_rows(card: Card) -> list[dict[str, str]]:
    """Every above-cap withdrawal of the window, prominently, next to the emergency starts."""
    return [
        {
            "event_id": entry.event_id,
            "pending": entry.status == "pending",
            "amount": f"{entry.amount:,}",
            "reason": entry.reason,
            "recipient": _person(entry.recipient),
            "manager": _person(entry.manager),
            "at": _stamp_fa(entry.at_tehran),
        }
        for entry in card.over_cap_withdrawals
    ]


def _late_cash_rows(card: Card) -> list[dict[str, str]]:
    """A cash record that arrived late: when it happened, when it arrived, and the gap it explains."""
    return [
        {
            "event_id": entry.event_id,
            "handover_id": entry.handover_id or "",
            "amount": f"{entry.amount:,}",
            "occurred_at": _stamp_fa(entry.occurred_at_tehran),
            "received_at": _stamp_fa(entry.received_at_tehran),
            "actor": _person(entry.actor),
            "explains": "بله" if entry.explains_discrepancy else "خیر",
        }
        for entry in card.late_cash
    ]


def _held_rows(card: Card) -> list[dict[str, str]]:
    """Records the server kept for the manager: released or rejected from this page (F5)."""
    return [
        {
            "held_event_id": entry.held_event_id,
            "event_id": entry.event_id,
            "label": entry.label,
            "message": entry.message,
            "occurred_at": _stamp_fa(entry.occurred_at_tehran),
            "received_at": _stamp_fa(entry.received_at_tehran),
            "actor": _person(entry.actor),
        }
        for entry in card.held_events
    ]


def _item_order(item: Any) -> tuple[int, str, str]:
    """Counted items with no code first, then counted, then the checklist; each by shelf and name."""
    rank = 2
    if item.kind is ItemKind.COUNTED:
        rank = 0 if not item.acc_code else 1
    return (rank, item.location_id, item.name)


def _item_row(item: Any, places: dict[str, str]) -> dict[str, Any]:
    """One editable row: the item as the shift sees it, and the Persian words for its kind and shelf."""
    return {
        "item_id": item.item_id,
        "name": item.name,
        "kind": item.kind.value,
        "kind_label": ITEM_KIND_LABELS.get(item.kind, item.kind.value),
        "location_id": item.location_id,
        "location_name": places.get(item.location_id, item.location_id),
        "unit": item.unit,
        "minimum": item.minimum,
        "acc_code": item.acc_code or "",
        "stock_factor": item.stock_factor,
        "needs_code": item.kind is ItemKind.COUNTED and not item.acc_code,
    }


def _reward_rows(card: Card) -> list[dict[str, str]]:
    """The day's rewards and the ones still waiting: earner, work, amount, and a deduction's month."""
    return [
        {
            "earner": _person(entry.earner),
            "work": entry.label_fa,
            "status": REWARD_STATUS_LABELS.get(entry.status, entry.status),
            "amount": f"{entry.amount:,}",
            "at": _stamp_fa(entry.at_tehran),
            "deduction_month": "" if entry.deduction_month is None else entry.deduction_month.replace("-", "/"),
        }
        for entry in card.rewards
    ]


def _campaign_rows(card: Card) -> list[dict[str, str]]:
    """One campaign that discounted in the window: how many, what it costs and what it gave away."""
    return [
        {
            "name": entry.name,
            "service": entry.service,
            "count": f"{entry.count:,}",
            "amount": UNKNOWN_FIGURE if entry.amount is None else f"{entry.amount:,}",
            "total": UNKNOWN_FIGURE if entry.total is None else f"{entry.total:,}",
        }
        for entry in card.campaign_discounts
    ]


# --- the purchasing chain and the warehouse (SHF-4, M-19..M-21) ------------------------------

_WEEKLY_STATE_FA = {"done": "انجام شده", "due": "در انتظار شمارش", "overdue": "گذشته از موعد"}


def _purchase_order_rows(card: Card) -> list[dict[str, str]]:
    """M-19: an order waiting for the manager, its lines and how long it has waited."""
    return [
        {
            "order_id": entry.order_id,
            "actor": _person(entry.actor),
            "at": _stamp_fa(entry.at_tehran),
            "age_hours": str(entry.age_hours),
            "line_count": str(entry.line_count),
            "lines": "، ".join(entry.lines),
            "note": entry.note or "",
        }
        for entry in card.purchase_orders
    ]


def _flagged_delivery_rows(card: Card) -> list[dict[str, str]]:
    """M-19: a problem parcel whose payment waits for the manager."""
    return [
        {
            "flag_event_id": entry.flag_event_id or "",
            "delivery_id": entry.delivery_id,
            "invoice_reference": entry.invoice_reference,
            "reason": entry.reason_fa,
            "actor": _person(entry.actor),
            "at": _stamp_fa(entry.at_tehran),
            "age_hours": str(entry.age_hours),
        }
        for entry in card.flagged_deliveries
    ]


def _parcel_rows(card: Card) -> list[dict[str, str]]:
    """M-20: a parcel still on its way to the storekeeper, with its age in hours."""
    return [
        {
            "delivery_id": entry.delivery_id,
            "invoice_reference": entry.invoice_reference,
            "packages": "" if entry.packages is None else str(entry.packages),
            "actor": _person(entry.actor),
            "at": _stamp_fa(entry.at_tehran),
            "age_hours": str(entry.age_hours),
        }
        for entry in card.parcels_in_transit
    ]


def _weekly_count_rows(card: Card) -> list[dict[str, Any]]:
    """M-20: the main store's count for this week and the window it belongs to."""
    return [
        {
            "state": _WEEKLY_STATE_FA.get(entry.state, entry.state),
            "window": f"{format_jalali(date.fromisoformat(entry.window_from))} تا "
            f"{format_jalali(date.fromisoformat(entry.window_to))}",
            "last_count": "" if entry.last_count_on is None else format_jalali(date.fromisoformat(entry.last_count_on)),
        }
        for entry in card.weekly_count
    ]


def _below_minimum_rows(card: Card) -> list[dict[str, Any]]:
    """M-21: an item under its minimum, with the figure it stands at now."""
    return [
        {
            "name": entry.name,
            "location": entry.location,
            "minimum": entry.minimum,
            "quantity": entry.quantity,
        }
        for entry in card.below_minimum
    ]
