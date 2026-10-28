"""The shift cutoff: accounting facts after the handover belong to the next shift (S01 H-11, R-05).

The cutoff is frozen when the handover starts, is inclusive, and is compared as an instant, so the
same moment written in Tehran time or UTC cannot land on different sides.
"""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from make_synthetic_acc_db import SyntheticAccounting, build

from shift.adapters.accounting.views import ViewName, format_time, parse_time
from shift.adapters.db import mirror_repo, watermark_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN, to_utc
from shift.domain.cutoff import ShiftCutoff, ShiftSide
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore

WORK_DATE = date(2026, 9, 24)
CUTOFF = datetime(2026, 9, 24, 14, 0, tzinfo=TEHRAN)


def _cutoff(shift_id: str = "shift-morning", at: datetime = CUTOFF) -> ShiftCutoff:
    return ShiftCutoff(shift_id=shift_id, work_date=WORK_DATE, cutoff_at=at)


def _close_at(path: Path, closed_at: str, *, cash: float) -> None:
    """Close one more invoice in accounting at a chosen instant."""
    connection = sqlite3.connect(path)
    try:
        cursor = connection.execute(
            "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, "
            "closed_at) VALUES (1, 'closed', 'آزاد', ?, '2026-09-24', 'evening', ?, ?)",
            (cash, closed_at, closed_at),
        )
        connection.execute(
            "INSERT INTO invoice_payment_lines (invoice_id, item_type, item_id, payment_type, patient_amount, "
            "closed_at) VALUES (?, 'visit', 1, 'cash', ?, ?)",
            (int(cursor.lastrowid or 0), cash, closed_at),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def bridge(database: Database, store: EventStore) -> AccountingBridge:
    """The bridge under test."""
    return AccountingBridge(database, store)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(seconds=-1), ShiftSide.CURRENT),
        (timedelta(0), ShiftSide.CURRENT),  # the cutoff itself belongs to the shift that is ending
        (timedelta(microseconds=1), ShiftSide.NEXT),
        (timedelta(hours=5), ShiftSide.NEXT),
    ],
)
def test_the_cutoff_is_inclusive_and_one_sided(offset: timedelta, expected: ShiftSide) -> None:
    assert _cutoff().side_of(CUTOFF + offset) is expected


def test_the_same_instant_in_utc_lands_on_the_same_side() -> None:
    assert _cutoff().covers(to_utc(CUTOFF)) is True
    assert _cutoff().covers(to_utc(CUTOFF + timedelta(minutes=1))) is False


def test_a_cutoff_needs_an_aware_instant() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ShiftCutoff(shift_id="shift-1", work_date=WORK_DATE, cutoff_at=datetime(2026, 9, 24, 14, 0))


def test_a_fact_needs_an_aware_instant() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _cutoff().side_of(datetime(2026, 9, 24, 14, 0))


@pytest.mark.parametrize(
    ("shift_id", "work_date"),
    [("  ", WORK_DATE), ("shift-1", datetime(2026, 9, 24, 14, 0, tzinfo=TEHRAN))],
)
def test_a_cutoff_needs_a_shift_and_a_business_date(shift_id: str, work_date: date) -> None:
    with pytest.raises(ValueError):
        ShiftCutoff(shift_id=shift_id, work_date=work_date, cutoff_at=CUTOFF)


def test_accounting_time_round_trips_through_the_sql_text_format() -> None:
    assert format_time(CUTOFF) == "2026-09-24 14:00:00"
    assert parse_time("2026-09-24 14:00:00") == CUTOFF
    assert format_time(to_utc(CUTOFF)) == "2026-09-24 14:00:00"  # rendered in Tehran, compared as text


def test_an_invoice_closed_after_the_cutoff_is_not_in_this_shifts_cash(
    scenario: SyntheticAccounting, bridge: AccountingBridge, database: Database
) -> None:
    report = bridge.poll(scenario.path, _cutoff())

    assert report.events == 3
    with database.read() as connection:
        mirrored = {row["invoice_id"] for row in mirror_repo.rows(connection, mirror_repo.CASH)}
        watermark = watermark_repo.get(connection, ViewName.CASH_RECEIPTS.value)
    assert scenario.after_cutoff_invoice not in mirrored
    assert watermark.last_key_at == "2026-09-24 13:40:00"


def test_the_next_shift_ingests_that_invoice_under_its_own_shift_id(
    scenario: SyntheticAccounting, bridge: AccountingBridge, database: Database
) -> None:
    bridge.poll(scenario.path, _cutoff())
    evening = _cutoff(shift_id="shift-evening", at=parse_time("2026-09-24 19:30:00"))

    report = bridge.poll(scenario.path, evening)

    with database.read() as connection:
        mirrored = {row["invoice_id"] for row in mirror_repo.rows(connection, mirror_repo.CASH)}
        attributed = connection.exec_driver_sql(
            "SELECT shift_id, payload FROM events WHERE idempotency_key LIKE ?",
            (f"acc:{ViewName.CASH_RECEIPTS.value}:{scenario.after_cutoff_invoice}@%",),
        ).one()
    assert report.events == 1
    assert scenario.after_cutoff_invoice in mirrored
    assert attributed[0] == "shift-evening"
    assert "999" in attributed[1]


def test_an_invoice_closed_exactly_at_the_cutoff_stays_in_the_shift(
    scenario: SyntheticAccounting, bridge: AccountingBridge, database: Database
) -> None:
    _close_at(scenario.path, "2026-09-24 14:00:00", cash=7_000)

    report = bridge.poll(scenario.path, _cutoff())

    assert report.events == 4
    with database.read() as connection:
        assert "2026-09-24 14:00:00" in {row["closed_at"] for row in mirror_repo.rows(connection, mirror_repo.CASH)}
