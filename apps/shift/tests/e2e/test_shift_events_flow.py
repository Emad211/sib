"""One Playwright journey per user flow of the staff phone (SHF-3, S01 §3, ADR-016).

Two flows, two journeys, on a phone-sized browser against a real uvicorn server and the synthetic
reference catalog:

1. A receptionist opens `/events`, takes «ضایعات» and answers the wizard one question at a time.
   The phone goes offline before the last tap, so the event can only reach the log through the
   IndexedDB outbox; once the network is back the queue drains and the log owns exactly one
   principal-owned event.
2. A card-to-card report waits for the accountant, who opens `/events`, reads the pending card built
   from the log (label, author, Tehran time, amount) and approves it; the decision itself is an
   `event.approved` row that references the report.

`tests/e2e/test_offline_outbox.py` owns the replay edge cases; this module only proves the screens
carry the flows. Every value is synthetic: staff from `tests/data/auth_fixtures.py`, the catalog from
`tools/seed_reference_data.py`. Nothing reads `_data/` or a production database (ADR-006).
"""

import base64
import threading
import time
from collections.abc import Callable, Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from auth_fixtures import PIN, ROLES, Seeded, seed_roster
from photo_fixtures import RECEIPT_JPEG, RECEIPT_SHA
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.common.ids import new_ulid
from shift.config.settings import Settings
from shift.services.photos import PhotoStore

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402

RECEPTIONIST = ROLES["reception"]
ACCOUNTANT = ROLES["accountant"]
PHONE_VIEWPORT = {"width": 390, "height": 844}

WASTE = "stock.waste_reported"
CARD_TRANSFER = "card.transfer_reported"
WASTE_LABEL = "ضایعات"
CARD_TRANSFER_LABEL = "کارت‌به‌کارت"
TRANSFER_AMOUNT = 250_000
ADMISSION_NO = "1001"
APPROVAL_NOTE = "سینتتیک — تأیید شد"
SYNTHETIC_TEXT = "1001"


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


@pytest.fixture
def live_shift(tmp_path: Path) -> Generator[tuple[str, Database]]:
    """A real server on a seeded database, and a read handle for the journey's assertions."""
    root = tmp_path / "shift_events_app"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        environment="development",  # httpx helper signs in over plain HTTP
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".shift_secret_key",
    )
    setup = Database(settings.database_path)
    try:
        setup.migrate()
        seed_roster(setup, pepper=settings.read_or_create_secret())
        seed_catalog(setup)
    finally:
        setup.close()
    PhotoStore(settings.resolved_photos_dir).save(RECEIPT_JPEG)  # the receipt the transfer names

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
    context = browser.new_context(viewport=PHONE_VIEWPORT, is_mobile=True, has_touch=True)
    return context, context.new_page()


def _wait_until(
    page: Page,
    predicate: Callable[[], Any],
    *,
    timeout: float = 15.0,
    description: str = "condition",
) -> Any:
    """Poll from Python; Playwright's wait_for_function does not await async callbacks."""
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
    page.wait_for_function(
        "() => location.pathname === '/'",
        timeout=15000,
    )


def _receipt_for(base_url: str) -> str:
    """Record the pending card-to-card report through the API, as the receptionist's phone does."""
    with httpx.Client(base_url=base_url, timeout=15.0) as api:
        api.get("/login")
        csrf = api.cookies.get("shift_csrf", "")
        signed_in = api.post(
            "/api/v1/auth/login",
            json={"mobile": RECEPTIONIST.mobile, "pin": PIN, "device_id": RECEPTIONIST.device_id},
            headers={"X-CSRFToken": csrf},
        )
        assert signed_in.status_code == 200, signed_in.text
        # The phone mints the identity offline; here the test does it for the same endpoint.
        client_event_id = new_ulid()
        recorded = api.post(
            "/api/v1/events",
            json={
                "type": CARD_TRANSFER,
                "payload": {"amount": TRANSFER_AMOUNT, "admission_no": ADMISSION_NO, "photo_sha256": RECEIPT_SHA},
                "client_event_id": client_event_id,
                "idempotency_key": client_event_id,
                # The phone stamps its own instant; a fixed date would be an old queue by now (F5).
                "client_at": datetime.now(tz=TEHRAN).isoformat(),
            },
            headers={"X-CSRFToken": signed_in.json()["csrf_token"]},
        )
        assert recorded.status_code == 200, recorded.text
        return str(recorded.json()["event_id"])


def _queue_length(page: Page) -> int:
    return int(page.evaluate("async () => (await window.ShiftOutbox.getQueuedItems()).length"))


def _first_queued(page: Page) -> dict[str, Any]:
    return page.evaluate("async () => (await window.ShiftOutbox.getQueuedItems())[0]")


def _field_values(page: Page, selector: str) -> list[str]:
    return page.eval_on_selector_all(f"{selector} option", "els => els.map(e => e.value)")


def _fill_step(page: Page, field: dict[str, Any], used_staff: list[str], actor_id: str) -> None:
    """Answer one wizard question the way a receptionist would: one value, no server knowledge."""
    selector = f'#shift-step-field [data-field="{field["key"]}"]'
    if field["kind"] == "multi":
        boxes = page.locator(f'{selector} input[type="checkbox"]')
        assert boxes.count() > 0, field["key"]
        boxes.first.check()
        return
    if field["kind"] == "boolean":
        page.check(selector)
        return
    if field["kind"] in {"choice", "item", "location", "staff"}:
        current = page.input_value(selector)
        if current:
            return
        values = [value for value in _field_values(page, selector) if value]
        if field["kind"] == "staff":
            # The clinic refuses the recorder and the culprit as the confirming second person.
            values = [value for value in values if value not in used_staff and value != actor_id]
            assert values, f"no second person left for {field['key']}"
            used_staff.append(values[0])
        assert values, f"no choice for {field['key']}"
        page.select_option(selector, values[0])
        return
    if field["kind"] == "date":
        page.fill(selector, "2026-10-28")
        return
    if field["kind"] == "photo":
        # A5: the photo is required for these kinds; the phone's own canvas draws a small JPEG to attach.
        data_url = page.evaluate(
            "() => { const c = document.createElement('canvas'); c.width = 40; c.height = 30;"
            " const g = c.getContext('2d'); g.fillStyle = '#4a7'; g.fillRect(0, 0, 40, 30);"
            " return c.toDataURL('image/jpeg', 0.8); }"
        )
        jpeg = base64.b64decode(data_url.split(",", 1)[1])
        page.set_input_files(
            '#shift-step-field input[type="file"]',
            files=[{"name": "receipt.jpg", "mimeType": "image/jpeg", "buffer": jpeg}],
        )
        page.wait_for_selector('#shift-step-field .se-photo-status:has-text("عکس آماده است")', timeout=15000)
        return
    if field["kind"] in {"amount", "quantity"}:
        page.fill(selector, "2")
        return
    page.fill(selector, SYNTHETIC_TEXT)


@pytest.mark.e2e
def test_a_receptionist_queues_waste_offline_and_the_log_owns_it_after_replay(
    live_shift: tuple[str, Database], browser: Browser
) -> None:
    """The wizard walks one question at a time; the final tap happens with no network (ADR-016)."""
    base_url, db = live_shift
    context, page = _open_phone(browser)
    try:
        _login(page, base_url, RECEPTIONIST)
        page.goto(f"{base_url}/events", wait_until="domcontentloaded")
        page.wait_for_selector(f'[data-kind="{WASTE}"]', timeout=15000)
        assert WASTE_LABEL in page.inner_text(f'[data-kind="{WASTE}"]')
        catalog = page.evaluate("() => JSON.parse(document.getElementById('shift-event-catalog').textContent)")
        kind = next(entry for entry in catalog["kinds"] if entry["type"] == WASTE)

        page.click(f'[data-kind="{WASTE}"]')
        page.wait_for_selector("#shift-event-step:not(.hidden)")
        assert kind["fields"][0]["label"] in page.inner_text("#shift-step-question")
        used_staff: list[str] = []
        for field in kind["fields"]:
            _fill_step(page, field, used_staff, RECEPTIONIST.staff_id)
            page.click("#shift-step-next")
        page.wait_for_selector("#shift-event-review:not(.hidden)")
        assert WASTE_LABEL in page.inner_text("#shift-event-review")

        # The network is gone before the last tap: only the local outbox can keep the event.
        context.set_offline(True)
        page.click("#shift-submit")
        page.wait_for_selector("#shift-event-groups:not(.hidden)", timeout=15000)
        _wait_until(page, lambda: _queue_length(page) == 1, description="one queued event")
        queued = _first_queued(page)
        body = queued["body"]
        assert body["type"] == WASTE
        assert queued["url"] == "/api/v1/events"
        assert "actor_id" not in body and "device_id" not in body and "shift_id" not in body
        assert body["idempotency_key"] == body["client_event_id"]
        assert isinstance(body["payload"]["quantity"], int), "a quantity reaches the payload as a number"
        assert body["payload"]["quantity"] == 2
        with db.read() as connection:
            assert events_repo.count_events(connection) == 0, "nothing reached the server while offline"

        context.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.evaluate("async () => { await window.ShiftOutbox.replayOutbox(); }")
        _wait_until(page, lambda: _queue_length(page) == 0, description="drained outbox")

        event_id = body["client_event_id"]
        with db.read() as connection:
            event = events_repo.find_by_event_id(connection, event_id)
        assert event is not None, "the replayed event never reached the log"
        assert (event.type, event.actor_id) == (WASTE, RECEPTIONIST.staff_id)
        assert event.payload["quantity"] == 2
        assert event.payload["culprit_id"] != event.payload["confirmer_id"], "the confirmer is a second person"
        assert event.work_date == body["work_date"]
    finally:
        context.close()


@pytest.mark.e2e
def test_the_accountant_approves_a_pending_card_transfer(live_shift: tuple[str, Database], browser: Browser) -> None:
    """The approval card is read from the pending endpoint and the decision is its own event."""
    base_url, db = live_shift
    target_id = _receipt_for(base_url)
    context, page = _open_phone(browser)
    try:
        _login(page, base_url, ACCOUNTANT)
        page.goto(f"{base_url}/events", wait_until="domcontentloaded")
        card = page.locator(f'#shift-pending-list [data-event-id="{target_id}"]')
        card.wait_for(state="visible", timeout=15000)
        text = card.inner_text()
        assert CARD_TRANSFER_LABEL in text
        assert RECEPTIONIST.full_name in text
        amount = page.evaluate("(value) => Number(value).toLocaleString('fa-IR')", TRANSFER_AMOUNT)
        assert amount in text, "the pending card shows the amount as a Persian figure"
        assert "تأیید" in text and "رد" in text
        # A decision needs the card's reference, so it is never a blank wizard button (R-10).
        assert page.locator('[data-kind="event.approved"]').count() == 0
        assert page.locator('[data-kind="event.rejected"]').count() == 0

        page.on("dialog", lambda dialog: dialog.accept(APPROVAL_NOTE))
        card.locator('[data-decision="event.approved"]').click()
        _wait_until(
            page,
            lambda: _approval_of(db, target_id) is not None,
            description="the approval event in the log",
        )
        page.wait_for_selector(f'#shift-pending-list [data-event-id="{target_id}"]', state="detached", timeout=15000)
        approved = _approval_of(db, target_id)
        assert approved is not None
        assert (approved.type, approved.actor_id) == ("event.approved", ACCOUNTANT.staff_id)
        assert approved.ref_event_id == target_id
        assert approved.payload.get("note") == APPROVAL_NOTE
    finally:
        context.close()


def _approval_of(db: Database, target_id: str) -> Any:
    with db.read() as connection:
        return events_repo.latest_ref(connection, target_id, ("event.approved",))
