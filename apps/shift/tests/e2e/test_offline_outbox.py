"""Playwright E2E test for offline event submission and outbox replay.

Marker: e2e
Flow:
1. Boot application on localhost.
2. Open PWA home shell in Chromium.
3. Simulate offline network context.
4. Submit an event while offline and await enqueue.
5. Verify writes never claim success offline (shows "در صف ارسال" queued state).
6. Restore network connection (online).
7. Replay outbox and assert exactly one event is persisted in the database.
"""

import socket
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.ids import new_ulid
from shift.config.settings import Settings

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _UvicornServer(threading.Thread):
    def __init__(self, app: Any, host: str, port: int) -> None:
        super().__init__(daemon=True)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(config)

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


@pytest.fixture
def live_server(tmp_path: Path) -> Generator[tuple[str, Database]]:
    """Start an ephemeral Uvicorn server running the application."""
    root = tmp_path / "live_app"
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "shift_live.db"

    settings = Settings(
        database_path=db_path,
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".shift_secret_key",
        hash_chain=True,
    )
    application = create_app(settings)
    port = _get_free_port()
    server = _UvicornServer(application, host="127.0.0.1", port=port)
    server.start()

    # Wait for server readiness
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10.0
    ready = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.05)

    if not ready:
        server.stop()
        raise RuntimeError("Uvicorn server failed to bind within 10 seconds")

    db = Database(db_path)
    try:
        yield base_url, db
    finally:
        server.stop()
        db.close()


@pytest.mark.filterwarnings("ignore:Unknown pytest.mark.e2e.*:pytest.PytestUnknownMarkWarning")
@pytest.mark.e2e
def test_offline_submit_then_replay_records_exactly_one_event(
    live_server: tuple[str, Database],
) -> None:
    """Offline submission queues in outbox; online replay records exactly one event."""
    base_url, db = live_server

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=True)
        context: BrowserContext = browser.new_context()
        page: Page = context.new_page()

        # Step 1: Navigate to PWA shell while online
        page.goto(base_url, wait_until="networkidle")
        page.wait_for_selector("#shift-overview-title")

        # Step 2: Go offline
        context.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")
        page.wait_for_selector("#network-banner:not(.hidden)")

        # Step 3: Submit an event while offline using a valid ULID
        fixed_time = datetime.now(UTC).isoformat()
        test_ulid = new_ulid()
        page.evaluate(
            """async ({ clientAt, ulid }) => {
                await window.submitShiftEvent({
                    client_event_id: ulid,
                    idempotency_key: ulid,
                    type: 'stock.waste_reported',
                    payload: { item_id: 'bandage_roll', location_id: 'emergency_cabinet', quantity: 5 },
                    actor_id: 'staff_offline_1',
                    device_id: 'device_phone_1',
                    work_date: '2026-10-28',
                    client_at: clientAt,
                    shift_id: null
                });
            }""",
            {"clientAt": fixed_time, "ulid": test_ulid},
        )

        # Wait until item is confirmed inside IndexedDB outbox
        page.wait_for_function("async () => (await window.ShiftOutbox.getQueuedItems()).length === 1", timeout=5000)

        # Step 4: Writes never claim success offline: assert queued badge
        page.wait_for_selector("#submission-feedback:has-text('در صف ارسال')")
        page.wait_for_selector("#outbox-card:not(.hidden)")

        # Confirm nothing reached the database yet while offline
        with db.read() as connection:
            assert events_repo.count_events(connection) == 0

        # Step 5: Restore connectivity (online)
        context.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")

        # Step 6: Trigger outbox replay and await completion
        page.evaluate("async () => { await window.ShiftOutbox.replayOutbox(); }")
        page.wait_for_function("async () => (await window.ShiftOutbox.getQueuedItems()).length === 0", timeout=10000)

        # Step 7: Verify database contains exactly one recorded event
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1
            event = events_repo.find_by_idempotency_key(connection, test_ulid)
            assert event is not None
            assert event.type == "stock.waste_reported"
            assert event.actor_id == "staff_offline_1"
            assert event.payload["quantity"] == 5

        # Step 8: Replay the batch again to assert replay idempotency convergence
        page.evaluate(
            """({ clientAt, ulid }) => {
                return fetch('/api/v1/events', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_event_id: ulid,
                        idempotency_key: ulid,
                        type: 'stock.waste_reported',
                        payload: { item_id: 'bandage_roll', location_id: 'emergency_cabinet', quantity: 5 },
                        actor_id: 'staff_offline_1',
                        device_id: 'device_phone_1',
                        work_date: '2026-10-28',
                        client_at: clientAt,
                        shift_id: null
                    })
                }).then(r => r.json());
            }""",
            {"clientAt": fixed_time, "ulid": test_ulid},
        )

        # Event count must still remain exactly 1!
        with db.read() as connection:
            assert events_repo.count_events(connection) == 1

        context.close()
        browser.close()
