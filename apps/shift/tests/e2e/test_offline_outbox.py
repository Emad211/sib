"""Playwright E2E tests for the PWA shell, offline outbox and authenticated ingest.

Marker: e2e
"""

import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from auth_fixtures import PIN, ROLES, seed_roster
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.config.settings import Settings

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402

PERSON = ROLES["reception"]

_SUBMIT_EVENT = """
async () => {
  const ulid = window.ShiftOutbox.mintUlid();
  await window.submitShiftEvent({
    client_event_id: ulid,
    idempotency_key: ulid,
    type: "stock.waste_reported",
    payload: {
      item_id: "para_500_amp",
      location_id: "shelf_reception",
      quantity: 5,
      waste_kind: "broken",
      culprit_id: "staff-reception",
      confirmer_id: "staff-spare",
    },
    client_at: new Date().toISOString(),
    work_date: window.ShiftOutbox.tehranBusinessDate(),
  });
  return (await window.ShiftOutbox.getQueuedItems())[0];
}
"""


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
def live_server(tmp_path: Path) -> Generator[tuple[str, Database]]:
    """Start an ephemeral Uvicorn server on a seeded database and a real login flow."""
    root = tmp_path / "live_app"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=root / "shift_live.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".shift_secret_key",
        hash_chain=True,
    )
    setup = Database(settings.database_path)
    try:
        setup.migrate()
        seed_roster(setup, pepper=settings.read_or_create_secret())
        seed_catalog(setup)
    finally:
        setup.close()
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


def _open_page(browser: Browser, *, service_workers: str = "allow") -> tuple[BrowserContext, Page]:
    context = browser.new_context(service_workers=service_workers)
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


def _queue_length(page: Page) -> int:
    return int(page.evaluate("async () => (await window.ShiftOutbox.getQueuedItems()).length"))


def _wait_for_queue_length(page: Page, expected: int, *, timeout: float = 15.0) -> None:
    _wait_until(
        page,
        lambda: _queue_length(page) == expected,
        timeout=timeout,
        description=f"queue length {expected}",
    )


def _first_queued_item(page: Page) -> dict[str, Any]:
    return page.evaluate("async () => (await window.ShiftOutbox.getQueuedItems())[0]")


def _wait_for_tries(page: Page, tries: int) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        item = _first_queued_item(page)
        return item if item and item["tries"] == tries else None

    return _wait_until(page, check, timeout=5.0, description=f"item with tries={tries}")


def _wait_for_attempts(page: Page, attempts: dict[str, int], minimum: int) -> None:
    _wait_until(
        page,
        lambda: attempts["count"] >= minimum,
        timeout=5.0,
        description=f"{minimum} replay attempts",
    )


def _wait_for_shell(page: Page) -> None:
    page.wait_for_selector("#shift-overview-title", timeout=15000)


def _wait_for_service_worker(page: Page) -> None:
    page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=15000)
    page.evaluate(
        "async () => { const reg = await navigator.serviceWorker.ready; if (!reg.active) throw new Error('inactive'); }"
    )


def _login(page: Page, base_url: str) -> None:
    """Log the seeded reception phone in through the real login page."""
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.evaluate("deviceId => localStorage.setItem('shift_device_id', deviceId)", PERSON.device_id)
    # The page reads the device id while loading, so reload after storing it.
    page.reload(wait_until="domcontentloaded")
    page.fill("#mobile", PERSON.mobile)
    page.fill("#pin", PIN)
    page.click("#submit")
    page.wait_for_function("location.pathname === '/'", timeout=15000)
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _wait_for_shell(page)


@pytest.mark.e2e
def test_offline_form_submit_then_replay_records_one_principal_owned_event(
    live_server: tuple[str, Database],
    browser: Browser,
) -> None:
    """The real form queues offline, then replay records exactly one principal-owned event."""
    base_url, db = live_server
    context, page = _open_page(browser)
    try:
        _login(page, base_url)
        _wait_for_service_worker(page)

        context.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")
        page.wait_for_selector("#network-banner:not(.hidden)")

        # S12: the service worker must not fabricate a durable "queued" response.
        offline_post = page.evaluate(
            """async () => {
                try {
                    const response = await fetch("/api/v1/events", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: "{}",
                    });
                    return { status: response.status };
                } catch (error) {
                    return { error: String(error) };
                }
            }"""
        )
        assert "error" in offline_post

        queued = page.evaluate(_SUBMIT_EVENT)
        assert queued is not None

        _wait_for_queue_length(page, 1)
        assert "actor_id" not in queued["body"]
        assert "device_id" not in queued["body"]
        assert "shift_id" not in queued["body"]
        page.wait_for_selector("#submission-feedback:has-text('در صف ارسال')")

        with db.read() as connection:
            assert events_repo.count_events(connection) == 0

        context.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.evaluate("async () => { await window.ShiftOutbox.replayOutbox(); }")
        _wait_for_queue_length(page, 0)
        page.wait_for_selector("#submission-feedback:has-text('همگام')", timeout=5000)

        event_id = queued["body"]["client_event_id"]
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1
            event = events_repo.find_by_event_id(connection, event_id)
            assert event is not None
            assert event.actor_id == PERSON.staff_id
            assert event.device_id == PERSON.device_id
            assert event.shift_id is not None
            assert event.work_date == queued["body"]["work_date"]
            assert event.payload["quantity"] == 5
            assert projections_repo.stock_rows(connection) == [
                {"item_id": "para_500_amp", "location_id": "shelf_reception", "qty": -5}
            ]

        # A direct replay with forged identity fields still dedupes and keeps the principal.
        replay = page.evaluate(
            """async ({ ulid, clientAt }) => {
                const response = await fetch("/api/v1/events", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        client_event_id: ulid,
                        idempotency_key: ulid,
                        type: "stock.waste_reported",
                        payload: {
                            item_id: "para_500_amp",
                            location_id: "shelf_reception",
                            quantity: 5,
                            waste_kind: "broken",
                            culprit_id: "staff-reception",
                            confirmer_id: "staff-spare",
                        },
                        actor_id: "staff_forged",
                        device_id: "device_forged",
                        shift_id: "shift_forged",
                        client_at: clientAt,
                    }),
                });
                return { status: response.status, body: await response.json() };
            }""",
            {"ulid": event_id, "clientAt": queued["body"]["client_at"]},
        )
        assert replay["status"] == 200
        assert replay["body"]["created"] is False
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1
            event = events_repo.find_by_event_id(connection, event_id)
            assert event is not None
            assert event.actor_id == PERSON.staff_id
            assert event.device_id == PERSON.device_id
    finally:
        context.close()


@pytest.mark.e2e
def test_cold_offline_launch_serves_the_shell_for_root_and_start_url(
    live_server: tuple[str, Database],
    browser: Browser,
) -> None:
    """The manifest start_url is cached, so an installed PWA launches the shell offline."""
    base_url, _db = live_server
    context, page = _open_page(browser)
    try:
        page.goto(f"{base_url}/?src=pwa", wait_until="domcontentloaded")
        _wait_for_shell(page)
        _wait_for_service_worker(page)
        _wait_until(
            page,
            lambda: page.evaluate("async (url) => !!(await caches.match(url))", "/?src=pwa"),
            description="cached start_url",
        )

        context.set_offline(True)

        page.goto(f"{base_url}/?src=pwa", wait_until="domcontentloaded")
        _wait_for_shell(page)
        assert page.locator("#shift-overview-title").count() == 1

        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        _wait_for_shell(page)
        assert page.locator("#shift-overview-title").count() == 1
        assert page.locator("text=ارتباط با سرور درمانگاه قطع است").count() == 0
    finally:
        context.close()


@pytest.mark.e2e
def test_online_failed_post_never_claims_success_and_retries(
    live_server: tuple[str, Database],
    browser: Browser,
) -> None:
    """A network failure shows queued state (never 'sent') and the retry timer fires."""
    base_url, db = live_server
    context, page = _open_page(browser, service_workers="block")
    attempts = {"count": 0}

    def handler(route: Any) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            route.abort()
        else:
            route.continue_()

    page.route("**/api/v1/events", handler)
    try:
        _login(page, base_url)

        queued = page.evaluate(_SUBMIT_EVENT)
        assert queued is not None
        page.wait_for_selector("#submission-feedback:has-text('در صف ارسال')", timeout=5000)
        assert page.locator("#submission-feedback:has-text('همگام')").count() == 0

        _wait_for_attempts(page, attempts, 1)
        item = _wait_for_tries(page, 1)
        assert item["status"] == "queued"
        assert item["next_try_at"] > 0

        # No reload and no online event: the page-level backoff timer must fire.
        _wait_for_queue_length(page, 0)
        page.wait_for_selector("#submission-feedback:has-text('همگام')", timeout=5000)
        assert attempts["count"] >= 2
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1
    finally:
        page.unroute("**/api/v1/events", handler)
        context.close()


@pytest.mark.e2e
def test_invalid_200_response_keeps_the_item_and_retries(
    live_server: tuple[str, Database],
    browser: Browser,
) -> None:
    """A captive-portal 200 HTML must never delete the queued event (S5)."""
    base_url, db = live_server
    context, page = _open_page(browser, service_workers="block")
    attempts = {"count": 0}

    def handler(route: Any) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            route.fulfill(status=200, content_type="text/html", body="<html>login page</html>")
        else:
            route.continue_()

    page.route("**/api/v1/events", handler)
    try:
        _login(page, base_url)
        page.evaluate(_SUBMIT_EVENT)

        _wait_for_attempts(page, attempts, 1)
        item = _wait_for_tries(page, 1)
        assert item["status"] == "queued"
        assert page.locator("#submission-feedback:has-text('همگام')").count() == 0

        _wait_for_queue_length(page, 0)
        assert attempts["count"] >= 2
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1
    finally:
        page.unroute("**/api/v1/events", handler)
        context.close()
