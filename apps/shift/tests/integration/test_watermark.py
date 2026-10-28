"""Watermark paging: every accounting row is ingested exactly once (S01 R-05, S04 §2).

The watermark is the contract with the past: keyset paging, one commit per page together with the
rows it covers, and idempotency keys that make a crash or a re-poll a no-op.
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from make_synthetic_acc_db import SyntheticAccounting, build
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from shift.adapters.accounting import views
from shift.adapters.accounting.views import parse_time
from shift.adapters.db import migrations, mirror_repo, watermark_repo
from shift.adapters.db.core import Database
from shift.adapters.db.migrations import m0001_baseline
from shift.domain.cutoff import ShiftCutoff
from shift.domain.events import EventType
from shift.services.accounting_bridge import FEEDS, AccountingBridge
from shift.services.event_store import EventStore


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def morning(scenario: SyntheticAccounting) -> ShiftCutoff:
    """The 14:00 handover of the synthetic morning shift."""
    return ShiftCutoff(shift_id="shift-morning", work_date=scenario.work_date, cutoff_at=parse_time(scenario.cutoff_at))


@pytest.fixture()
def bridge(database: Database, store: EventStore) -> AccountingBridge:
    """The bridge under test, writing into the shift app's own database."""
    return AccountingBridge(database, store)


def _run_sql(path: Path, statements: Sequence[tuple[str, Sequence[object]]]) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement, parameters in statements:
            connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


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
        rows = connection.exec_driver_sql(
            "SELECT type, shift_id, idempotency_key, payload FROM events ORDER BY seq"
        ).mappings()
        return [
            {
                "type": row["type"],
                "shift_id": row["shift_id"],
                "key": row["idempotency_key"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]


def test_the_first_poll_mirrors_the_shifts_cash_and_appends_one_event_per_invoice(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    report = bridge.poll(scenario.path, morning)

    assert report.degraded is False
    assert report.rows["v_shift_cash_receipts_v1"] == 3  # the 14:20 invoice belongs to the next shift
    assert report.events == 3
    with database.read() as connection:
        cash = mirror_repo.rows(connection, mirror_repo.CASH)
    assert [(row["invoice_id"], row["amount"], row["amount_raw"]) for row in cash] == [
        (1, 150_000, 150_000.0),
        (2, 40_000, 40_000.0),
        (3, 25_000, 25_000.0),
    ]
    events = _events(database)
    assert {event["key"] for event in events} == {
        "acc:v_shift_cash_receipts_v1:1",
        "acc:v_shift_cash_receipts_v1:2",
        "acc:v_shift_cash_receipts_v1:3",
    }
    assert {event["type"] for event in events} == {"acc.cash_receipt"}
    assert {event["shift_id"] for event in events} == {"shift-morning"}
    assert [event["payload"]["amount"] for event in events] == [150_000, 40_000, 25_000]


def test_a_second_poll_adds_no_row_and_no_event(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    bridge.poll(scenario.path, morning)

    again = bridge.poll(scenario.path, morning)

    assert again.events == 0
    assert set(again.rows.values()) == {0}
    assert len(_events(database)) == 3


def test_paging_one_row_at_a_time_never_double_counts(
    scenario: SyntheticAccounting, store: EventStore, morning: ShiftCutoff, database: Database
) -> None:
    small_pages = AccountingBridge(database, store, page_size=1)

    report = small_pages.poll(scenario.path, morning)

    assert report.rows["v_shift_cash_receipts_v1"] == 3
    assert report.events == 3
    assert report.pages >= 3
    with database.read() as connection:
        assert sum(row["amount"] for row in mirror_repo.rows(connection, mirror_repo.CASH)) == 215_000
    assert len(_events(database)) == 3


def test_a_lost_watermark_cannot_duplicate_a_row_or_an_event(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    bridge.poll(scenario.path, morning)
    with database.write() as connection:
        connection.exec_driver_sql("DELETE FROM acc_watermark")

    replay = bridge.poll(scenario.path, morning)

    assert replay.events == 0  # the idempotency key returns the stored event
    assert replay.rows["v_shift_cash_receipts_v1"] == 0  # the mirror keeps the row it already has
    assert len(_events(database)) == 3
    with database.read() as connection:
        assert mirror_repo.count(connection, mirror_repo.CASH) == 3


def test_cash_closed_after_a_poll_is_picked_up_by_the_next_poll(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    bridge.poll(scenario.path, morning)
    _close_invoice(scenario.path, closed_at="2026-09-24 13:58:00", cash=5_000)

    report = bridge.poll(scenario.path, morning)

    assert report.rows["v_shift_cash_receipts_v1"] == 1
    assert report.events == 1
    with database.read() as connection:
        assert [row["amount"] for row in mirror_repo.rows(connection, mirror_repo.CASH)][-1] == 5_000


def test_the_watermark_records_the_last_row_it_read(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    bridge.poll(scenario.path, morning)

    with database.read() as connection:
        watermark = watermark_repo.get(connection, "v_shift_cash_receipts_v1")
        registrations = watermark_repo.get(connection, "v_shift_patient_registrations_v1")

    assert (watermark.last_key_at, watermark.last_key_id, watermark.ok, watermark.error) == (
        "2026-09-24 13:40:00",
        3,
        True,
        None,
    )
    assert (registrations.last_key_at, registrations.last_key_id) == ("2026-09-24 12:05:00", 3)


def test_a_view_that_breaks_mid_flight_is_recorded_and_the_other_feeds_still_sync(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    _run_sql(
        scenario.path,
        [
            ("DROP VIEW v_shift_cash_receipts_v1", ()),
            ("CREATE VIEW v_shift_cash_receipts_v1 AS SELECT * FROM v_shift_removed_v0", ()),
        ],
    )

    report = bridge.poll(scenario.path, morning)

    assert report.degraded is True
    assert "v_shift_cash_receipts_v1" in (report.error or "")
    assert report.rows["v_shift_vitals_services_v1"] == 3
    with database.read() as connection:
        watermark = watermark_repo.get(connection, "v_shift_cash_receipts_v1")
    assert watermark.ok is False
    assert "v_shift_cash_receipts_v1" in (watermark.error or "")


def test_the_open_invoice_snapshot_is_replaced_not_accumulated(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    first = bridge.poll(scenario.path, morning)
    _run_sql(
        scenario.path,
        [
            (
                "UPDATE invoices SET status = 'closed', closed_at = '2026-09-24 13:59:00' WHERE id = ?",
                (scenario.open_invoice,),
            )
        ],
    )
    second = bridge.poll(scenario.path, morning)

    assert first.open_invoices == 2
    assert second.open_invoices == 1
    with database.read() as connection:
        assert [row["invoice_id"] for row in mirror_repo.rows(connection, mirror_repo.OPEN_INVOICE)] == [
            scenario.reopened_invoice
        ]


def test_money_is_rounded_once_at_ingest_and_the_raw_value_is_kept(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    _close_invoice(scenario.path, closed_at="2026-09-24 13:55:00", cash=1_000.4)
    _close_invoice(scenario.path, closed_at="2026-09-24 13:56:00", cash=1_000.5)

    bridge.poll(scenario.path, morning)

    with database.read() as connection:
        cash = {
            row["closed_at"]: (row["amount"], row["amount_raw"])
            for row in mirror_repo.rows(connection, mirror_repo.CASH)
        }
    assert cash["2026-09-24 13:55:00"] == (1_000, 1_000.4)
    assert cash["2026-09-24 13:56:00"] == (1_001, 1_000.5)  # half-up, and only once
    assert [event["payload"]["amount"] for event in _events(database)][-2:] == [1_000, 1_001]


def test_an_empty_page_is_a_no_op_for_every_mirror(database: Database) -> None:
    with database.write() as connection:
        assert mirror_repo.insert_cash(connection, []) == 0
        assert mirror_repo.insert_consumption(connection, []) == 0
        assert mirror_repo.insert_vitals(connection, []) == 0
        assert mirror_repo.insert_registrations(connection, []) == 0
        assert mirror_repo.insert_voids(connection, []) == 0
        assert mirror_repo.replace_open_invoices(connection, [], "2026-09-24T00:00:00Z") == 0
        assert mirror_repo.count(connection, mirror_repo.OPEN_INVOICE) == 0


def test_the_mirror_repo_refuses_a_table_name_it_does_not_own(database: Database) -> None:
    """The table name is interpolated into SQL, so only the six known mirrors are accepted."""
    with database.read() as connection, pytest.raises(ValueError, match="unknown accounting mirror"):
        mirror_repo.count(connection, "acc_cash; DROP TABLE events")


def test_a_page_size_that_could_livelock_is_refused(database: Database, store: EventStore) -> None:
    with pytest.raises(ValueError, match="page_size"):
        AccountingBridge(database, store, page_size=0)


def test_every_paged_view_has_exactly_one_feed_and_only_cash_produces_events() -> None:
    """The registry decision from the bridge docstring, pinned: one feed per view, events only for cash."""
    assert set(FEEDS) == set(views.PAGED_READS)
    assert FEEDS[views.ViewName.CASH_RECEIPTS].event_type is EventType.ACC_CASH_RECEIPT
    assert [view for view, feed in FEEDS.items() if feed.event_type is not None] == [views.ViewName.CASH_RECEIPTS]


def test_a_version_1_database_gains_the_mirrors_with_a_backup_first(tmp_path: Path) -> None:
    """Step 1/2 deployments upgrade in place: migration 3 adds only tables, after a verified backup."""
    path = tmp_path / "v1.db"
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        m0001_baseline.up(connection)
        connection.exec_driver_sql("PRAGMA user_version = 1")
    engine.dispose()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
        assert (report.from_version, report.to_version) == (1, migrations.LATEST)
        assert report.applied == ["0003_accounting_mirror"]
        assert report.backup is not None
        with database.read() as connection:
            tables = {str(row[0]) for row in connection.exec_driver_sql("SELECT name FROM sqlite_master")}
            version = int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())
    finally:
        database.close()

    assert {
        "acc_watermark",
        "acc_cash",
        "acc_consumption",
        "acc_vital",
        "acc_registration",
        "acc_open_invoice",
        "acc_void",
    } <= tables
    assert {"events", "proj_stock", "proj_cash"} <= tables  # the step 4/5 tables are untouched
    assert version == migrations.LATEST
