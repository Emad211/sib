"""The shift cutoff: accounting facts after a flow's start belong to the next shift (S01 H-11, R-05).

`ShiftCutoff` still freezes one instant per handover and is inclusive: a row closed exactly at it is
still the shift's. The bridge itself no longer takes a cutoff: it reads up to now and asks the flows
already recorded which shift each fact belongs to, so the same fact lands in the same shift whatever
moment the poll happened to run (D3).
"""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from clocks import FixedClock
from make_synthetic_acc_db import SyntheticAccounting, build

from shift.adapters.accounting.views import ViewName, format_time, parse_time
from shift.adapters.db import handover_repo, mirror_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN, to_utc
from shift.domain.cutoff import ShiftCutoff, ShiftSide
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering
from shift.services.accounting_bridge import AccountingBridge
from shift.services.event_store import EventStore

WORK_DATE = date(2026, 9, 24)
CUTOFF = datetime(2026, 9, 24, 14, 0, tzinfo=TEHRAN)
MORNING = ShiftKey(WORK_DATE, 1)
EVENING = ShiftKey(WORK_DATE, 2)
CASH_VIEW = ViewName.CASH_RECEIPTS.value


def _cutoff(shift_id: str = "2026-09-24#1", at: datetime = CUTOFF) -> ShiftCutoff:
    return ShiftCutoff(shift_id=shift_id, work_date=WORK_DATE, cutoff_at=at)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 24, hour, minute, tzinfo=TEHRAN)


def _close_at(path: Path, closed_at: str, *, cash: float) -> int:
    """Close one more invoice in accounting at a chosen instant."""
    connection = sqlite3.connect(path)
    try:
        cursor = connection.execute(
            "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, "
            "closed_at) VALUES (1, 'closed', 'آزاد', ?, '2026-09-24', 'evening', ?, ?)",
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


def _open_flow(database: Database, *, opened_at: datetime, closing: ShiftKey = MORNING) -> None:
    """Record the reception flow that froze its cutoff at `opened_at` while closing `closing` (D1)."""
    with database.write() as connection:
        handover_repo.insert(
            connection,
            handover_id=f"flow-{opened_at.isoformat()}",
            shift_id=closing.text,
            flow=HandoverFlow.RECEPTION,
            opened_at=opened_at,
            work_date=WORK_DATE,
            outgoing_id="staff-out",
            incoming_id="staff-in",
            created_at=opened_at,
        )


def _attributed(database: Database, invoice_id: int) -> str:
    """The shift the receipt event of one invoice was stamped with."""
    with database.read() as connection:
        row = connection.exec_driver_sql(
            "SELECT shift_id FROM events WHERE idempotency_key LIKE ?",
            (f"acc:{CASH_VIEW}:{invoice_id}@%",),
        ).one()
    return str(row[0])


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def clock() -> FixedClock:
    """The bridge reads up to 14:00, the instant the synthetic morning shift hands over."""
    return FixedClock(CUTOFF)


@pytest.fixture()
def bridge(database: Database, store: EventStore, clock: FixedClock) -> AccountingBridge:
    """The bridge under test, writing into the shift app's own database."""
    return AccountingBridge(database, store, clock=clock)


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


def test_a_fact_before_a_flows_cutoff_keeps_its_shift_and_after_it_goes_to_the_next(
    scenario: SyntheticAccounting, bridge: AccountingBridge, database: Database
) -> None:
    """H-11 read literally: the flow's own start moves facts on, whatever the wall clock says."""
    before = _close_at(scenario.path, "2026-09-24 12:50:00", cash=7_000)
    after = _close_at(scenario.path, "2026-09-24 13:10:00", cash=8_000)
    _open_flow(database, opened_at=_at(13, 0))

    report = bridge.poll(scenario.path)

    assert report.degraded is False
    assert _attributed(database, before) == MORNING.text
    assert _attributed(database, after) == EVENING.text
    # 13:10 is still the morning shift on the clinic clock; the flow, not the clock, moved the fact.
    assert covering(_at(13, 10)) == MORNING


def test_a_fact_exactly_at_the_flows_cutoff_is_already_the_next_shifts(
    scenario: SyntheticAccounting, bridge: AccountingBridge, database: Database
) -> None:
    invoice = _close_at(scenario.path, "2026-09-24 13:00:00", cash=7_000)
    _open_flow(database, opened_at=_at(13, 0))

    bridge.poll(scenario.path)

    assert _attributed(database, invoice) == EVENING.text


def test_a_fact_closed_after_now_is_read_by_a_later_poll(
    scenario: SyntheticAccounting, bridge: AccountingBridge, database: Database, clock: FixedClock
) -> None:
    first = bridge.poll(scenario.path)
    with database.read() as connection:
        mirrored = {row["invoice_id"] for row in mirror_repo.rows(connection, mirror_repo.CASH)}
    clock.moment = parse_time("2026-09-24 14:30:00")

    second = bridge.poll(scenario.path)

    assert scenario.after_cutoff_invoice not in mirrored
    assert first.rows[CASH_VIEW] == 3
    assert second.events == 1
    assert _attributed(database, scenario.after_cutoff_invoice) == EVENING.text


def test_the_attribution_is_the_same_whenever_the_poll_runs(scenario: SyntheticAccounting, tmp_path: Path) -> None:
    """A poll that runs late cannot move a fact into another shift: the flows decide, not the clock."""
    invoice = _close_at(scenario.path, "2026-09-24 13:10:00", cash=8_000)
    attributed: list[str] = []
    for run, moment in enumerate(("2026-09-24 13:20:00", "2026-09-24 20:00:00")):
        database = Database(tmp_path / f"run-{run}.db")
        try:
            database.migrate()
            _open_flow(database, opened_at=_at(13, 0))
            bridge = AccountingBridge(database, EventStore(database), clock=FixedClock(parse_time(moment)))
            assert bridge.poll(scenario.path).degraded is False
            attributed.append(_attributed(database, invoice))
        finally:
            database.close()

    assert attributed == [EVENING.text, EVENING.text]
