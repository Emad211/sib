"""Hand-computed answers of the accounting views on the synthetic database (S04 §2, §2.1).

Every expected number below is counted from the scenario in `tools/make_synthetic_acc_db.py`:
cash is the sum of the *cash* payment lines of *closed* invoices, consumption scales by
`stock_factor`, and a voided or reopened invoice contributes nothing.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from make_synthetic_acc_db import SyntheticAccounting, build
from sqlalchemy import Connection

from shift.adapters.accounting.readonly import open_accounting


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database for the semantics checks."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def read(scenario: SyntheticAccounting) -> Iterator[Connection]:
    """A read-only connection, exactly as the bridge opens accounting."""
    with open_accounting(scenario.path) as database, database.connect() as connection:
        yield connection


def test_cash_is_the_sum_of_the_cash_lines_of_closed_invoices(read: Connection) -> None:
    rows = read.exec_driver_sql(
        "SELECT invoice_id, closed_at, amount FROM v_shift_cash_receipts_v1 ORDER BY invoice_id"
    ).all()

    # Invoice 1 paid 120 000 + 30 000 in cash and 50 000 by card: only the cash is in the drawer.
    assert [tuple(row) for row in rows] == [
        (1, "2026-09-24 08:10:00", 150_000.0),
        (2, "2026-09-24 09:05:00", 40_000.0),
        (3, "2026-09-24 13:40:00", 25_000.0),
        (4, "2026-09-24 14:20:00", 999_000.0),
    ]
    assert sum(row[2] for row in rows) == 1_214_000


def test_a_voided_invoice_contributes_no_cash_although_its_lines_remain(
    scenario: SyntheticAccounting, read: Connection
) -> None:
    cash_ids = {row[0] for row in read.exec_driver_sql("SELECT invoice_id FROM v_shift_cash_receipts_v1").all()}

    assert scenario.refunded_invoice not in cash_ids
    assert scenario.cancelled_invoice not in cash_ids
    frozen = read.exec_driver_sql(
        "SELECT SUM(patient_amount) FROM invoice_payment_lines WHERE invoice_id = ?",
        (scenario.refunded_invoice,),
    ).scalar_one()
    assert frozen == 60_000  # the refunded invoice keeps its frozen lines; its status excludes them


def test_a_reopened_invoice_contributes_neither_cash_nor_consumption(
    scenario: SyntheticAccounting, read: Connection
) -> None:
    cash_ids = {row[0] for row in read.exec_driver_sql("SELECT invoice_id FROM v_shift_cash_receipts_v1").all()}
    ledger_invoices = {row[0] for row in read.exec_driver_sql("SELECT invoice_id FROM v_shift_consumption_v1").all()}

    assert scenario.reopened_invoice not in cash_ids
    assert scenario.reopened_invoice not in ledger_invoices


def test_consumption_scales_by_the_stock_factor_and_skips_what_the_patient_brought(read: Connection) -> None:
    rows = read.exec_driver_sql(
        "SELECT invoice_id, acc_code, quantity, item_name FROM v_shift_consumption_v1 ORDER BY ledger_id"
    ).all()

    # 4 units of a tariff with stock_factor 0.5 are 2 stock units; the 9 units the patient brought are
    # not clinic stock; «دستکش» and «گاز استریل» have no acc_code yet, so they cannot move a shelf.
    assert [tuple(row) for row in rows] == [
        (1, "SYR-5", 3.0, "سرنگ ۵ سیسی"),
        (1, "SER-500", 2.0, "سرم نیم‌لیتری"),
        (1, None, 2.0, "گاز استریل"),
        (1, None, 5.0, "دستکش"),
    ]


def test_consumption_is_listed_once_per_ledger_line(read: Connection) -> None:
    total = read.exec_driver_sql("SELECT SUM(quantity) FROM v_shift_consumption_v1 WHERE acc_code = 'SYR-5'")
    count = read.exec_driver_sql("SELECT COUNT(*) FROM v_shift_consumption_v1")

    assert total.scalar_one() == 3.0
    assert count.scalar_one() == 4


def test_vitals_are_the_two_reward_services_of_closed_invoices(read: Connection) -> None:
    rows = read.exec_driver_sql(
        "SELECT invoice_id, nurse_id, type FROM v_shift_vitals_services_v1 ORDER BY injection_id"
    ).all()

    # «تزریق عضلانی» is not a vital, and the still-open invoice's «تست قند» has not been paid for yet.
    assert [tuple(row) for row in rows] == [
        (1, 7, "تست قند"),
        (1, 8, "کنترل فشار"),
        (3, 7, "تست قند"),
    ]


def test_registrations_report_validity_instead_of_identity(read: Connection) -> None:
    rows = read.exec_driver_sql(
        "SELECT patient_id, created_by, created_at, has_valid_nid, has_valid_phone, is_foreign "
        "FROM v_shift_patient_registrations_v1 ORDER BY patient_id"
    ).all()

    assert [tuple(row) for row in rows] == [
        (1, "reception1", "2026-09-24 07:40:00", 1, 1, 0),
        (2, "reception2", "2026-09-24 08:20:00", 0, 0, 0),
        (3, "reception1", "2026-09-24 12:05:00", 0, 0, 1),
    ]


def test_invoice_lookup_reports_status_money_and_close_time(read: Connection) -> None:
    rows = read.exec_driver_sql(
        "SELECT invoice_id, status, total, closed_at FROM v_shift_invoice_lookup_v1 ORDER BY invoice_id"
    ).all()

    assert [tuple(row) for row in rows] == [
        (1, "closed", 200_000.0, "2026-09-24 08:10:00"),
        (2, "closed", 50_000.0, "2026-09-24 09:05:00"),
        (3, "closed", 25_000.0, "2026-09-24 13:40:00"),
        (4, "closed", 999_000.0, "2026-09-24 14:20:00"),
        (5, "voided", 60_000.0, "2026-09-24 10:00:00"),
        (6, "voided", 15_000.0, "2026-09-24 11:00:00"),
        (7, "open", 70_000.0, None),
        (8, "open", 0.0, None),
    ]


def test_open_invoices_are_the_reopened_and_the_never_closed_one(
    scenario: SyntheticAccounting, read: Connection
) -> None:
    rows = read.exec_driver_sql(
        "SELECT invoice_id, opened_by, shift, work_date FROM v_shift_open_invoices_v1 ORDER BY invoice_id"
    ).all()

    assert [tuple(row) for row in rows] == [
        (scenario.reopened_invoice, "reception1", "morning", "2026-09-24"),
        (scenario.open_invoice, "reception1", "evening", "2026-09-24"),
    ]


def test_payment_lines_keep_card_and_cash_apart(read: Connection) -> None:
    total = read.exec_driver_sql("SELECT SUM(amount) FROM v_shift_payment_lines_v1").scalar_one()
    card = read.exec_driver_sql("SELECT SUM(amount) FROM v_shift_payment_lines_v1 WHERE payment_type = 'card'")

    assert total == 1_274_000.0  # 1 214 000 cash + 60 000 card
    assert card.scalar_one() == 60_000.0  # 50 000 + 10 000
