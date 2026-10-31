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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shift.api import deps
from shift.api.routes_web import _SECURITY_HEADERS, _shell_page
from shift.api.templating import templates
from shift.common.clock import TEHRAN, parse_iso, to_tehran
from shift.common.jalali import format_jalali
from shift.domain.permissions import Permission
from shift.domain.policy import SETTING_SPECS, PolicyError, SettingKey, SettingKind, ShiftKey
from shift.domain.shift_events import FieldKind, KindSpec, kind_for
from shift.services.auth import Principal
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
from shift.services.settings import SettingsService, SettingValue

CARD_PATH = "/manager/card"
HISTORY_PATH = "/manager/cards"
SETTINGS_PATH = "/manager/settings"
NO_STORE = "no-store"
FRIDAY = 4
SATURDAY = 5

CARD_NOT_FOUND = "کارتی برای این روز پیدا نشد."

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

CardsService = Callable[[Request], DailyCardService]
SettingsServiceSource = Callable[[Request], SettingsService]


def build_manager_router(*, cards: CardsService, settings: SettingsServiceSource) -> APIRouter:
    """Build the manager pages; the two services come from the app state, never from a global."""
    pages = APIRouter(tags=["manager"])
    CardsDep = Annotated[DailyCardService, Depends(cards)]
    SettingsDep = Annotated[SettingsService, Depends(settings)]

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
    """The card's seven sections as rows of text; a section the stored card lacks is left out."""
    builders: dict[str, Callable[[Card], list[dict[str, Any]]]] = {
        "emergency_starts": _emergency_rows,
        "cash_exceptions": _cash_rows,
        "discrepancies": _discrepancy_rows,
        "flows": _flow_rows,
        "open_flows": _open_flow_rows,
        "shift_events": _shift_event_rows,
        "open_events": _open_event_rows,
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


def _shift_event_row(entry: ShiftEventLine) -> dict[str, Any]:
    """One shift-event row: the kind's label and code, its actor, its instant and its own fields."""
    return {
        "code": entry.code,
        "label": entry.label_fa,
        "actor": _person(entry.actor),
        "at": _stamp_fa(entry.at_tehran),
        "status": SHIFT_EVENT_STATUS_LABELS.get(entry.status, entry.status),
        "fields": _event_fields(kind_for(entry.event_type), entry.summary),
    }


def _event_fields(kind: KindSpec | None, summary: dict[str, Any]) -> list[dict[str, str]]:
    """The row's values, labelled by the kind's own `KindSpec` so the page matches the phone."""
    specs = {field.key: field for field in kind.fields} if kind is not None else {}
    order = [key for key in (kind.card_fields if kind is not None else ()) if key in summary]
    order += [key for key in summary if key not in order]
    fields: list[dict[str, str]] = []
    for key in order:
        value = summary[key]
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
