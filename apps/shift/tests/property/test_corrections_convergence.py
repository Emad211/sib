"""Property: any sequence of re-closes and voids keeps shift cash equal to accounting (SHF-1.3).

Accounting's reopen and void routes rewrite what the cash view says. For random amount sequences the
live projection must equal the view, a re-poll must be a no-op, and a rebuild must reproduce it. Each
example gets fresh synthetic accounting and shift databases under pytest's temporary directory.
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from clocks import FixedClock
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from make_synthetic_acc_db import build

from shift.adapters.accounting.views import parse_time
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine

AMOUNT = st.integers(min_value=1, max_value=1_000_000)
FIRST_CLOSE = "2026-09-24 13:00:00"
VOIDED_AT = "2026-09-24 13:56:00"


def _run_sql(path: Path, statements: Sequence[tuple[str, Sequence[object]]]) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement, parameters in statements:
            connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


def _close(path: Path, closed_at: str, cash: int) -> int:
    connection = sqlite3.connect(path)
    try:
        cursor = connection.execute(
            "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, "
            "closed_at) VALUES (1, 'closed', 'آزاد', ?, '2026-09-24', 'morning', ?, ?)",
            (cash, closed_at, closed_at),
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
    _run_sql(
        path,
        [
            ("DELETE FROM invoice_payment_lines WHERE invoice_id = ?", (invoice_id,)),
            ("UPDATE invoices SET status = 'open', closed_at = NULL WHERE id = ?", (invoice_id,)),
        ],
    )


def _reclose(path: Path, invoice_id: int, *, closed_at: str, cash: int) -> None:
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


def _void(path: Path, invoice_id: int, *, amount: int) -> None:
    _run_sql(
        path,
        [
            (
                "INSERT INTO invoice_voids (invoice_id, kind, amount, refund_method, reason, voided_by, voided_at) "
                "VALUES (?, 'refund', ?, 'cash', 'ثبت آزمایشی', 'manager1', ?)",
                (invoice_id, amount, VOIDED_AT),
            ),
            ("UPDATE invoices SET status = 'voided' WHERE id = ?", (invoice_id,)),
        ],
    )


def _net_cash(database: Database, projections: ProjectionEngine) -> int:
    projections.sync()
    with database.read() as connection:
        return sum(int(row["expected"]) for row in projections_repo.cash_rows(connection))


def _accounting_net(path: Path, until: str) -> int:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM v_shift_cash_receipts_v1 WHERE closed_at <= ?", (until,)
        ).fetchone()
    finally:
        connection.close()
    return int(round(row[0]))


def _assert_poll_keeps_cash_equal(
    bridge: AccountingBridge,
    accounting: Path,
    database: Database,
    projections: ProjectionEngine,
    until: str,
) -> None:
    """Poll once, check the invariant and the no-op re-poll, then leave the event log applied."""
    bridge.poll(accounting)
    assert _net_cash(database, projections) == _accounting_net(accounting, until)
    again = bridge.poll(accounting)
    assert again.events == 0
    assert set(again.rows.values()) == {0}


@given(
    amounts=st.lists(AMOUNT, min_size=1, max_size=4),
    same_stamp=st.booleans(),
    void_after=st.booleans(),
)
@settings(max_examples=15, deadline=None, derandomize=True, suppress_health_check=list(HealthCheck))
def test_random_recloses_and_voids_keep_cash_equal_to_accounting(
    tmp_path: Path, amounts: list[int], same_stamp: bool, void_after: bool
) -> None:
    case = tmp_path / uuid4().hex
    case.mkdir()
    scenario = build(case / "accounting.db")
    database = Database(case / "shift.db")
    database.migrate()
    try:
        store = EventStore(database)
        projections = ProjectionEngine(database)
        bridge = AccountingBridge(database, store, clock=FixedClock(parse_time(scenario.cutoff_at)))
        invoice = _close(scenario.path, FIRST_CLOSE, amounts[0])
        _assert_poll_keeps_cash_equal(bridge, scenario.path, database, projections, scenario.cutoff_at)

        for index, amount in enumerate(amounts[1:], start=1):
            _reopen(scenario.path, invoice)
            closed_at = "2026-09-24 13:20:00" if same_stamp else f"2026-09-24 13:{20 + index * 5:02d}:00"
            _reclose(scenario.path, invoice, closed_at=closed_at, cash=amount)
            _assert_poll_keeps_cash_equal(bridge, scenario.path, database, projections, scenario.cutoff_at)

        if void_after:
            _void(scenario.path, invoice, amount=amounts[-1])
            _assert_poll_keeps_cash_equal(bridge, scenario.path, database, projections, scenario.cutoff_at)

        assert projections.rebuild(dry_run=True).drift == ()
        assert projections.rebuild().drift == ()
    finally:
        database.close()
