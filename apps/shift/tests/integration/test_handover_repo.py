"""Persisted handover flows: one row per flow, its own cutoff, and the D3 attribution (H-11, H-12).

A flow row is what survives a restart: the shift being closed, the flow, the instant it started (its
frozen cutoff) and its lifecycle. `shift_id_at` is the rule the accounting bridge asks for every
fact — the newest flow started at or before the fact decides, and the clinic clock only decides
before any flow exists.
"""

from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from shift.adapters.db import handover_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN, to_utc
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering

WORK_DATE = date(2026, 10, 28)
MORNING = ShiftKey(WORK_DATE, 1)
EVENING = ShiftKey(WORK_DATE, 2)
NIGHT = ShiftKey(WORK_DATE, 3)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 28, hour, minute, tzinfo=TEHRAN)


def _start(
    database: Database,
    handover_id: str,
    flow: HandoverFlow,
    *,
    shift: ShiftKey = MORNING,
    opened_at: datetime,
) -> None:
    """Persist one flow the way the handover service will."""
    with database.write() as connection:
        handover_repo.insert(
            connection,
            handover_id=handover_id,
            shift_id=shift.text,
            flow=flow,
            opened_at=opened_at,
            work_date=WORK_DATE,
            outgoing_id="staff-out",
            incoming_id="staff-in",
            created_at=opened_at,
        )


def test_a_flow_round_trips_and_its_lifecycle_is_written_in_place(database: Database) -> None:
    _start(database, "flow-reception", HandoverFlow.RECEPTION, opened_at=_at(13, 0))
    with database.read() as connection:
        stored = handover_repo.get(connection, "flow-reception")
    assert stored is not None
    assert (stored.flow, stored.shift_id, stored.opened_at) == (HandoverFlow.RECEPTION, MORNING.text, _at(13, 0))
    assert (stored.signed_at, stored.early_left, stored.emergency_by) == (None, False, None)

    with database.write() as connection:
        handover_repo.mark_early_left(connection, "flow-reception")
        handover_repo.mark_emergency(connection, "flow-reception", "manager-1")
        handover_repo.mark_signed(connection, "flow-reception", _at(13, 40))
    with database.read() as connection:
        closed = handover_repo.get(connection, "flow-reception")
    assert closed is not None
    assert (closed.early_left, closed.emergency_by, closed.signed_at) == (True, "manager-1", _at(13, 40))


def test_one_reception_and_one_nursing_flow_per_shift_and_never_a_third(database: Database) -> None:
    _start(database, "reception", HandoverFlow.RECEPTION, opened_at=_at(13, 0))
    _start(database, "nursing", HandoverFlow.NURSING, opened_at=_at(13, 5))

    with pytest.raises(IntegrityError, match="UNIQUE"):
        _start(database, "reception-again", HandoverFlow.RECEPTION, opened_at=_at(13, 10))


def test_open_flows_are_the_unsigned_ones_oldest_first(database: Database) -> None:
    _start(database, "nursing", HandoverFlow.NURSING, opened_at=_at(13, 30))
    _start(database, "reception", HandoverFlow.RECEPTION, opened_at=_at(13, 0))
    _start(database, "next-evening", HandoverFlow.RECEPTION, shift=EVENING, opened_at=_at(19, 0))
    with database.write() as connection:
        handover_repo.mark_signed(connection, "nursing", _at(13, 45))

    with database.read() as connection:
        open_ids = [flow.handover_id for flow in handover_repo.open_flows(connection)]
        latest = handover_repo.latest_flow_at_or_before(connection, _at(18, 0))

    assert open_ids == ["reception", "next-evening"]
    assert latest is not None and latest.handover_id == "nursing"


def test_a_fact_moves_to_the_next_shift_once_its_flows_cutoff_has_started(database: Database) -> None:
    _start(database, "reception", HandoverFlow.RECEPTION, opened_at=_at(13, 0))
    _start(database, "nursing", HandoverFlow.NURSING, opened_at=_at(13, 20))

    with database.read() as connection:
        # 13:10 is still inside the morning shift on the wall clock, but the reception flow started,
        # so H-11 hands the fact to the shift that follows the flow's own shift.
        assert covering(_at(13, 10)) == MORNING
        assert handover_repo.shift_id_at(connection, _at(12, 59), HandoverFlow.RECEPTION) == MORNING.text
        assert handover_repo.shift_id_at(connection, _at(13, 10), HandoverFlow.RECEPTION) == EVENING.text
        assert handover_repo.shift_id_at(connection, _at(14, 0), HandoverFlow.RECEPTION) == EVENING.text
        assert handover_repo.shift_id_at(connection, to_utc(_at(13, 10)), HandoverFlow.RECEPTION) == EVENING.text


def test_a_newer_flow_of_the_other_kind_never_moves_the_fact(database: Database) -> None:
    """D3 per flow: each kind freezes its own cutoff, so a later nursing flow leaves cash alone."""
    _start(database, "reception", HandoverFlow.RECEPTION, shift=MORNING, opened_at=_at(13, 0))
    _start(database, "nursing", HandoverFlow.NURSING, shift=EVENING, opened_at=_at(19, 0))

    with database.read() as connection:
        assert handover_repo.shift_id_at(connection, _at(19, 30), HandoverFlow.RECEPTION) == EVENING.text
        assert handover_repo.shift_id_at(connection, _at(19, 30), HandoverFlow.NURSING) == NIGHT.text
        newest = handover_repo.latest_flow_at_or_before(connection, _at(19, 30))
    assert newest is not None and newest.handover_id == "nursing"


def test_a_later_shifts_flow_moves_facts_on_again(database: Database) -> None:
    _start(database, "reception-morning", HandoverFlow.RECEPTION, opened_at=_at(13, 0))
    _start(database, "reception-evening", HandoverFlow.RECEPTION, shift=EVENING, opened_at=_at(18, 0))

    with database.read() as connection:
        assert handover_repo.shift_id_at(connection, _at(18, 10), HandoverFlow.RECEPTION) == NIGHT.text
        assert handover_repo.shift_id_at(connection, _at(17, 0), HandoverFlow.RECEPTION) == EVENING.text


def test_before_any_flow_the_clinic_clock_decides(database: Database) -> None:
    with database.read() as connection:
        assert handover_repo.shift_id_at(connection, _at(8, 0), HandoverFlow.RECEPTION) == MORNING.text
        assert handover_repo.shift_id_at(connection, _at(20, 0), HandoverFlow.NURSING) == NIGHT.text
        clock = lambda _instant: MORNING  # noqa: E731 — the injected rule, not a real clock
        assert handover_repo.shift_id_at(connection, _at(20, 0), HandoverFlow.NURSING, clock) == MORNING.text


def test_the_open_invoice_guard_reads_the_accounting_mirror(database: Database) -> None:
    with database.write() as connection:
        assert handover_repo.open_invoice_count(connection) == 0
        connection.exec_driver_sql(
            "INSERT INTO acc_open_invoice (invoice_id, opened_at, opened_by, shift, work_date, refreshed_at) "
            "VALUES (1, '2026-10-28 13:40:00', 'reception1', 'evening', '2026-10-28', '2026-10-28T00:00:00Z'), "
            "(2, '2026-10-28 13:50:00', 'reception2', 'evening', '2026-10-28', '2026-10-28T00:00:00Z')"
        )
        assert handover_repo.open_invoice_count(connection) == 2
