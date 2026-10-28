"""`v_shift_voids_v1`: refunds and cancellations, added by accounting migration 7 (S04 §2.1).

Before migration 7 the bridge can only mirror the voided invoices' status; this file pins the view
that lets it see the money, the kind and the invoice's shift.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from make_synthetic_acc_db import SyntheticAccounting, build
from sqlalchemy import Connection

from shift.adapters.accounting import views
from shift.adapters.accounting.readonly import open_accounting


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database for the void checks."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def read(scenario: SyntheticAccounting) -> Iterator[Connection]:
    """A read-only connection, exactly as the bridge opens accounting."""
    with open_accounting(scenario.path) as database, database.connect() as connection:
        yield connection


def test_every_void_is_listed_with_its_money_kind_reason_and_shift(
    scenario: SyntheticAccounting, read: Connection
) -> None:
    rows = read.exec_driver_sql(
        "SELECT void_id, invoice_id, kind, amount, refund_method, reason, voided_by, voided_at, shift "
        "FROM v_shift_voids_v1 ORDER BY void_id"
    ).all()

    assert [tuple(row) for row in rows] == [
        (
            1,
            scenario.refunded_invoice,
            "refund",
            60_000.0,
            "cash",
            "ثبت آزمایشی",
            "manager1",
            "2026-09-24 13:05:00",
            "morning",
        ),
        (
            2,
            scenario.cancelled_invoice,
            "void",
            15_000.0,
            None,
            "ثبت آزمایشی",
            "manager1",
            "2026-09-24 15:30:00",
            "morning",
        ),
    ]


def test_a_void_carries_the_work_date_of_the_invoice_not_of_the_correction(read: Connection) -> None:
    rows = read.exec_driver_sql("SELECT DISTINCT work_date, shift FROM v_shift_voids_v1").all()

    assert [tuple(row) for row in rows] == [("2026-09-24", "morning")]


def test_the_money_of_a_refunded_invoice_leaves_the_cash_view(scenario: SyntheticAccounting, read: Connection) -> None:
    cash_ids = {row[0] for row in read.exec_driver_sql("SELECT invoice_id FROM v_shift_cash_receipts_v1").all()}
    refund = read.exec_driver_sql(
        "SELECT amount, refund_method FROM v_shift_voids_v1 WHERE invoice_id = ?", (scenario.refunded_invoice,)
    ).one()

    assert scenario.refunded_invoice not in cash_ids
    assert tuple(refund) == (60_000.0, "cash")


def test_the_view_only_exists_from_accounting_migration_7(scenario: SyntheticAccounting) -> None:
    connection = sqlite3.connect(scenario.path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        created_by = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view' AND name = ?", (views.ViewName.VOIDS.value,)
        ).fetchone()
    finally:
        connection.close()

    assert version >= 7
    assert created_by == (views.ViewName.VOIDS.value,)


def test_one_invoice_cannot_be_voided_twice(scenario: SyntheticAccounting) -> None:
    """The bridge keys its mirror on the invoice; accounting's UNIQUE is what keeps that honest."""
    connection = sqlite3.connect(scenario.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO invoice_voids (invoice_id, kind, amount, refund_method, reason) "
                "VALUES (?, 'refund', 1, 'cash', 'ثبت آزمایشی')",
                (scenario.refunded_invoice,),
            )
    finally:
        connection.close()
