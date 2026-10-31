"""One Playwright journey per purchase flow: the chain and the warehouse (SHF-4, S01 §4, R-10).

Marker: e2e

S01 §4 gives the clinic one chain (storekeeper drafts, manager decides, purchaser places, reception
takes the parcel in, the accountant types the invoice and pays) and one warehouse job (the storekeeper
charges a shelf from the main store and counts the store blind once a week). This module walks each of
them once, on a phone-sized browser, against a real uvicorn server on a synthetic database:

* **the chain** — the storekeeper drafts on `/purchase`, the manager sees the order on the daily card
  and approves it there, the purchaser hands it to a vendor, reception takes it in *with* a problem flag
  (so the payment is locked), the accountant sees the lock, types the invoice, is refused the payment
  once while the flag waits, the manager approves the flag, and the payment lands: the screen says
  «پرداخت‌شده» (P-01…P-08, M-19).
* **the warehouse** — the storekeeper charges the reception shelf from the main store and the shelf
  projection rises by the charged amount while the screen still prints no shelf figure (the count is
  blind, P-10/P-11), the manager's card reports the weekly count as not done, the storekeeper counts,
  and the card reports it done (M-20).

The card's five purchase sections are piece B1's (`templates/manager_card.html`); their titles here are
the contract the foreman fixed: «سفارش‌های خرید در انتظار تصمیم», «بار مشکل‌دار (پرداخت قفل)», «بار در
راه انبار», «شمارش هفتگی انبار», «اقلام زیر حداقل». Persian text is compared with its zero-width
joiners removed (`_flat`), so a label matches however it is typed; nothing else about the wording is
relaxed.

Everything is synthetic: accounting from `tools/make_synthetic_acc_db.py` (emptied), staff from
`tests/data/auth_fixtures.py`, catalog from `tests/data/synthetic_catalog.py`. Nothing reads `_data/` or a
production database (ADR-006). The weekly-count due day is seeded for the test so today is the first day
of the window, whichever weekday the suite runs on.
"""

import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from auth_fixtures import PIN, ROLES, Seeded, seed_roster
from synthetic_accounting import empty_accounting
from synthetic_catalog import ITEMS
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import projections_repo, settings_repo
from shift.adapters.db.core import Database
from shift.app import create_app
from shift.common.clock import SystemClock, to_tehran, utc_iso
from shift.config.settings import Settings
from shift.domain.daily_card import FRIDAY, window_for
from shift.domain.policy import SettingKey, ShiftKey
from shift.services.daily_card import Card, DailyCardService
from shift.services.purchase import PAYMENT_LOCKED

playwright = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402

# The people of the chain: one phone each, because the clinic hands the parcel on between them.
STOREKEEPER = ROLES["storekeeper"]
MANAGER = ROLES["manager"]
PURCHASER = ROLES["purchaser"]
RECEPTIONIST = ROLES["reception"]
ACCOUNTANT = ROLES["accountant"]

# The synthetic catalog: paracetamol on the reception shelf and the main store (S03 §1).
ITEM = "para_500_amp"
ITEM_NAME = next(item.name for item in ITEMS if item.item_id == ITEM)
SHELF = "shelf_reception"
STORE = "main_store"
QUANTITY = 4
# A synthetic JPEG header: F1 makes the invoice photo mandatory at the door (decision 6).
INVOICE_PHOTO = b"\xff\xd8\xff\xe0" + b"0" * 64
UNIT_PRICE = 25_000
INVOICE_REFERENCE = "فاکتور ۱۲"
VENDOR = "داروخانهٔ آزمایشی"
ORDER_REFERENCE = "PO-1"
PACKAGES = 2
PROBLEM = "short"
CHARGE = 5
COUNTED = 7
RECEIPT = "R-9001"
# The due day is seeded so that today opens the window: Saturday..Monday is the clinic's own default.
WINDOW_DAYS = 3

PHONE_VIEWPORT = {"width": 390, "height": 844}
# What this screen says when an action landed; the server's refusals are its own Persian sentences.
DRAFTED = "سفارش ثبت شد."
DECIDED = "تصمیم ثبت شد."
PLACED = "سفارش به فروشنده داده شد."
RECEIVED = "دریافت بار ثبت شد."
DIGITIZED = "فاکتور ثبت شد."
PAID = "پرداخت ثبت شد."
CHARGED = "شارژ قفسه ثبت شد."
OFFERED = "شارژ قفسه برای تأیید فرستاده شد."
CONFIRMED = "تأیید ثبت شد."
COUNTED_STATUS = "شمارش هفتگی ثبت شد."
LOCK = "پرداخت تا تصمیم مدیر قفل است"
PAID_LABEL = "پرداخت‌شده"
# The card's purchase sections (piece B1; the contract the foreman fixed).
CARD_ORDERS = "سفارش‌های خرید در انتظار تصمیم"
CARD_FLAGGED = "بار مشکل‌دار (پرداخت قفل)"
CARD_WEEKLY = "شمارش هفتگی انبار"
# The shift-event kind a flagged parcel is, as the phone's approval list labels it (piece A's registry).
FLAG_LABEL = "بار مشکل‌دار"
# Text only this screen carries: no price ever reaches a person without `purchase.payment.record`.
PRICE_WORD = "تومان"


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


def _seed_the_count_window(database_path: Path) -> None:
    """Make today the first day of the weekly count window, whatever weekday the suite runs on.

    The clinic counts Saturday..Monday and the card reads `weekly_count_weekday` from the versioned
    settings. The seeded default is Monday; a run on any other day would fall outside the window, so the
    test writes one version at the install-time shift — the same row a manager's change would leave, two
    days later than the default — and the journey then counts inside its own window (P-11, R-09).
    """
    today = SystemClock().now_utc()
    due_weekday = (to_tehran(today).date().weekday() + WINDOW_DAYS - 1) % 7
    database = Database(database_path)
    try:
        with database.write() as connection:
            settings_repo.insert_version(
                connection,
                setting_key=SettingKey.WEEKLY_COUNT_WEEKDAY.value,
                value=str(due_weekday),
                version=settings_repo.next_version(connection, SettingKey.WEEKLY_COUNT_WEEKDAY.value),
                effective_from_shift=ShiftKey.baseline().text,
                changed_at=utc_iso(today),
                changed_by="tool:test_purchase_flow",
            )
    finally:
        database.close()


@pytest.fixture
def live_shift(tmp_path: Path) -> Generator[LiveShift]:
    """A real server on a seeded synthetic database, with the count window open today."""
    root = tmp_path / "purchase_flow_app"
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
    _seed_the_count_window(settings.database_path)

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
    """One headless Chromium for the module; closed even when a journey fails."""
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

    A manager also holds `device.enroll`, and the login screen sends that session on to the device admin
    page instead of showing its success line, so both landings count as a finished login.
    """
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


def _flat(text: str) -> str:
    """The same Persian text without its zero-width joiners, so a label matches however it is typed."""
    return text.replace("\u200c", "").replace("\u200d", "").replace("\u200e", "").replace("\u200f", "")


def _fa(number: int) -> str:
    """A figure as the screen prints it: Persian digits."""
    return str(number).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _wait_status(page: Page, text: str) -> None:
    """Wait for the screen's one status line to say what the action did."""
    page.wait_for_function(
        "expected => { var node = document.getElementById('purchase-status');"
        " return Boolean(node) && node.textContent === expected; }",
        arg=text,
        timeout=20000,
    )


def _state_from_page(page: Page) -> dict:
    """The state this page's own session sees, read the way its script reads it."""
    return page.evaluate(
        "() => fetch('/api/v1/purchase/state', {credentials: 'same-origin'}).then(response => response.json())"
    )


def _post_from_page(page: Page, action: str, data: dict) -> dict:
    """Post one action from the page's own session, exactly as a button of the page would."""
    return page.evaluate(
        """payload => fetch('/api/v1/purchase/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        }).then(async response => ({status: response.status, body: await response.json()}))""",
        {"action": action, "data": data},
    )


def _stock(database: Database, item_id: str) -> tuple[int, int]:
    """The shelf and the store as the projection holds them: the screen itself prints no figure."""
    with database.read() as connection:
        rows = projections_repo.stock_rows(connection)
    quantities = {(row["item_id"], row["location_id"]): int(row["qty"]) for row in rows}
    return quantities.get((item_id, SHELF), 0), quantities.get((item_id, STORE), 0)


def _card_day_for(moment: datetime) -> date:
    """The run day whose card covers `moment`'s Tehran day: the card reports the day before it."""
    today = to_tehran(moment).date()
    run_day = today + timedelta(days=1)
    return run_day + timedelta(days=1) if run_day.weekday() == FRIDAY else run_day


def _state_word(page: Page) -> str:
    """The state the card prints for this week's count, in the card's own vocabulary (M-20)."""
    row = page.locator("section[aria-labelledby='mg-weekly-title'] .mg-line.mg-strong").first
    return row.inner_text().strip()


def _weekly_block(page: Page) -> str:
    """The card's weekly-count block as the page renders it."""
    return _flat(page.locator("section[aria-labelledby='mg-weekly-title']").inner_text())


def _write_the_card(shift: LiveShift) -> Card:
    """Rewrite today's card from the log the way the scheduler does, and prove it covers today."""
    now = SystemClock().now_utc()
    run_day = _card_day_for(now)
    card = shift.cards.regenerate(run_day)
    assert card is not None, "the scheduler path stored no card for the day"
    window = window_for(run_day)
    assert window is not None and window.covered_from <= to_tehran(now).date() <= window.covered_to
    return card


@pytest.mark.e2e
def test_the_chain_from_a_storekeeper_draft_to_a_paid_invoice(live_shift: LiveShift, browser: Browser) -> None:
    """One order walks the whole chain: draft, decide, place, receive with a problem, pay (P-01…P-08)."""
    phones: list[BrowserContext] = []
    try:
        # 1. The storekeeper opens the screen from the app shell and drafts what the store is short of.
        storekeeper, page = _open_phone(browser)
        phones.append(storekeeper)
        _login(page, live_shift.base_url, STOREKEEPER)
        page.goto(f"{live_shift.base_url}/", wait_until="domcontentloaded")
        link = page.locator('a[href="/purchase"]')
        assert link.count() == 1, "the shell offers a storekeeper exactly one way to the purchase screen"
        link.click()
        page.wait_for_selector("#purchase-draft .pu-lines-box .pu-line [data-item]", timeout=15000)
        assert page.url.endswith("/purchase")
        suggestions = _flat(page.inner_text("#purchase-draft .pu-suggestions"))
        assert _flat(ITEM_NAME) in suggestions, "a below-minimum item is the draft's suggestion (P-01)"
        page.select_option("#purchase-draft .pu-lines-box .pu-line:nth-child(1) [data-item]", ITEM)
        page.fill("#purchase-draft .pu-lines-box .pu-line:nth-child(1) [data-quantity]", str(QUANTITY))
        page.click("#purchase-draft [data-do='order.draft']")
        _wait_status(page, DRAFTED)

        # 2. The daily card shows the order waiting for the manager, before the decision (M-19).
        _write_the_card(live_shift)
        manager, manager_page = _open_phone(browser)
        phones.append(manager)
        _login(manager_page, live_shift.base_url, MANAGER)
        manager_page.goto(f"{live_shift.base_url}/manager/card", wait_until="domcontentloaded")
        manager_page.wait_for_selector(".mg-page", timeout=15000)
        card_text = _flat(manager_page.inner_text(".mg-page"))
        assert _flat(CARD_ORDERS) in card_text, "the card carries the order waiting for a decision"
        assert _flat(ITEM_NAME) in card_text
        assert PRICE_WORD not in card_text, "an order row carries no price (M-19)"

        # 3. The manager decides on the purchase screen: one approval, and the order leaves the queue.
        manager_page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        manager_page.wait_for_selector("#purchase-decide .pu-card", timeout=15000)
        waiting = _flat(manager_page.inner_text("#purchase-decide"))
        assert _flat(ITEM_NAME) in waiting
        manager_page.click("#purchase-decide [data-do='order.decide.approved']")
        _wait_status(manager_page, DECIDED)

        # 4. The purchaser hands the approved order to a vendor, with its reference (P-03).
        purchaser, purchaser_page = _open_phone(browser)
        phones.append(purchaser)
        _login(purchaser_page, live_shift.base_url, PURCHASER)
        purchaser_page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        purchaser_page.wait_for_selector("#purchase-place [data-vendor]", timeout=15000)
        purchaser_page.fill("#purchase-place [data-vendor]", VENDOR)
        purchaser_page.fill("#purchase-place [data-reference]", ORDER_REFERENCE)
        purchaser_page.click("#purchase-place [data-do='order.place']")
        _wait_status(purchaser_page, PLACED)

        # 5. Reception takes the parcel in at the door, with a problem flag: units, never a price.
        reception, reception_page = _open_phone(browser)
        phones.append(reception)
        _login(reception_page, live_shift.base_url, RECEPTIONIST)
        reception_page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        reception_page.wait_for_selector("#purchase-receive [data-invoice-reference]", timeout=15000)
        reception_page.fill("#purchase-receive [data-invoice-reference]", INVOICE_REFERENCE)
        line = "#purchase-receive .pu-lines-box .pu-line:nth-child(1) "
        reception_page.fill(f"{line}[data-item]", ITEM)
        reception_page.fill(f"{line}[data-location]", SHELF)
        reception_page.fill(f"{line}[data-quantity]", str(QUANTITY))
        reception_page.set_input_files(
            "#purchase-receive [data-invoice-photo]",
            files=[{"name": "invoice.jpg", "mimeType": "image/jpeg", "buffer": INVOICE_PHOTO}],
        )
        reception_page.fill("#purchase-receive [data-packages]", str(PACKAGES))
        reception_page.select_option("#purchase-receive [data-reason]", PROBLEM)
        reception_page.click("#purchase-receive [data-do='delivery.receive']")
        _wait_status(reception_page, RECEIVED)
        door = _flat(reception_page.inner_text("#purchase-receive"))
        transit = _flat(reception_page.inner_text("#purchase-transit"))
        assert PRICE_WORD not in door and PRICE_WORD not in transit, "no price at the door (R-01, P-04)"
        assert INVOICE_REFERENCE in transit

        # 6. The card reports the flagged parcel with its payment locked (M-19).
        _write_the_card(live_shift)
        manager_page.goto(f"{live_shift.base_url}/manager/card", wait_until="domcontentloaded")
        manager_page.wait_for_selector(".mg-page", timeout=15000)
        card_text = _flat(manager_page.inner_text(".mg-page"))
        assert _flat(CARD_FLAGGED) in card_text, "the card carries the flagged parcel and its locked payment"
        assert INVOICE_REFERENCE in card_text, "the flagged row names the receipt it belongs to"

        # 7. The accountant sees the lock, types the invoice's prices, and is refused the payment once.
        accountant, accountant_page = _open_phone(browser)
        phones.append(accountant)
        _login(accountant_page, live_shift.base_url, ACCOUNTANT)
        accountant_page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        accountant_page.wait_for_selector("#purchase-invoice .pu-card", timeout=15000)
        assert _flat(LOCK) in _flat(accountant_page.inner_text("#purchase-invoice")), (
            "a flagged parcel locks the payment"
        )
        assert accountant_page.locator("#purchase-invoice [data-do='payment.record']").count() == 0
        price_line = "#purchase-invoice .pu-lines-box .pu-line:nth-child(1) "
        accountant_page.fill(f"{price_line}[data-item]", ITEM)
        accountant_page.fill(f"{price_line}[data-quantity]", str(QUANTITY))
        accountant_page.fill(f"{price_line}[data-price]", str(UNIT_PRICE))
        accountant_page.click("#purchase-invoice [data-do='invoice.digitize']")
        _wait_status(accountant_page, DIGITIZED)
        parcel = _state_from_page(accountant_page)["invoices"][0]
        assert parcel["invoice_total"] == QUANTITY * UNIT_PRICE, "the server's own total"
        assert parcel["payment_locked"] is True
        refused = _post_from_page(
            accountant_page,
            "payment.record",
            {"delivery_id": parcel["delivery_id"], "amount_toman": UNIT_PRICE * QUANTITY, "method": "cash"},
        )
        assert refused["status"] == 422, refused
        assert refused["body"]["error"]["message"] == PAYMENT_LOCKED
        assert accountant_page.locator("#purchase-invoice [data-do='payment.record']").count() == 0

        # 8. The manager opens the phone's approvals and approves the flag, which unlocks the payment.
        manager_page.goto(f"{live_shift.base_url}/events", wait_until="domcontentloaded")
        manager_page.wait_for_selector("#shift-pending-list .se-pending-card", timeout=15000)
        assert _flat(FLAG_LABEL) in _flat(manager_page.inner_text("#shift-pending-list"))
        manager_page.on("dialog", lambda dialog: dialog.accept(""))
        manager_page.click("#shift-pending-list [data-decision='event.approved']")
        manager_page.wait_for_function(
            "() => document.querySelectorAll('#shift-pending-list .se-pending-card').length === 0",
            timeout=20000,
        )

        # 9. The accountant records the payment; the screen says the invoice is paid.
        accountant_page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        accountant_page.wait_for_selector("#purchase-invoice [data-amount]", timeout=15000)
        accountant_page.fill("#purchase-invoice [data-amount]", str(QUANTITY * UNIT_PRICE))
        accountant_page.select_option("#purchase-invoice [data-method]", "cash")
        accountant_page.fill("#purchase-invoice [data-receipt]", RECEIPT)
        accountant_page.click("#purchase-invoice [data-do='payment.record']")
        _wait_status(accountant_page, PAID)
        paid = _flat(accountant_page.inner_text("#purchase-invoice"))
        assert _flat(PAID_LABEL) in paid
        assert accountant_page.locator("#purchase-invoice [data-do='payment.record']").count() == 0
        assert _state_from_page(accountant_page)["invoices"][0]["remaining"] == 0
    finally:
        for context in phones:
            context.close()


@pytest.mark.e2e
def test_the_warehouse_shelf_charge_and_the_blind_weekly_count(live_shift: LiveShift, browser: Browser) -> None:
    """The store fills one shelf, the shelf projection moves, and the count closes the week (P-10/P-11)."""
    phones: list[BrowserContext] = []
    try:
        storekeeper, page = _open_phone(browser)
        phones.append(storekeeper)
        _login(page, live_shift.base_url, STOREKEEPER)
        page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        page.wait_for_selector("#purchase-restock [data-restock-item]", timeout=15000)

        # 1. The storekeeper offers the reception shelf's charge, naming the person who confirms it (P-10, G3).
        page.select_option("#purchase-restock [data-restock-item]", ITEM)
        page.fill("#purchase-restock [data-restock-quantity]", str(CHARGE))
        page.select_option("#purchase-restock [data-confirmer]", RECEPTIONIST.staff_id)
        page.click("#purchase-restock [data-do='restock.record']")
        _wait_status(page, OFFERED)
        assert _stock(live_shift.database, ITEM) == (0, 0), "an offer moves nothing until both sides counted"

        # 1b. The named receiver counts what arrived on the shelf and confirms it: now the stock moves.
        reception, reception_page = _open_phone(browser)
        phones.append(reception)
        _login(reception_page, live_shift.base_url, RECEPTIONIST)
        reception_page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        reception_page.wait_for_selector("#purchase-inbox [data-offer-count]", timeout=15000)
        inbox_text = _flat(reception_page.inner_text("#purchase-inbox"))
        assert _flat(ITEM_NAME) in inbox_text, "the offer names the item the shelf is waiting for"
        reception_page.fill("#purchase-inbox [data-offer-count]", str(CHARGE))
        reception_page.click("#purchase-inbox [data-do='restock.confirm']")
        _wait_status(reception_page, CONFIRMED)
        # The shelf moved by exactly the charge, and the screen still prints no shelf figure (P-11).
        assert _stock(live_shift.database, ITEM) == (CHARGE, -CHARGE)
        page.goto(f"{live_shift.base_url}/purchase", wait_until="domcontentloaded")
        page.wait_for_selector("#purchase-restock [data-restock-item]", timeout=15000)
        history = _flat(page.inner_text("#purchase-restock"))
        assert _flat(ITEM_NAME) in history
        assert "انبار اصلی" in history and "قفسهٔ پذیرش" in history
        assert _fa(CHARGE) in history, "the record says how much moved"
        assert page.locator("#purchase-count [data-count]").evaluate_all(
            "nodes => nodes.every(node => node.value === '')"
        ), "the count boxes start empty: the count is blind"

        # 2. With no count in this window, the manager's card reports the weekly count as not done (M-20).
        due = _write_the_card(live_shift)
        assert [row.state for row in due.weekly_count] == ["due"], "the window is open and uncounted"
        manager, manager_page = _open_phone(browser)
        phones.append(manager)
        _login(manager_page, live_shift.base_url, MANAGER)
        manager_page.goto(f"{live_shift.base_url}/manager/card", wait_until="domcontentloaded")
        manager_page.wait_for_selector("section[aria-labelledby='mg-weekly-title']", timeout=15000)
        weekly = _weekly_block(manager_page)
        assert _flat(CARD_WEEKLY) in weekly, "the card carries the weekly count's state"
        assert _flat("بازهٔ شمارش") in weekly, "the row names the window it waits for"
        assert _state_word(manager_page) != "انجام شده", "nothing counted this window yet"

        # 3. The storekeeper counts the store blind, the item the charge left there (P-11).
        count_row = page.locator("#purchase-count .pu-count-row").filter(has_text=ITEM_NAME).first
        count_row.locator("[data-count]").fill(str(COUNTED))
        count_row.locator("[data-near]").check()
        page.click("#purchase-count [data-do='weekly.count']")
        _wait_status(page, COUNTED_STATUS)

        # 4. The card now reports the count as done, in the week's own words (M-20).
        closed = _write_the_card(live_shift)
        assert [row.state for row in closed.weekly_count] == ["done"], "the count closed this window"
        manager_page.goto(f"{live_shift.base_url}/manager/card", wait_until="domcontentloaded")
        manager_page.wait_for_selector("section[aria-labelledby='mg-weekly-title']", timeout=15000)
        assert _flat(CARD_WEEKLY) in _weekly_block(manager_page)
        assert _state_word(manager_page) == "انجام شده", "the card renders the week's own state"
    finally:
        for context in phones:
            context.close()
