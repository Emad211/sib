"""Cash corrections: a re-closed or voided invoice is reversed, never silently kept (R-03, S04 §2.1).

Accounting's reopen route deletes the payment lines and closes the invoice again with a new
`closed_at`; its void route keeps the old lines and sets `status='voided'`. Both take back a cash
fact the bridge may already have ingested. These tests drive the synthetic accounting database
through those sequences and assert that the shift side nets to the amount accounting currently shows,
that re-polling adds nothing, and that a rebuild reproduces the live projection.
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from make_synthetic_acc_db import SyntheticAccounting, build

from event_fixtures import build_event
from shift.adapters.accounting import views
from shift.adapters.accounting.views import parse_time
from shift.adapters.db import projections_repo, watermark_repo
from shift.adapters.db.core import Database
from shift.domain.cutoff import ShiftCutoff
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine

CASH_VIEW = views.ViewName.CASH_RECEIPTS.value
VOIDS_VIEW = views.ViewName.VOIDS.value


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


def _close_invoice(path: Path, *, closed_at: str, cash: int, patient: int = 1) -> int:
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


def _reopen(path: Path, invoice_id: int) -> None:
    """Accounting's reopen route: lines deleted, invoice back to open (FB-25)."""
    _run_sql(
        path,
        [
            ("DELETE FROM invoice_payment_lines WHERE invoice_id = ?", (invoice_id,)),
            ("UPDATE invoices SET status = 'open', closed_at = NULL WHERE id = ?", (invoice_id,)),
        ],
    )


def _reclose(path: Path, invoice_id: int, *, closed_at: str, cash: int) -> None:
    """Close the reopened invoice again, with the corrected amount and a new close timestamp."""
    _run_sql(
        path,
        [
            (
                "UPDATE invoices SET status = 'closed', closed_at = ?, total_amount = ? WHERE id = ?",
                (closed_at, cash, invoice_id),
            ),
            (
                "INSERT INTO invoice_payment_lines (invoice_id, item_type, item_id, payment_type, patient_amount, "
                "closed_at) VALUES (?, 'visit', 1, 'cash', ?, ?)",
                (invoice_id, cash, closed_at),
            ),
        ],
    )


def _void(
    path: Path, invoice_id: int, *, amount: int, voided_at: str, kind: str = "refund", method: str | None = "cash"
) -> None:
    """Accounting's void route: the reason is kept, the invoice leaves the closed set."""
    _run_sql(
        path,
        [
            (
                "INSERT INTO invoice_voids (invoice_id, kind, amount, refund_method, reason, voided_by, voided_at) "
                "VALUES (?, ?, ?, ?, 'ثبت آزمایشی', 'manager1', ?)",
                (invoice_id, kind, amount, method, voided_at),
            ),
            ("UPDATE invoices SET status = 'voided' WHERE id = ?", (invoice_id,)),
        ],
    )


def _events(database: Database) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT event_id, type, shift_id, payload, ref_event_id, idempotency_key FROM events ORDER BY seq"
        ).mappings()
        return [
            {
                "event_id": str(row["event_id"]),
                "type": str(row["type"]),
                "shift_id": row["shift_id"],
                "payload": json.loads(row["payload"]),
                "ref": row["ref_event_id"],
                "key": str(row["idempotency_key"]),
            }
            for row in rows
        ]


def _cash_versions(database: Database, invoice_id: int) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT closed_at, amount, superseded_at FROM acc_cash WHERE invoice_id = ? ORDER BY closed_at",
            (invoice_id,),
        ).mappings()
        return [dict(row) for row in rows]


def _net_cash(database: Database, projections: ProjectionEngine) -> int:
    """What the shift projection says the drawer holds, after applying every event so far."""
    projections.sync()
    with database.read() as connection:
        return sum(int(row["expected"]) for row in projections_repo.cash_rows(connection))


def _accounting_net(path: Path, until: str) -> int:
    """What accounting currently says the drawer should hold, read through the contract view."""
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM v_shift_cash_receipts_v1 WHERE closed_at <= ?", (until,)
        ).fetchone()
    finally:
        connection.close()
    return int(round(row[0]))


def _invoice_events(database: Database, invoice_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = _events(database)
    receipts = [event for event in events if event["type"] == "acc.cash_receipt" and f":{invoice_id}@" in event["key"]]
    reversals = [event for event in events if event["type"] == "acc.cash_reversal"]
    return receipts, reversals


def test_reclosing_a_polled_invoice_reverses_the_superseded_receipt(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    morning: ShiftCutoff,
    database: Database,
    projections: ProjectionEngine,
) -> None:
    bridge.poll(scenario.path, morning)
    before = _net_cash(database, projections)
    _reopen(scenario.path, scenario.cash_invoice)
    _reclose(scenario.path, scenario.cash_invoice, closed_at="2026-09-24 13:45:00", cash=200_000)

    report = bridge.poll(scenario.path, morning)

    assert report.events == 2  # one reversal and one corrected receipt
    assert report.rows[CASH_VIEW] == 1
    assert _net_cash(database, projections) == before - 150_000 + 200_000
    assert _net_cash(database, projections) == _accounting_net(scenario.path, scenario.cutoff_at)
    receipts, reversals = _invoice_events(database, scenario.cash_invoice)
    assert [event["payload"]["amount"] for event in receipts] == [150_000, 200_000]
    assert receipts[0]["ref"] is None
    assert receipts[1]["ref"] == receipts[0]["event_id"]
    assert receipts[1]["key"] == "acc:v_shift_cash_receipts_v1:1@2026-09-24 13:45:00@200000"
    assert len(reversals) == 1
    assert reversals[0]["ref"] == receipts[0]["event_id"]
    assert reversals[0]["payload"] == {"amount": 150_000, "amount_raw": 150_000.0, "reason": "reclosed"}
    assert [
        (version["closed_at"], version["amount"], version["superseded_at"] is not None)
        for version in _cash_versions(database, scenario.cash_invoice)
    ] == [
        ("2026-09-24 08:10:00", 150_000, True),
        ("2026-09-24 13:45:00", 200_000, False),
    ]

    again = bridge.poll(scenario.path, morning)

    assert again.events == 0
    assert set(again.rows.values()) == {0}
    assert _net_cash(database, projections) == before - 150_000 + 200_000


def test_a_reclose_at_the_same_stamp_corrects_the_amount_in_place(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    morning: ShiftCutoff,
    database: Database,
    projections: ProjectionEngine,
) -> None:
    invoice = _close_invoice(scenario.path, closed_at="2026-09-24 13:20:00", cash=100_000)
    bridge.poll(scenario.path, morning)
    before = _net_cash(database, projections)
    _reopen(scenario.path, invoice)
    _reclose(scenario.path, invoice, closed_at="2026-09-24 13:20:00", cash=120_000)

    report = bridge.poll(scenario.path, morning)

    assert report.events == 2
    assert report.rows[CASH_VIEW] == 1
    assert _net_cash(database, projections) == before - 100_000 + 120_000
    assert _net_cash(database, projections) == _accounting_net(scenario.path, scenario.cutoff_at)
    receipts, reversals = _invoice_events(database, invoice)
    assert [event["payload"]["amount"] for event in receipts] == [100_000, 120_000]
    assert reversals[0]["ref"] == receipts[0]["event_id"]
    assert [
        (version["closed_at"], version["amount"], version["superseded_at"])
        for version in _cash_versions(database, invoice)
    ] == [("2026-09-24 13:20:00", 120_000, None)]
    assert bridge.poll(scenario.path, morning).events == 0


def test_two_recloses_net_to_the_latest_amount_and_still_rebuild(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    morning: ShiftCutoff,
    database: Database,
    projections: ProjectionEngine,
) -> None:
    invoice = _close_invoice(scenario.path, closed_at="2026-09-24 13:10:00", cash=100_000)
    bridge.poll(scenario.path, morning)
    before = _net_cash(database, projections)
    _reopen(scenario.path, invoice)
    _reclose(scenario.path, invoice, closed_at="2026-09-24 13:15:00", cash=130_000)
    assert bridge.poll(scenario.path, morning).events == 2
    _reopen(scenario.path, invoice)
    _reclose(scenario.path, invoice, closed_at="2026-09-24 13:20:00", cash=110_000)
    assert bridge.poll(scenario.path, morning).events == 2

    assert _net_cash(database, projections) == before - 100_000 + 110_000
    assert _net_cash(database, projections) == _accounting_net(scenario.path, scenario.cutoff_at)
    receipts, reversals = _invoice_events(database, invoice)
    assert [event["payload"]["amount"] for event in receipts] == [100_000, 130_000, 110_000]
    assert [event["payload"]["amount"] for event in reversals] == [100_000, 130_000]
    assert [event["ref"] for event in receipts[1:]] == [receipts[0]["event_id"], receipts[1]["event_id"]]
    assert [event["ref"] for event in reversals] == [receipts[0]["event_id"], receipts[1]["event_id"]]
    assert [
        (version["closed_at"], version["amount"], version["superseded_at"] is not None)
        for version in _cash_versions(database, invoice)
    ] == [
        ("2026-09-24 13:10:00", 100_000, True),
        ("2026-09-24 13:15:00", 130_000, True),
        ("2026-09-24 13:20:00", 110_000, False),
    ]

    check = projections.rebuild(dry_run=True)
    assert check.drift == ()
    assert projections.rebuild().drift == ()
    assert bridge.poll(scenario.path, morning).events == 0


@pytest.mark.parametrize(("kind", "method", "reason"), [("refund", "cash", "refunded"), ("void", None, "voided")])
def test_voiding_a_polled_invoice_nets_its_cash_to_zero(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    morning: ShiftCutoff,
    database: Database,
    projections: ProjectionEngine,
    kind: str,
    method: str | None,
    reason: str,
) -> None:
    invoice = _close_invoice(scenario.path, closed_at="2026-09-24 13:10:00", cash=80_000)
    bridge.poll(scenario.path, morning)
    before = _net_cash(database, projections)
    _void(scenario.path, invoice, amount=80_000, voided_at="2026-09-24 13:30:00", kind=kind, method=method)

    report = bridge.poll(scenario.path, morning)

    assert report.events == 1
    assert report.rows[VOIDS_VIEW] == 1
    assert _net_cash(database, projections) == before - 80_000
    assert _net_cash(database, projections) == _accounting_net(scenario.path, scenario.cutoff_at)
    receipts, reversals = _invoice_events(database, invoice)
    assert len(reversals) == 1
    assert reversals[0]["ref"] == receipts[0]["event_id"]
    assert reversals[0]["payload"] == {"amount": 80_000, "amount_raw": 80_000.0, "reason": reason}
    with database.read() as connection:
        compensated_at = connection.exec_driver_sql(
            "SELECT compensated_at FROM acc_void WHERE invoice_id = ?", (invoice,)
        ).scalar_one()
    assert compensated_at is not None

    again = bridge.poll(scenario.path, morning)

    assert again.events == 0
    assert set(again.rows.values()) == {0}
    assert _net_cash(database, projections) == before - 80_000


def test_a_void_for_an_invoice_with_no_ingested_cash_adds_no_event(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff, database: Database
) -> None:
    report = bridge.poll(scenario.path, morning)

    assert report.events == 3
    assert [event for event in _events(database) if event["type"] == "acc.cash_reversal"] == []
    with database.read() as connection:
        compensated = connection.exec_driver_sql(
            "SELECT compensated_at FROM acc_void WHERE invoice_id = ?", (scenario.refunded_invoice,)
        ).scalar_one()
    assert compensated is not None  # there was nothing to reverse, and re-polling must stay a no-op


def test_a_receipt_written_before_corrections_is_reversed_by_its_legacy_key(
    scenario: SyntheticAccounting,
    bridge: AccountingBridge,
    morning: ShiftCutoff,
    database: Database,
    store: EventStore,
    projections: ProjectionEngine,
) -> None:
    """A v3 database has `acc:v_shift_cash_receipts_v1:<invoice>` keys; migration 6 rewinds the watermark."""
    invoice = _close_invoice(scenario.path, closed_at="2026-09-24 13:10:00", cash=80_000)
    legacy = store.append(
        build_event(
            "acc.cash_receipt",
            payload={"amount": 80_000},
            shift_id=morning.shift_id,
            work_date=morning.work_date,
            client_at=parse_time("2026-09-24 13:10:00"),
            idempotency_key=f"acc:{CASH_VIEW}:{invoice}",
        )
    ).event
    with database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO acc_cash (invoice_id, closed_at, amount, amount_raw, ingested_at) VALUES (?, ?, ?, ?, ?)",
            (invoice, "2026-09-24 13:10:00", 80_000, 80_000.0, "2026-09-24T00:00:00Z"),
        )
        watermark_repo.save(connection, CASH_VIEW, "2026-09-24 13:10:00", invoice, "2026-09-24T00:00:00Z")
    _reopen(scenario.path, invoice)
    _reclose(scenario.path, invoice, closed_at="2026-09-24 13:45:00", cash=90_000)

    report = bridge.poll(scenario.path, morning)

    assert report.events == 5  # three synthetic receipts, the reversal and the corrected receipt
    assert report.rows[CASH_VIEW] == 4
    receipts, reversals = _invoice_events(database, invoice)
    assert len(reversals) == 1
    assert reversals[0]["ref"] == legacy.event_id
    assert reversals[0]["payload"]["amount"] == 80_000
    assert [event["payload"]["amount"] for event in receipts] == [90_000]
    assert _net_cash(database, projections) == _accounting_net(scenario.path, scenario.cutoff_at)
    assert bridge.poll(scenario.path, morning).events == 0
