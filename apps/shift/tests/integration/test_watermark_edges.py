"""Watermark edge cases: the seed is below every id, and paging separates a shared close second.

S04 §2 asks for keyset paging on `(timestamp, id)`; these two tests pin the corners: the seed must
sort before a row whose id is 0, and two invoices closed in the same second must page apart by id
instead of being skipped or counted twice.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from clocks import FixedClock
from make_synthetic_acc_db import SyntheticAccounting, build

from shift.adapters.accounting.views import parse_time
from shift.adapters.db import mirror_repo, watermark_repo
from shift.adapters.db.core import Database
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def clock(scenario: SyntheticAccounting) -> FixedClock:
    """The bridge reads up to this instant: the scenario's own 14:00 handover."""
    return FixedClock(parse_time(scenario.cutoff_at))


@pytest.fixture()
def bridge(database: Database, store: EventStore, clock: FixedClock) -> AccountingBridge:
    """The bridge under test, writing into the shift app's own database."""
    return AccountingBridge(database, store, clock=clock)


def _close_invoice(path: Path, *, closed_at: str, cash: float, patient: int = 1) -> int:
    """Close one more invoice in accounting, the way reception does."""
    connection = sqlite3.connect(path)
    try:
        cursor = connection.execute(
            "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, "
            "closed_at) VALUES (?, 'closed', 'آزاد', ?, '2026-09-24', 'morning', ?, ?)",
            (patient, cash, closed_at, closed_at),
        )
        invoice = int(cursor.lastrowid or 0)
        connection.execute(
            "INSERT INTO invoice_payment_lines (invoice_id, item_type, item_id, payment_type, patient_amount, "
            "closed_at) VALUES (?, 'visit', 1, 'cash', ?, ?)",
            (invoice, cash, closed_at),
        )
        connection.commit()
    finally:
        connection.close()
    return invoice


def _events(database: Database) -> list[dict[str, object]]:
    with database.read() as connection:
        rows = connection.exec_driver_sql("SELECT payload FROM events ORDER BY seq").mappings()
        return [json.loads(row["payload"]) for row in rows]


def test_the_seed_watermark_sorts_before_every_accounting_id(database: Database) -> None:
    with database.read() as connection:
        seed = watermark_repo.get(connection, "v_shift_patient_registrations_v1")

    assert (seed.last_key_at, seed.last_key_id) == (watermark_repo.EMPTY_WATERMARK_AT, -1)
    assert watermark_repo.EMPTY_WATERMARK_ID < 0  # a row whose id is 0 is still read


def test_two_invoices_closed_in_the_same_second_are_paged_apart_by_their_ids(
    scenario: SyntheticAccounting, store: EventStore, database: Database, clock: FixedClock
) -> None:
    """Keyset disambiguation: `(closed_at = :after_at AND invoice_id > :after_id)` (S04 §2)."""
    first = _close_invoice(scenario.path, closed_at="2026-09-24 13:57:00", cash=1_000)
    second = _close_invoice(scenario.path, closed_at="2026-09-24 13:57:00", cash=2_000, patient=2)
    small_pages = AccountingBridge(database, store, page_size=1, clock=clock)

    report = small_pages.poll(scenario.path)

    assert report.rows["v_shift_cash_receipts_v1"] == 5
    assert report.events == 5
    assert report.pages >= 5
    with database.read() as connection:
        cash = {row["invoice_id"]: row["amount"] for row in mirror_repo.rows(connection, mirror_repo.CASH)}
    assert (cash[first], cash[second]) == (1_000, 2_000)
    assert len(_events(database)) == 5
