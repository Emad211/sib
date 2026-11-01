"""Playwright journeys for the phone handover screen: one per flow, never per case (ADR-017 item 5).

Marker: e2e

Each journey drives the real screen against a real uvicorn server on a synthetic database, a
synthetic accounting copy with no invoices (`tools/make_synthetic_acc_db.py`) and the manager's
baseline, exactly as `tools/record_handover_baseline.py` would record it. Both people of a normal
handover are on their own phone (S03 §3): the incoming person opens the flow, the outgoing person
finds it in «تحویل باز» and joins it, the checklists and a parcel in transit are recorded, and each
side signs from the phone it holds. The emergency journey stays on one device, because the outgoing
person has left (H-08).

What the three journeys promise:
1. reception: the box, the shelf, the checklists, a parcel and both signatures from two phones,
   and no reference from the projections ever reaches either page;
2. nursing: the CPR room and the trolley from two phones, the cash step is never shown (H-12), and
   a parcels step with nothing in transit advances without inventing a parcel id;
3. emergency start: the outgoing person has left, the manager confirms by phone, one signature
   closes the flow and the page names no culprit (H-08).
"""

import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from auth_fixtures import PIN, ROLES, SPARE, Seeded, seed_roster
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import events_repo, handover_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.app import create_app
from shift.common.clock import SystemClock
from shift.config.settings import Settings
from shift.domain.handover_fsm import HandoverFlow
from shift.services.audit import AuditService
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.handover import HandoverService
from shift.services.projections import ProjectionEngine

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402

# The people: the incoming person opens the flow, the custodian of the manager's baseline hands it
# over on their own phone, and a second person of the same role joins rather than opening a flow.
INCOMING_RECEPTION = ROLES["reception"]
OUTGOING_RECEPTION = ROLES["multi_role"]  # holds the reception role, so also a receptionist
INCOMING_NURSE = ROLES["nurse"]
OUTGOING_NURSE = SPARE  # the second nurse of the roster
MANAGER = ROLES["manager"]

# Every figure below is invented, and each is long enough that finding it on the page cannot be a
# coincidence: a leak of any of them fails the journey (R-02, ADR-011).
CASH_OPENING = 1_234_567
SHELF_COUNTS = {"para_500_amp": 987_654, "diclofenac_75_amp": 532_119}
CPR_COUNTS = {"saline_500_bag": 424_248, "oxygen_mask": 818_173}
TROLLEY_COUNTS = {"glove_latex_m": 1_231_233, "alcohol_pad": 7_272_722}
REFERENCE_VALUES = (CASH_OPENING, *SHELF_COUNTS.values(), *CPR_COUNTS.values(), *TROLLEY_COUNTS.values())
FRIDGE_READING = "6.5"
PARCEL_ID = "parcel-courier-01"
PARCEL_PACKAGES = 2

OFFLINE_GATE = "تحویل شیفت نیازمند اتصال به سرور درمانگاه است"
ALREADY_OPEN = "این تحویل باز شده است"
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
PHONE_VIEWPORT = {"width": 390, "height": 844}


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


def _record_baselines(database_path: Path) -> None:
    """The manager's opening count for both flows, wired as `tools/record_handover_baseline.py` is.

    It runs before the server starts, on its own connection, so the journeys never race a seed.
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
            custodian_id=OUTGOING_RECEPTION.staff_id,
            manager_id=MANAGER.staff_id,
            opening_cash=CASH_OPENING,
            items=[
                {"item_id": item_id, "location_id": "shelf_reception", "quantity": quantity}
                for item_id, quantity in SHELF_COUNTS.items()
            ],
        )
        service.record_baseline(
            flow=HandoverFlow.NURSING,
            custodian_id=OUTGOING_NURSE.staff_id,
            manager_id=MANAGER.staff_id,
            opening_cash=None,
            items=[
                *(
                    {"item_id": item_id, "location_id": "cpr_room", "quantity": quantity}
                    for item_id, quantity in CPR_COUNTS.items()
                ),
                *(
                    {"item_id": item_id, "location_id": "trolley", "quantity": quantity}
                    for item_id, quantity in TROLLEY_COUNTS.items()
                ),
            ],
        )
    finally:
        database.close()


@pytest.fixture
def live_server(tmp_path: Path) -> Generator[tuple[str, Database]]:
    """A real server on a seeded database, with a readable accounting copy and both baselines."""
    root = tmp_path / "handover_app"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".shift_secret_key",
        hash_chain=True,
        accounting_db_path=empty_accounting(root / "accounting.db"),
    )
    setup = Database(settings.database_path)
    try:
        setup.migrate()
        seed_roster(setup, pepper=settings.read_or_create_secret())
        seed_catalog(setup)
    finally:
        setup.close()
    _record_baselines(settings.database_path)

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
        yield f"http://127.0.0.1:{server.port()}", db
    finally:
        server.stop()
        db.close()


@pytest.fixture(scope="module")
def browser() -> Generator[Browser]:
    """One headless Chromium for the module; closed even when a test fails."""
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


def _wait_until(
    page: Page,
    predicate: Callable[[], Any],
    *,
    timeout: float = 15.0,
    description: str = "condition",
) -> Any:
    """Poll from Python, as the offline-outbox harness does."""
    deadline = time.time() + timeout
    last: Any = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {description}; last={last!r}")


def _login(page: Page, base_url: str, person: Seeded) -> None:
    """Log one person in on their paired phone through the real login page."""
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.evaluate("deviceId => localStorage.setItem('shift_device_id', deviceId)", person.device_id)
    page.reload(wait_until="domcontentloaded")
    page.fill("#mobile", person.mobile)
    page.fill("#pin", PIN)
    page.click("#submit")
    page.wait_for_function("location.pathname === '/'", timeout=15000)


def _open_handover(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/handover", wait_until="domcontentloaded")
    page.wait_for_selector("#handover-start:not(.hidden)", timeout=15000)


def _start(page: Page, flow: str, *, step: str) -> None:
    page.click(f"#handover-start-{flow}")
    page.wait_for_selector(f'#handover-wizard[data-step="{step}"]', timeout=15000)


def _join(page: Page, flow: str, *, step: str) -> None:
    """The second phone finds the open flow in «تحویل باز» and takes the same flow over."""
    page.wait_for_selector("#handover-open:not(.hidden)", timeout=15000)
    assert "تحویل باز" in page.inner_text("#handover-open")
    page.click(f"#handover-join-{flow}")
    page.wait_for_selector(f'#handover-wizard[data-step="{step}"]', timeout=15000)
    page.wait_for_timeout(100)


def _persian(value: int) -> str:
    return str(value).translate(PERSIAN_DIGITS)


def _count(page: Page, value: int, *, persian: bool = False) -> None:
    """Type one whole number into the open count field and submit it."""
    page.fill("#handover-count-input", _persian(value) if persian else str(value))
    page.click("#handover-count-submit")


def _count_until_advance(page: Page, quantities: dict[str, int]) -> None:
    """Answer each blind count of the current item step until it offers its advance."""
    for _ in range(12):
        field = page.locator("#handover-count-input")
        if field.count() == 0:
            page.wait_for_selector("#handover-advance", timeout=15000)
            return
        item_id = field.get_attribute("data-item-id")
        assert item_id in quantities, f"the screen asked for an item this step does not count: {item_id!r}"
        _count(page, quantities[item_id])
        page.wait_for_selector('#handover-message:has-text("درست است")', timeout=15000)
        page.wait_for_function(
            """(item) => {
                const input = document.querySelector("#handover-count-input");
                return input === null
                    ? Boolean(document.querySelector("#handover-advance"))
                    : input.dataset.itemId !== item;
            }""",
            arg=item_id,
            timeout=15000,
        )
        if page.locator("#handover-count-input").count():
            assert page.input_value("#handover-count-input") == "", "a submitted count stays on the screen"
    raise AssertionError("the item step never finished")


def _advance(page: Page, step: str) -> None:
    page.click("#handover-advance")
    page.wait_for_selector(f'#handover-wizard[data-step="{step}"]', timeout=15000)
    page.wait_for_timeout(100)


def _answer_checklists(
    page: Page,
    *,
    yes_id: str = "store_lock_ok",
    yes_name: str = "قفل انبار سالم است",
    reading_id: str = "fridge_temperature",
    reading_name: str = "دمای یخچال دارو",
    reading: str = FRIDGE_READING,
) -> None:
    """Answer the flow's own checklist; the step offers its advance only after the last answer.

    Each flow asks only its lines (A3): reception answers the store's two, nursing the trolley's two.
    """
    page.wait_for_selector("#handover-checklist-list", timeout=15000)
    assert reading_name in page.inner_text("#handover-step")
    assert page.locator("#handover-advance").count() == 0, "the checklist step leaves only when answered"

    page.click(f"#handover-checklist-{yes_id}-yes")
    page.wait_for_selector(f"#handover-checklist-{yes_id}-yes", state="detached", timeout=15000)
    page.wait_for_timeout(100)
    assert page.locator("#handover-advance").count() == 0, "one answer is not the whole checklist"

    page.fill(f"#handover-reading-{reading_id}", reading)
    page.click(f"#handover-checklist-{reading_id}-submit")
    page.wait_for_selector("#handover-advance", timeout=15000)
    page.wait_for_timeout(100)


def _hand_parcel(page: Page, parcel_id: str, packages: int) -> None:
    page.wait_for_selector("#handover-parcel-id", timeout=15000)
    page.fill("#handover-parcel-id", parcel_id)
    page.fill("#handover-parcel-packages", str(packages))
    page.click("#handover-parcel-submit")
    page.wait_for_selector('#handover-message:has-text("بسته ثبت شد")', timeout=15000)
    assert page.input_value("#handover-parcel-id") == "", "a recorded parcel leaves the field empty"
    page.wait_for_timeout(100)


def _refresh(page: Page, step: str) -> None:
    """Pull the other phone's progress into this one; nothing polls on its own."""
    page.click("#handover-refresh")
    page.wait_for_selector(f'#handover-wizard[data-step="{step}"]', timeout=15000)
    page.wait_for_timeout(100)


def _refresh_until_done(page: Page) -> None:
    """The other phone signed last: this phone's next refresh lands on the done screen."""
    page.click("#handover-refresh")
    page.wait_for_selector("#handover-done:not(.hidden)", timeout=15000)


def _sign_own(page: Page) -> None:
    """The caller's own signature, never the other person's (S03 §3, H-04)."""
    page.click("#handover-sign-own")
    page.wait_for_timeout(100)


def _flow_events(db: Database, flow: HandoverFlow) -> list[EventRecord]:
    """Every event of the newest flow of one kind, as the append-only log holds them."""
    with db.read() as connection:
        row = handover_repo.newest_flow(connection, flow)
        assert row is not None, f"no {flow.value} flow was ever opened"
        return list(events_repo.iter_handover_events(connection, row.handover_id))


def _signatures(events: list[EventRecord]) -> list[tuple[str, str]]:
    return [(event.actor_id, str(event.payload["signer"])) for event in events if event.type == "handover.signed"]


def _payloads(events: list[EventRecord], event_type: str) -> list[dict[str, Any]]:
    return [event.payload for event in events if event.type == event_type]


def _assert_no_reference_on_the_page(page: Page, values: tuple[int, ...]) -> None:
    """R-02: no reference, expected figure or delta may reach the page, a URL or the outbox."""
    html = page.content()
    text = page.inner_text("body")
    urls = " ".join(page.evaluate("() => performance.getEntriesByType('resource').map((entry) => entry.name)"))
    queued = page.evaluate("async () => (await window.ShiftOutbox.getQueuedItems()).length")
    assert queued == 0, "a handover call never enters the offline outbox (ADR-016)"
    for value in values:
        assert str(value) not in html, f"a reference reached the page markup: {value}"
        assert str(value) not in text, f"a reference reached the page text: {value}"
        assert str(value) not in urls, f"a reference reached a request URL: {value}"


@pytest.mark.e2e
def test_reception_handover_counts_the_box_the_shelf_and_both_people_sign_on_their_own_phones(
    live_server: tuple[str, Database],
    browser: Browser,
) -> None:
    """The reception flow end to end: the second phone joins, records, and signs its own side."""
    base_url, db = live_server
    incoming_context, incoming = _open_phone(browser)
    outgoing_context, outgoing = _open_phone(browser)
    try:
        _login(incoming, base_url, INCOMING_RECEPTION)
        _login(outgoing, base_url, OUTGOING_RECEPTION)
        _open_handover(incoming, base_url)

        # ADR-016: without the clinic server the screen says so and offers nothing to tap.
        incoming_context.set_offline(True)
        incoming.evaluate("window.dispatchEvent(new Event('offline'))")
        incoming.wait_for_selector("#handover-offline:not(.hidden)", timeout=15000)
        assert OFFLINE_GATE in incoming.inner_text("#handover-offline")
        assert incoming.locator("#handover-start-reception").is_hidden()
        incoming_context.set_offline(False)
        incoming.evaluate("window.dispatchEvent(new Event('online'))")
        incoming.wait_for_selector("#handover-start:not(.hidden)", timeout=15000)

        _start(incoming, "reception", step="cash")

        # The second person opens the screen after the flow exists: H-10 still refuses a start,
        # and the open list is how they join instead.
        _open_handover(outgoing, base_url)
        outgoing.click("#handover-start-reception")
        outgoing.wait_for_selector("#handover-exists:not(.hidden)", timeout=15000)
        assert ALREADY_OPEN in outgoing.inner_text("#handover-exists")
        assert "فقط مسئول" in outgoing.inner_text("#handover-exists")
        outgoing.click("#handover-exists-back")
        _join(outgoing, "reception", step="cash")

        # The box (A4): the outgoing person counts first, with Persian digits, on their own phone; the
        # incoming phone shows whose turn it is and opens its count by itself, then accepts the match.
        incoming.wait_for_selector("#handover-turn", timeout=15000)
        _count(outgoing, CASH_OPENING, persian=True)
        outgoing.wait_for_selector('#handover-message:has-text("شمارش ثبت شد")', timeout=15000)
        incoming.wait_for_selector("#handover-count-input", timeout=15000)
        _count(incoming, CASH_OPENING)
        incoming.wait_for_selector('#handover-message:has-text("درست است")', timeout=15000)
        incoming.wait_for_selector("#handover-advance", timeout=15000)
        _advance(incoming, "shelf")

        _count_until_advance(incoming, SHELF_COUNTS)
        _advance(incoming, "checklists")
        _answer_checklists(incoming)
        _advance(incoming, "parcels")
        _hand_parcel(incoming, PARCEL_ID, PARCEL_PACKAGES)
        _advance(incoming, "dual_sign")

        # Each person signs on their own phone; the outgoing phone refreshes to see the step.
        _refresh(outgoing, "dual_sign")
        assert outgoing.locator("#handover-sign-outgoing").count() == 0, "a phone signs its own side only"
        _sign_own(outgoing)
        outgoing.wait_for_selector('#handover-message:has-text("امضای شما ثبت شد")', timeout=15000)
        assert outgoing.locator("#handover-done").is_hidden()

        _sign_own(incoming)
        incoming.wait_for_selector("#handover-done:not(.hidden)", timeout=15000)
        _refresh_until_done(outgoing)

        events = _flow_events(db, HandoverFlow.RECEPTION)
        assert _signatures(events) == [
            (OUTGOING_RECEPTION.staff_id, "outgoing"),
            (INCOMING_RECEPTION.staff_id, "incoming"),
        ]
        assert [
            (payload["item_id"], payload["ok"], payload["reading"])
            for payload in _payloads(events, "handover.checklist_answered")
        ] == [("store_lock_ok", True, None), ("fridge_temperature", True, float(FRIDGE_READING))]
        assert _payloads(events, "handover.parcel_handed") == [{"parcel_id": PARCEL_ID, "packages": PARCEL_PACKAGES}]

        _assert_no_reference_on_the_page(incoming, REFERENCE_VALUES)
        _assert_no_reference_on_the_page(outgoing, REFERENCE_VALUES)
    finally:
        outgoing_context.close()
        incoming_context.close()


@pytest.mark.e2e
def test_nursing_handover_counts_the_cpr_room_and_the_trolley_with_no_cash_step(
    live_server: tuple[str, Database],
    browser: Browser,
) -> None:
    """The nursing flow end to end: its own start, its own items, never the box (H-12)."""
    base_url, db = live_server
    incoming_context, incoming = _open_phone(browser)
    outgoing_context, outgoing = _open_phone(browser)
    try:
        _login(incoming, base_url, INCOMING_NURSE)
        _login(outgoing, base_url, OUTGOING_NURSE)
        _open_handover(incoming, base_url)
        _start(incoming, "nursing", step="cpr_room")
        assert incoming.locator('#handover-wizard[data-step="cash"]').count() == 0

        _open_handover(outgoing, base_url)
        _join(outgoing, "nursing", step="cpr_room")
        assert outgoing.locator('#handover-wizard[data-step="cash"]').count() == 0

        _count_until_advance(incoming, CPR_COUNTS)
        _advance(incoming, "trolley")
        _count_until_advance(incoming, TROLLEY_COUNTS)
        _advance(incoming, "checklists")
        _answer_checklists(
            incoming,
            yes_id="trolley_seal_ok",
            yes_name="پلمب ترالی سالم است",
            reading_id="oxygen_pressure_trolley",
            reading_name="فشار کپسول اکسیژن ترالی",
            reading="180",
        )
        _advance(incoming, "parcels")

        # Nothing in transit: the step says so and advances without inventing a parcel id.
        incoming.wait_for_selector("#handover-parcel-id", timeout=15000)
        assert "بسته‌ای در راه نیست" in incoming.inner_text("#handover-step")
        _advance(incoming, "dual_sign")

        _sign_own(incoming)
        incoming.wait_for_selector('#handover-message:has-text("امضای شما ثبت شد")', timeout=15000)
        _refresh(outgoing, "dual_sign")
        _sign_own(outgoing)
        outgoing.wait_for_selector("#handover-done:not(.hidden)", timeout=15000)
        _refresh_until_done(incoming)

        events = _flow_events(db, HandoverFlow.NURSING)
        assert _signatures(events) == [
            (INCOMING_NURSE.staff_id, "incoming"),
            (OUTGOING_NURSE.staff_id, "outgoing"),
        ]
        assert _payloads(events, "handover.parcel_handed") == [], "an empty parcels step invents no parcel"

        _assert_no_reference_on_the_page(incoming, REFERENCE_VALUES)
        _assert_no_reference_on_the_page(outgoing, REFERENCE_VALUES)
    finally:
        outgoing_context.close()
        incoming_context.close()


@pytest.mark.e2e
def test_emergency_start_finishes_alone_after_the_manager_confirms_by_phone(
    live_server: tuple[str, Database],
    browser: Browser,
) -> None:
    """H-08: the outgoing person left, one signature closes the flow and no culprit is named."""
    base_url, db = live_server
    context, page = _open_phone(browser)
    try:
        _login(page, base_url, INCOMING_RECEPTION)
        _open_handover(page, base_url)
        _start(page, "reception", step="cash")

        page.click("#handover-early-leave")
        page.click("#handover-early-leave-yes")
        page.wait_for_selector("#handover-waiting:not(.hidden)", timeout=15000)
        assert "نفر خروجی رفته است" in page.inner_text("#handover-waiting")

        page.click("#handover-manager-button")
        page.wait_for_selector("#handover-manager-picker:not(.hidden)", timeout=15000)
        assert page.locator("#handover-manager option", has_text=MANAGER.full_name).count() == 1
        page.select_option("#handover-manager", label=MANAGER.full_name)
        page.click("#handover-manager-confirm")
        page.wait_for_selector('#handover-wizard[data-step="cash"]', timeout=15000)

        # Alone the incoming person counts twice; the settlement is unnamed and still settles.
        _count(page, CASH_OPENING - 5)
        page.wait_for_selector('#handover-message:has-text("دوباره بشمارید")', timeout=15000)
        _count(page, CASH_OPENING - 9)
        page.wait_for_selector('#handover-message:has-text("ثبت شد")', timeout=15000)
        page.wait_for_selector("#handover-advance", timeout=15000)
        _advance(page, "shelf")

        _count_until_advance(page, SHELF_COUNTS)
        _advance(page, "checklists")
        _answer_checklists(page)
        _advance(page, "parcels")
        page.wait_for_selector("#handover-parcel-id", timeout=15000)
        _advance(page, "dual_sign")

        assert page.locator("#handover-sign-outgoing").count() == 0, "the absent person cannot sign"
        _sign_own(page)
        page.wait_for_selector("#handover-done:not(.hidden)", timeout=15000)

        events = _flow_events(db, HandoverFlow.RECEPTION)
        assert _signatures(events) == [(INCOMING_RECEPTION.staff_id, "incoming")]
        confirmed = _payloads(events, "handover.emergency_start_confirmed")
        assert [payload["manager_id"] for payload in confirmed] == [MANAGER.staff_id]
        discrepancies = _payloads(events, "discrepancy.recorded")
        assert discrepancies, "the alone chain still records the gap it found"
        assert all(payload["culprit_id"] is None for payload in discrepancies), "H-08 keeps it unnamed"

        html = page.content()
        assert OUTGOING_RECEPTION.full_name not in html, "the page must not name the absent person"
        _assert_no_reference_on_the_page(page, REFERENCE_VALUES)
    finally:
        context.close()
