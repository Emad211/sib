"""One Playwright journey for the manager's daily card: the morning screen on a phone (SHF-6.1).

Marker: e2e

S01 §6 gives the manager one screen in the morning, and R-10 keeps it to one job a page: an
emergency start leads the card, the day's gaps follow, the day's shift events and whatever still waits
on somebody come next (SHF-3), the history opens any stored card and the settings screen is where the
card time lives. This journey walks that morning once, on a phone-sized browser, against a real uvicorn
server: the app shell links the manager to the card, the emergency start a receptionist recorded leads
the screen with who recorded it, the manager who confirmed it by phone and the Tehran instant the log
stamped, the gap the alone chain left reads «بدون مقصر», the transfer the accountant has not decided
reads «در انتظار تأیید» in both shift-event blocks, the history lists the stored card and opens it, the
settings screen saves the card time and shows it waiting for the next shift (R-09), and the same browser
signed in as a receptionist is refused with a `403` that carries no card text at all.

The emergency flow is recorded through the real HTTP API before the browser opens — the outgoing
person has left, the manager confirms by phone and the incoming person settles the box alone (H-08) —
so the card the journey reads is built from the log exactly as the scheduler builds it. Everything is
synthetic: accounting from `tools/make_synthetic_acc_db.py` (emptied of invoices), staff from
`tests/data/auth_fixtures.py`, and the manager's baseline recorded the way
`tools/record_handover_baseline.py` records it. Nothing reads `_data/` or a production database
(ADR-006).
"""

import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from auth_fixtures import PIN, ROLES, Seeded, seed_roster
from synthetic_catalog import seed as seed_catalog
from synthetic_accounting import empty_accounting

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import TEHRAN, SystemClock, parse_iso, to_tehran
from shift.common.ids import new_ulid
from shift.common.jalali import format_jalali
from shift.config.settings import Settings
from shift.domain.daily_card import FRIDAY, window_for
from shift.domain.handover_fsm import HandoverFlow
from shift.services.audit import AuditService
from shift.services.catalog import CatalogService
from shift.services.daily_card import Card, DailyCardService
from shift.services.event_store import EventStore
from shift.services.handover import HandoverService
from shift.services.projections import ProjectionEngine

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402

# The people: the manager reads the card, the baseline's custodian hands the box over, and the
# receptionist finishes alone once the outgoing person has left (H-08).
MANAGER = ROLES["manager"]
RECEPTIONIST = ROLES["reception"]
OUTGOING = ROLES["multi_role"]

# Every figure is invented: the opening count is the reference the card must never render, and the
# gap is what the alone chain leaves short of it.
CASH_OPENING = 1_234_567
CASH_GAP = 4_321
SHELF_COUNTS = {"para_500_amp": 987_654, "diclofenac_75_amp": 532_119}
SETTING_DEFAULT = "07:30"
CARD_TIME = "08:15"
# The shift event of the day (SHF-3): a transfer the accountant has not decided, so it reads pending.
TRANSFER_AMOUNT = 150_000
TRANSFER_ADMISSION = "A-9001"

PHONE_VIEWPORT = {"width": 390, "height": 844}
EMERGENCY_HEADING = "شروع اضطراری تحویل"
GAP_HEADING = "مغایرت‌ها"
UNNAMED = "بدون مقصر"
CARD_TITLE = "کارت روزانهٔ مدیر"
SHIFT_EVENTS_HEADING = "رویدادهای شیفت"
OPEN_EVENTS_HEADING = "رویدادهای باز"
TRANSFER_LABEL = "کارت‌به‌کارت"
PENDING_STATUS = "در انتظار تأیید"
TOMAN = "تومان"
REFUSAL = "نقش شما اجازهٔ این کار را ندارد."
# Text that only the card page carries: a 403 may not be that page with its figures stripped out.
CARD_MARKERS = (EMERGENCY_HEADING, GAP_HEADING, UNNAMED, CARD_TITLE, "mg-amount", "mg-section")


class _UvicornServer(threading.Thread):
    """Uvicorn on an ephemeral port; the OS picks the port, so there is no TOCTOU."""

    def __init__(self, app: Any, host: str) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host=host, port=0, log_level="warning"))

    def run(self) -> None:
        self.server.run()

    def port(self) -> int:
        servers = getattr(self.server, "servers", [])
        if not servers or not servers[0].sockets:
            raise RuntimeError("uvicorn did not bind a socket")
        return int(servers[0].sockets[0].getsockname()[1])

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(timeout=10)


@dataclass(frozen=True, slots=True)
class LiveShift:
    """The running app, its URL, the app's own card service and a handle for the journey's reads."""

    base_url: str
    database: Database
    cards: DailyCardService


def _record_the_baseline(database_path: Path) -> None:
    """The manager's opening count for the reception flow, wired as the operator tool is.

    It runs before the server starts, on its own connection, so the journey never races a seed.
    """
    clock = SystemClock()
    database = Database(database_path)
    try:
        service = HandoverService(
            database,
            EventStore(database, clock=clock, hash_chain=True),
            projections=ProjectionEngine(database, clock=clock),
            catalog=CatalogService(database, clock=clock),
            audit=AuditService(database, clock=clock),
            clock=clock,
        )
        service.record_baseline(
            flow=HandoverFlow.RECEPTION,
            custodian_id=OUTGOING.staff_id,
            manager_id=MANAGER.staff_id,
            opening_cash=CASH_OPENING,
            items=[
                {"item_id": item_id, "location_id": "shelf_reception", "quantity": quantity}
                for item_id, quantity in SHELF_COUNTS.items()
            ],
        )
    finally:
        database.close()


@pytest.fixture
def live_shift(tmp_path: Path) -> Generator[LiveShift]:
    """A real server on a seeded synthetic database, with the manager's opening count recorded."""
    root = tmp_path / "manager_card_app"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".shift_secret_key",
        accounting_db_path=empty_accounting(root / "accounting.db"),
    )
    setup = Database(settings.database_path)
    try:
        setup.migrate()
        seed_roster(setup, pepper=settings.read_or_create_secret())
        seed_catalog(setup)
    finally:
        setup.close()
    _record_the_baseline(settings.database_path)

    application = create_app(settings)
    server = _UvicornServer(application, host="127.0.0.1")
    server.start()
    deadline = time.time() + 15.0
    while time.time() < deadline and not server.server.started:
        if not server.is_alive():
            raise RuntimeError("uvicorn thread died before startup")
        time.sleep(0.02)
    if not server.server.started:
        server.stop()
        raise RuntimeError("uvicorn server failed to start within 15 seconds")

    db = Database(settings.database_path)
    try:
        yield LiveShift(f"http://127.0.0.1:{server.port()}", db, application.state.daily_card)
    finally:
        server.stop()
        db.close()


@pytest.fixture(scope="module")
def browser() -> Generator[Browser]:
    """One headless Chromium for the module; closed even when the test fails."""
    with sync_playwright() as playwright_api:
        instance = playwright_api.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _open_phone(browser: Browser) -> tuple[BrowserContext, Page]:
    """A phone-sized context; the service worker is blocked so a cache never hides a live answer."""
    context = browser.new_context(viewport=PHONE_VIEWPORT, is_mobile=True, has_touch=True, service_workers="block")
    return context, context.new_page()


def _login(page: Page, base_url: str, person: Seeded) -> None:
    """Log one person in on their paired phone through the real login page.

    A manager also holds `device.enroll`, and the login screen sends that session on to the device
    admin page instead of showing its success line, so both landings count as a finished login.
    """
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.evaluate("deviceId => localStorage.setItem('shift_device_id', deviceId)", person.device_id)
    page.reload(wait_until="domcontentloaded")
    page.fill("#mobile", person.mobile)
    page.fill("#pin", PIN)
    page.click("#submit")
    page.wait_for_function(
        "() => Boolean(document.querySelector('#message.ok')) || location.pathname === '/admin/devices'",
        timeout=15000,
    )


def _sign_in_over_http(api: httpx.Client, person: Seeded) -> str:
    """The real login handshake for the test's own API call; returns the CSRF token to send."""
    api.get("/login")
    csrf = api.cookies.get("shift_csrf", "")
    signed_in = api.post(
        "/api/v1/auth/login",
        json={"mobile": person.mobile, "pin": PIN, "device_id": person.device_id},
        headers={"X-CSRFToken": csrf},
    )
    assert signed_in.status_code == 200, signed_in.text
    return signed_in.json()["csrf_token"]


def _emergency_start(base_url: str) -> str:
    """Record the flow the card must report: alone, confirmed by phone, settled unnamed (H-08)."""
    with httpx.Client(base_url=base_url, timeout=15.0) as api:
        headers = {"X-CSRFToken": _sign_in_over_http(api, RECEPTIONIST)}
        started = api.post("/api/v1/handover/start", json={"flow": "reception"}, headers=headers)
        assert started.status_code == 201, started.text
        handover_id = str(started.json()["handover_id"])
        url = f"/api/v1/handover/{handover_id}"
        left = api.post(f"{url}/early-leave", headers=headers)
        assert left.status_code == 200, left.text
        confirmed = api.post(f"{url}/emergency-start", json={"manager_id": MANAGER.staff_id}, headers=headers)
        assert confirmed.status_code == 200, confirmed.text
        # Alone: one count, one self-recount, then the settlement the manager has to read.
        counts = [
            api.post(f"{url}/counts", json={"scope": "cash", "counted": CASH_OPENING - CASH_GAP}, headers=headers)
            for _ in range(2)
        ]
        assert [response.status_code for response in counts] == [200, 200], [response.text for response in counts]
        assert [response.json()["status"] for response in counts] == ["recount", "settled"]
    return handover_id


def _report_a_card_transfer(base_url: str) -> str:
    """Record the shift event of the day: a transfer the accountant has not decided yet (SHF-3)."""
    with httpx.Client(base_url=base_url, timeout=15.0) as api:
        headers = {"X-CSRFToken": _sign_in_over_http(api, RECEPTIONIST)}
        now = SystemClock().now_utc()
        reported = api.post(
            "/api/v1/events",
            json={
                "type": "card.transfer_reported",
                "payload": {"amount": TRANSFER_AMOUNT, "admission_no": TRANSFER_ADMISSION},
                "client_at": now.isoformat(),
                "client_event_id": new_ulid(now),
            },
            headers=headers,
        )
        assert reported.status_code == 200, reported.text
        assert reported.json()["created"] is True, reported.text
        return str(reported.json()["event_id"])


def _card_day_for(moment: datetime) -> date:
    """The card date whose window covers `moment`'s Tehran day: the next run the scheduler makes.

    A card reports the day before it (S01 §6), so today's work is on tomorrow's card; when tomorrow
    is Friday there is no card and Saturday's card covers Thursday and Friday (ADR-018).
    """
    today = to_tehran(moment).date()
    run_day = today + timedelta(days=1)
    return run_day + timedelta(days=1) if run_day.weekday() == FRIDAY else run_day


def _generate_the_card(shift: LiveShift) -> Card:
    """Store the card through the scheduler's own entry point, then prove it covers the work above."""
    now = SystemClock().now_utc()
    run_day = _card_day_for(now)
    stored = shift.cards.generate_due(datetime.combine(run_day, clock_time(7, 30), tzinfo=TEHRAN))
    assert [card.card_date for card in stored] == [run_day], "the scheduler path stored no card for the day"
    window = window_for(run_day)
    assert window is not None and window.covered_from <= to_tehran(now).date() <= window.covered_to
    return stored[0]


def _emergency_confirmed_at(database: Database, handover_id: str) -> str:
    """The instant the log stamped the manager's phone confirmation: what the card has to print."""
    with database.read() as connection:
        confirmed = [
            event
            for event in events_repo.iter_handover_events(connection, handover_id)
            if event.type == "handover.emergency_start_confirmed"
        ]
    assert len(confirmed) == 1, "the emergency start was never recorded"
    return confirmed[0].server_at


def _stamp(iso_text: str) -> str:
    """A stored instant as the clinic prints it: Jalali date and Tehran time."""
    local = to_tehran(parse_iso(iso_text))
    return f"{format_jalali(local.date())} {local:%H:%M}"


@pytest.mark.e2e
def test_the_manager_reads_the_card_on_a_phone_and_a_receptionist_is_refused(
    live_shift: LiveShift, browser: Browser
) -> None:
    """One morning: the shell link, the emergency lead, the unnamed gap, the history, settings, 403."""
    handover_id = _emergency_start(live_shift.base_url)
    _report_a_card_transfer(live_shift.base_url)
    card = _generate_the_card(live_shift)
    emergency = card.emergency_starts[0]
    assert emergency.recorded_by.name == RECEPTIONIST.full_name
    (gap,) = card.discrepancies
    assert (gap.difference, gap.culprit) == (CASH_GAP, None), "the alone settlement stays unnamed (H-08)"
    assert [(row.event_type, row.status) for row in card.shift_events] == [("card.transfer_reported", "pending")]
    confirmed_at = _stamp(_emergency_confirmed_at(live_shift.database, handover_id))

    context, page = _open_phone(browser)
    try:
        _login(page, live_shift.base_url, MANAGER)

        # 1. The app shell links a manager to the card, and the link opens it (R-10, R-07).
        page.goto(f"{live_shift.base_url}/", wait_until="domcontentloaded")
        link = page.locator('a[href="/manager/card"]')
        assert link.count() == 1, "the shell must offer the manager exactly one way to the card"
        link.click()
        page.wait_for_selector(".mg-page .mg-title", timeout=15000)
        assert page.url.endswith("/manager/card")

        # 2. The emergency start leads the screen: who recorded it, which manager confirmed it by
        #    phone, and the Tehran instant the log stamped (H-08).
        emergency_section = page.locator("section[aria-labelledby='mg-emergency-title']")
        assert page.locator(".mg-page > section").first.get_attribute("aria-labelledby") == "mg-emergency-title"
        assert "mg-emergency" in (emergency_section.get_attribute("class") or "")
        emergency_text = emergency_section.inner_text()
        assert EMERGENCY_HEADING in emergency_text
        assert f"ثبت‌کننده: {RECEPTIONIST.full_name}" in emergency_text
        assert f"مدیر تأییدکننده: {MANAGER.full_name}" in emergency_text
        assert confirmed_at in emergency_text

        # 3. The gap the alone chain left, read as a figure and as nobody's fault (H-08).
        gap_section = page.locator("section[aria-labelledby='mg-gap-title']")
        gap_text = gap_section.inner_text()
        assert f"{gap.difference:,}" in gap_text
        assert UNNAMED in gap_text
        assert emergency_section.bounding_box()["y"] < gap_section.bounding_box()["y"], "the emergency block leads"

        # 4. The shift event of the day: the transfer still waiting for the accountant (SHF-3), and
        #    the same row in the open block, because a waiting approval is what the card exists for.
        events_section = page.locator("section[aria-labelledby='mg-shift-events-title']")
        assert SHIFT_EVENTS_HEADING in events_section.inner_text()
        events_text = events_section.inner_text()
        assert TRANSFER_LABEL in events_text
        assert PENDING_STATUS in events_text
        assert RECEPTIONIST.full_name in events_text
        assert f"{TRANSFER_AMOUNT:,} {TOMAN}" in events_text
        assert TRANSFER_ADMISSION in events_text
        open_section = page.locator("section[aria-labelledby='mg-open-events-title']")
        assert OPEN_EVENTS_HEADING in open_section.inner_text()
        open_text = open_section.inner_text()
        assert TRANSFER_LABEL in open_text and PENDING_STATUS in open_text
        assert gap_section.bounding_box()["y"] < events_section.bounding_box()["y"], "the events follow the gaps"

        # 5. The history lists the stored card and opens it.
        page.click('a.mg-nav-link[href="/manager/cards"]')
        page.wait_for_selector(".mg-list-link", timeout=15000)
        rows = page.locator(".mg-list-link")
        assert rows.count() == 1
        assert rows.first.get_attribute("href") == f"/manager/card/{card.card_date.isoformat()}"
        assert format_jalali(card.card_date) in rows.first.inner_text()
        rows.first.click()
        page.wait_for_selector("#mg-emergency-title", timeout=15000)
        assert page.url.endswith(f"/manager/card/{card.card_date.isoformat()}")

        # 6. The settings screen saves the card time and shows it waiting for the next shift (R-09).
        page.click('a.mg-nav-link[href="/manager/settings"]')
        page.wait_for_selector("#set-card_time", timeout=15000)
        assert page.input_value("#set-card_time") == SETTING_DEFAULT
        page.fill("#set-card_time", CARD_TIME)
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.click('form[data-setting="card_time"] button[type="submit"]')
        page.wait_for_selector("form[data-setting='card_time'] .mg-pending", timeout=15000)
        assert page.input_value("#set-card_time") == SETTING_DEFAULT, "the running shift keeps its value (R-09)"
        pending = page.inner_text("form[data-setting='card_time'] .mg-pending")
        assert CARD_TIME in pending and "تغییر در انتظار" in pending

        # 7. The same browser signed in as a receptionist is refused, and the refusal is bare.
        context.clear_cookies()
        _login(page, live_shift.base_url, RECEPTIONIST)
        refused = page.goto(f"{live_shift.base_url}/manager/card", wait_until="domcontentloaded")
        assert refused is not None and refused.status == 403
        body = page.content()
        assert REFUSAL in body
        assert all(marker not in body for marker in CARD_MARKERS), "the 403 page carries card text"
        assert f"{gap.difference:,}" not in body
        assert MANAGER.full_name not in body, "the refusal must not name the manager the card names"
        assert emergency.recorded_by.name not in body
    finally:
        context.close()
