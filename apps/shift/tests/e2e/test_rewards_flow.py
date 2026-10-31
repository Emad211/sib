"""One Playwright journey for the reward flow (SHF-5, S01 §5, ADR-016).

The whole money story of one person, on two phone-sized browsers against a real uvicorn server and a
synthetic accounting mirror:

1. The manager signs in, opens `/manager/rewards`, sets this month's rate for a sugar test and presses
   «ثبت پاداش‌های این ماه»; the accrual reads the accounting copy and writes one earning record.
2. He confirms the waiting record with the screen's own button — which posts SHF-3's `/api/v1/events`
   with `event.approved`, never a second approval endpoint.
3. The nurse signs in on her own phone, opens `/rewards` and reads her own amount for the month.
4. The manager's daily card, built for the day the confirmation landed on, carries the confirmed row.

Every value is synthetic: staff and phones from `tests/data/auth_fixtures.py`, the catalog from
`tools/seed_reference_data.py`, and one invented sugar test in the accounting mirror. Nothing reads
`_data/` or a production database (ADR-006).
"""

import threading
import time
from collections.abc import Callable, Generator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from auth_fixtures import PIN, ROLES, Seeded, seed_roster
from seed_reference_data import seed as seed_catalog

from shift.adapters.db import mirror_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.common.jalali import format_jalali
from shift.config.settings import Settings
from shift.domain.daily_card import FRIDAY
from shift.services.daily_card import Card, DailyCardService

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402
from playwright.sync_api import Error as PlaywrightError  # noqa: E402

MANAGER = ROLES["manager"]
NURSE = ROLES["nurse"]
PHONE_VIEWPORT = {"width": 390, "height": 844}
# The sugar test one nurse did in the accounting copy, and the amount this month pays for it.
NURSE_ACC = "31"
INJECTION_ID = 9001
VITALS_RATE = 50_000
RATE_FA = "۵۰٬۰۰۰"
PENDING_STATUS = "در انتظار تأیید"
CONFIRMED_STATUS = "تأییدشده"
PENDING_BOARD = "تأییدهای در انتظار"
MONTH_TABLE = "جدول این ماه"
OWN_AMOUNT = "مبلغ این ماه"
CARD_REWARDS = "section[aria-labelledby='mg-rewards-title']"


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


def _map_the_nurse(database: Database) -> None:
    """The manager's one mapping: the accounting user the nurse's work is recorded under."""
    with database.write() as connection:
        connection.exec_driver_sql("UPDATE staff SET acc_user = ? WHERE staff_id = ?", (NURSE_ACC, NURSE.staff_id))


def _record_one_vital(database: Database) -> None:
    """One sugar test in the accounting mirror, stamped in the month the clinic is in now."""
    closed_at = datetime.now(tz=TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    with database.write() as connection:
        mirror_repo.insert_vitals(
            connection,
            [
                {
                    "injection_id": INJECTION_ID,
                    "invoice_id": 1,
                    "nurse_id": int(NURSE_ACC),
                    "kind": "sugar",
                    "closed_at": closed_at,
                    "ingested_at": closed_at,
                }
            ],
        )


@pytest.fixture
def live_shift(tmp_path: Path) -> Generator[tuple[str, Database]]:
    """A real server on a seeded database, and a handle for the journey's own assertions."""
    root = tmp_path / "rewards_app"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
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
        _map_the_nurse(setup)
        _record_one_vital(setup)
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


def _open_phone(browser: Browser) -> tuple[BrowserContext, Page]:
    """A phone-sized context; the service worker is blocked so a cache never hides a live answer."""
    context = browser.new_context(viewport=PHONE_VIEWPORT, is_mobile=True, has_touch=True, service_workers="block")
    return context, context.new_page()


def _wait_until(
    page: Page,
    predicate: Callable[[], Any],
    *,
    timeout: float = 20.0,
    description: str = "condition",
) -> Any:
    """Poll from Python; a reload in flight destroys the context the next read would use, so retry."""
    deadline = time.time() + timeout
    last: Any = None
    while time.time() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except PlaywrightError as error:  # the page navigated between two reads of this poll
            last = error
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
        "() => Boolean(document.querySelector('#message.ok')) || location.pathname === '/admin/devices'",
        timeout=15000,
    )


def _store_the_card(database: Database) -> Card:
    """Store the card a run tomorrow would build, so its window covers the day of the confirmation."""
    run_day = datetime.now(tz=TEHRAN).date() + timedelta(days=1)
    if run_day.weekday() == FRIDAY:
        run_day += timedelta(days=1)
    card = DailyCardService(database).regenerate(run_day)
    assert card is not None
    return card


@pytest.mark.e2e
def test_the_manager_pays_a_reward_and_the_nurse_and_the_card_see_it(
    live_shift: tuple[str, Database], browser: Browser
) -> None:
    """Rate, accrue, confirm, the nurse's own amount, and the confirmed row on the manager's card."""
    base_url, db = live_shift
    context, page = _open_phone(browser)
    nurse_context, nurse_page = _open_phone(browser)
    try:
        _login(page, base_url, MANAGER)
        page.goto(f"{base_url}/manager/rewards", wait_until="domcontentloaded")

        # 1. This month's rate for a sugar test, saved through the screen's own form.
        assert page.input_value("#rate-vitals") == "", "the month starts with no rate at all"
        page.fill("#rate-vitals", str(VITALS_RATE))
        page.click("form.rw-rate:has(#rate-vitals) button[type=submit]")
        _wait_until(
            page,
            lambda: "نرخ کنونی" in page.content() and RATE_FA in page.content(),
            description="the saved rate",
        )

        # 2. The accrual reads the accounting copy and writes the earning record nobody has decided.
        page.click("form[action='/manager/rewards/accrue'] button[type=submit]")
        _wait_until(
            page,
            lambda: page.locator("[data-reward-approve]").count() == 1,
            description="the waiting reward",
        )
        waiting = page.inner_text(f"[aria-label='{PENDING_BOARD}']")
        assert NURSE.full_name in waiting
        assert RATE_FA in waiting

        # 3. The manager confirms it; the button posts SHF-3's decision event, and the record moves.
        page.locator("[data-reward-approve]").first.click()
        _wait_until(
            page,
            lambda: (
                page.locator("[data-reward-approve]").count() == 0
                and NURSE.full_name in page.inner_text(f"[aria-label='{MONTH_TABLE}']")
            ),
            description="the confirmed row",
        )
        table = page.inner_text(f"[aria-label='{MONTH_TABLE}']")
        assert CONFIRMED_STATUS in table
        assert RATE_FA in table
        assert PENDING_STATUS not in table

        # 4. The nurse opens her own screen on her own phone and reads her own amount.
        _login(nurse_page, base_url, NURSE)
        nurse_page.goto(f"{base_url}/rewards", wait_until="domcontentloaded")
        own = nurse_page.inner_text(f"[aria-label='{OWN_AMOUNT}']")
        assert RATE_FA in own
        assert NURSE.full_name in nurse_page.inner_text(".mg-head")

        # 5. The manager's daily card carries the confirmed reward with its earner and its amount.
        card = _store_the_card(db)
        page.goto(f"{base_url}/manager/card", wait_until="domcontentloaded")
        rewards = page.inner_text(CARD_REWARDS)
        assert NURSE.full_name in rewards
        assert CONFIRMED_STATUS in rewards
        assert f"{VITALS_RATE:,}" in rewards
        assert format_jalali(card.card_date) in page.content()
    finally:
        nurse_context.close()
        context.close()
