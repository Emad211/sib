"""Playwright journey for the flow's own first count (owner decision 9, ADR-022 item 5).

Marker: e2e

One flow, one journey, never one per case (ADR-017 item 5). A receptionist who is still **off shift**
logs in on their own phone, finds «شمارش اولیه» on the home screen, counts the box and the reception
shelf one screen at a time, and records it under their own name; the tile then disappears and the
server holds them as the flow's custodian. No expected quantity is ever on the screen: the numbers
below are invented and only the person's own typing puts them there.
"""

import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from auth_fixtures import PIN, ROLES, Seeded, seed_roster
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import handover_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.config.settings import Settings
from shift.domain.handover_fsm import HandoverFlow

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402

RECEPTION = ROLES["reception"]
CASH_OPENING = 1_234_567
SHELF_COUNTS = {"para_500_amp": 987_654, "diclofenac_75_amp": 21_212}
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
PHONE_VIEWPORT = {"width": 390, "height": 844}
BASELINE_HREF = 'a[href="/handover/baseline"]'


class _UvicornServer(threading.Thread):
    """Uvicorn on an ephemeral port; the OS picks the port, so there is no TOCTOU."""

    def __init__(self, app, host: str) -> None:
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
def live_server(tmp_path: Path) -> Generator[tuple[str, Database]]:
    """A real server, the roster **off shift** and no baseline yet: the first count is still to do."""
    root = tmp_path / "baseline_app"
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
        seed_roster(setup, pepper=settings.read_or_create_secret(), on_shift=False)
        seed_catalog(setup)
    finally:
        setup.close()

    server = _UvicornServer(create_app(settings), host="127.0.0.1")
    server.start()
    deadline = time.time() + 15.0
    while time.time() < deadline and not server.server.started:
        if not server.is_alive():
            raise RuntimeError("uvicorn thread died before startup")
        time.sleep(0.02)
    if not server.server.started:
        server.stop()
        raise RuntimeError("uvicorn server failed to start within 15 seconds")

    database = Database(settings.database_path)
    try:
        yield f"http://127.0.0.1:{server.port()}", database
    finally:
        server.stop()
        database.close()


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


def _login(page: Page, base_url: str, person: Seeded) -> None:
    """Log one person in on their paired phone through the real login page."""
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.evaluate("deviceId => localStorage.setItem('shift_device_id', deviceId)", person.device_id)
    page.reload(wait_until="domcontentloaded")
    page.fill("#mobile", person.mobile)
    page.fill("#pin", PIN)
    page.click("#submit")
    page.wait_for_function("location.pathname === '/'", timeout=15000)


def _persian(value: int) -> str:
    return str(value).translate(PERSIAN_DIGITS)


def test_a_receptionist_counts_the_box_and_shelf_on_their_own_phone(
    live_server: tuple[str, Database], browser: Browser
) -> None:
    """Owner decision 9: the person counts on their phone, becomes custodian, and the job disappears."""
    base_url, database = live_server
    context, page = _open_phone(browser)
    try:
        _login(page, base_url, RECEPTION)
        page.click(BASELINE_HREF)
        page.wait_for_selector("#baseline-wizard:not(.hidden)", timeout=15000)

        page.wait_for_selector("#baseline-cash-input", timeout=15000)
        page.fill("#baseline-cash-input", _persian(CASH_OPENING))
        page.click("#baseline-cash-next")

        page.wait_for_selector("#baseline-items-next", timeout=15000)
        assert "قفسهٔ پذیرش" in page.inner_text("#baseline-step")
        for item_id, quantity in SHELF_COUNTS.items():
            page.fill(f'input[data-item-id="{item_id}"]', _persian(quantity))
        page.click("#baseline-items-next")

        page.wait_for_selector("#baseline-submit", timeout=15000)
        page.click("#baseline-submit")
        page.wait_for_selector("#baseline-done:not(.hidden)", timeout=15000)

        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        assert page.locator(BASELINE_HREF).count() == 0, "the first count is gone from the home screen"
    finally:
        context.close()

    with database.read() as connection:
        assert handover_repo.has_baseline(connection, HandoverFlow.RECEPTION)
        assert handover_repo.custodian(connection, HandoverFlow.RECEPTION) == RECEPTION.staff_id
