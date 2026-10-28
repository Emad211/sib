"""The accounting database is only ever read, and an unreadable one degrades (R-05, R-06).

S01 R-05: the bridge opens accounting with `mode=ro`; any write must fail. S01 R-06: when accounting
is missing or broken the shift app keeps working and says so, instead of crashing.
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from make_synthetic_acc_db import SyntheticAccounting, build
from sqlalchemy.exc import OperationalError

from shift.adapters.accounting import views
from shift.adapters.accounting.readonly import AccountingUnavailable, open_accounting, ro_uri
from shift.adapters.accounting.views import parse_time
from shift.adapters.db.core import Database
from shift.domain.cutoff import ShiftCutoff
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def morning(scenario: SyntheticAccounting) -> ShiftCutoff:
    """The handover of the synthetic morning shift, which freezes the cutoff."""
    return ShiftCutoff(shift_id="shift-morning", work_date=scenario.work_date, cutoff_at=parse_time(scenario.cutoff_at))


@pytest.fixture()
def bridge(database: Database, store: EventStore) -> AccountingBridge:
    """The bridge on the shift app's own test database."""
    return AccountingBridge(database, store)


def _check_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_sql(path: Path, script: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO patients (name, family_name) VALUES ('x', 'y')",
        "UPDATE invoices SET status = 'open'",
        "DELETE FROM invoice_payment_lines",
        "CREATE TABLE sneaky (id INTEGER)",
        "DROP TABLE invoice_voids",
    ],
)
def test_the_accounting_connection_refuses_every_write(scenario: SyntheticAccounting, statement: str) -> None:
    with open_accounting(scenario.path) as database, database.connect() as connection:
        with pytest.raises(OperationalError, match="readonly"):
            connection.exec_driver_sql(statement)


def test_the_connection_is_query_only_and_points_at_the_same_file(scenario: SyntheticAccounting) -> None:
    with open_accounting(scenario.path) as database, database.connect() as connection:
        query_only = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
        opened = connection.exec_driver_sql("PRAGMA database_list").one()[2]

    assert query_only == 1
    assert Path(str(opened)).resolve() == scenario.path.resolve()


def test_the_read_only_uri_is_percent_safe() -> None:
    assert ro_uri("/tmp/a b#c%d?e.db").endswith("%20b%23c%25d%3Fe.db?mode=ro")


def test_a_path_with_spaces_and_hashes_still_opens(tmp_path: Path) -> None:
    awkward = tmp_path / "a b#c%d.db"
    scenario = build(awkward)

    with open_accounting(awkward) as database, database.connect() as connection:
        assert connection.exec_driver_sql(f"SELECT COUNT(*) FROM {views.ViewName.CASH_RECEIPTS}").scalar_one() == 4

    assert scenario.path == awkward


def test_a_missing_accounting_database_degrades_instead_of_raising(
    tmp_path: Path, bridge: AccountingBridge, morning: ShiftCutoff
) -> None:
    missing = tmp_path / "not-there.db"

    with pytest.raises(AccountingUnavailable, match="not found"):
        open_accounting(missing)
    report = bridge.poll(missing, morning)

    assert report.degraded is True
    assert report.voids_available is False
    assert report.rows == {}
    assert report.events == 0
    assert str(missing) in (report.error or "")


def test_a_file_that_is_not_a_database_degrades(tmp_path: Path, bridge: AccountingBridge, morning: ShiftCutoff) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database, it is a text file")

    with pytest.raises(AccountingUnavailable, match="not readable"):
        open_accounting(broken)
    assert bridge.poll(broken, morning).degraded is True


def test_a_poll_leaves_the_accounting_file_byte_identical(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff
) -> None:
    before = _check_digest(scenario.path)

    report = bridge.poll(scenario.path, morning)

    assert report.degraded is False
    assert report.events == 3
    assert _check_digest(scenario.path) == before


def test_an_accounting_database_without_the_voids_view_still_syncs_cash(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff
) -> None:
    _run_sql(scenario.path, "DROP VIEW v_shift_voids_v1")

    report = bridge.poll(scenario.path, morning)

    assert report.voids_available is False
    assert report.degraded is False
    assert report.rows[views.ViewName.CASH_RECEIPTS.value] == 3
    assert views.ViewName.VOIDS.value not in report.rows


def test_the_bridge_refuses_to_poll_without_a_cutoff(scenario: SyntheticAccounting, bridge: AccountingBridge) -> None:
    """Cash receipts cannot be attributed to a shift without a cutoff, so polling demands one."""
    with pytest.raises(TypeError):
        bridge.poll(scenario.path)  # type: ignore[call-arg]


def test_a_reshaped_view_degrades_its_own_feed_only(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff
) -> None:
    """`amount` renamed to `total` in `_v1` must fail the read instead of silently mirroring the wrong column."""
    _run_sql(
        scenario.path,
        "DROP VIEW v_shift_cash_receipts_v1;"
        "CREATE VIEW v_shift_cash_receipts_v1 AS "
        "SELECT invoice_id, closed_at, SUM(patient_amount) AS total FROM invoice_payment_lines "
        "WHERE payment_type = 'cash' GROUP BY invoice_id, closed_at",
    )

    report = bridge.poll(scenario.path, morning)

    assert report.degraded is True
    assert views.ViewName.CASH_RECEIPTS.value in (report.error or "")
    assert views.ViewName.CASH_RECEIPTS.value not in report.rows
    assert report.rows[views.ViewName.VITALS_SERVICES.value] == 3
    assert report.events == 0


def test_a_missing_required_view_degrades_the_poll_and_names_the_view(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff
) -> None:
    _run_sql(scenario.path, "DROP VIEW v_shift_cash_receipts_v1")

    report = bridge.poll(scenario.path, morning)

    assert report.degraded is True
    assert report.events == 0
    assert views.ViewName.CASH_RECEIPTS.value in (report.error or "")
    assert report.voids_available is True


def test_a_broken_open_invoice_snapshot_does_not_stop_the_streams(
    scenario: SyntheticAccounting, bridge: AccountingBridge, morning: ShiftCutoff
) -> None:
    _run_sql(
        scenario.path,
        "DROP VIEW v_shift_open_invoices_v1;CREATE VIEW v_shift_open_invoices_v1 AS SELECT * FROM v_shift_removed_v0",
    )

    report = bridge.poll(scenario.path, morning)

    assert report.degraded is True
    assert views.ViewName.OPEN_INVOICES.value in (report.error or "")
    assert report.rows[views.ViewName.CASH_RECEIPTS.value] == 3
    assert report.open_invoices == 0
