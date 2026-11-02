"""Every transition of the two handover flows (H-04, H-08, H-12) and their terminal states."""

from datetime import UTC, date, datetime

import pytest

from shift.domain.handover_formulas import HandoverError
from shift.domain.handover_fsm import (
    FLOW_STEPS,
    Handover,
    HandoverFlow,
    HandoverStep,
    Signer,
)

SHIFT = "shift-1"
WORK_DATE = date(2026, 10, 31)
STARTED_AT = datetime(2026, 10, 31, 4, 0, tzinfo=UTC)  # 07:30 Tehran


def _start(
    flow: HandoverFlow = HandoverFlow.RECEPTION,
    *,
    started_at: datetime = STARTED_AT,
    open_invoices: int = 0,
    outgoing_id: str = "staff-out",
    incoming_id: str = "staff-in",
) -> Handover:
    return Handover.start(
        flow,
        shift_id=SHIFT,
        work_date=WORK_DATE,
        started_at=started_at,
        open_invoices=open_invoices,
        outgoing_id=outgoing_id,
        incoming_id=incoming_id,
    )


def _to_dual_sign(flow: HandoverFlow = HandoverFlow.RECEPTION) -> Handover:
    handover = _start(flow)
    while handover.step is not HandoverStep.DUAL_SIGN:
        handover = handover.advance()
    return handover


def test_each_flow_hands_over_its_own_steps_in_h_04_order() -> None:
    assert FLOW_STEPS[HandoverFlow.RECEPTION] == (
        HandoverStep.CASH,
        HandoverStep.SHELF,
        HandoverStep.CHECKLISTS,
        HandoverStep.DUAL_SIGN,
    )
    assert FLOW_STEPS[HandoverFlow.NURSING] == (
        HandoverStep.CPR_ROOM,
        HandoverStep.TROLLEY,
        HandoverStep.CHECKLISTS,
        HandoverStep.DUAL_SIGN,
    )


def test_start_opens_the_flow_on_its_first_step_with_its_own_cutoff() -> None:
    handover = _start()

    assert handover.flow is HandoverFlow.RECEPTION
    assert handover.step is HandoverStep.CASH
    assert not handover.closed
    assert handover.cutoff.shift_id == SHIFT
    assert handover.cutoff.cutoff_at == STARTED_AT


def test_each_flow_freezes_its_own_cutoff_when_it_starts() -> None:
    reception = _start(started_at=datetime(2026, 10, 31, 4, 0, tzinfo=UTC))
    nursing = _start(HandoverFlow.NURSING, started_at=datetime(2026, 10, 31, 4, 30, tzinfo=UTC))

    before_both = datetime(2026, 10, 31, 3, 45, tzinfo=UTC)
    between_the_starts = datetime(2026, 10, 31, 4, 15, tzinfo=UTC)
    after_both = datetime(2026, 10, 31, 4, 45, tzinfo=UTC)

    assert reception.cutoff.covers(before_both)
    assert nursing.cutoff.covers(before_both)
    assert not reception.cutoff.covers(between_the_starts)
    assert nursing.cutoff.covers(between_the_starts)
    assert not reception.cutoff.covers(after_both)
    assert not nursing.cutoff.covers(after_both)


def test_an_open_invoice_blocks_the_start() -> None:
    with pytest.raises(HandoverError):
        _start(open_invoices=1)


@pytest.mark.parametrize(("outgoing_id", "incoming_id"), [("", "staff-in"), ("staff-out", " "), ("", "")])
def test_a_handover_starts_with_both_people_named(outgoing_id: str, incoming_id: str) -> None:
    with pytest.raises(HandoverError):
        _start(outgoing_id=outgoing_id, incoming_id=incoming_id)


@pytest.mark.parametrize("person", ["staff-a", " staff-a "])
def test_one_person_never_hands_over_to_themselves(person: str) -> None:
    with pytest.raises(HandoverError):
        _start(outgoing_id=person, incoming_id="staff-a")


@pytest.mark.parametrize("flow", list(HandoverFlow))
def test_advance_walks_the_flow_in_order_and_never_closes(flow: HandoverFlow) -> None:
    handover = _start(flow)

    for expected in FLOW_STEPS[flow][1:]:
        handover = handover.advance()
        assert handover.step is expected
        assert not handover.closed


def test_the_dual_sign_step_is_left_only_by_signing() -> None:
    with pytest.raises(HandoverError):
        _to_dual_sign().advance()


def test_a_signature_before_the_dual_sign_step_is_rejected() -> None:
    with pytest.raises(HandoverError):
        _start().sign(Signer.INCOMING)


def test_one_signature_keeps_the_handover_open_and_the_second_closes_it() -> None:
    handover = _to_dual_sign().sign(Signer.INCOMING)
    assert not handover.closed

    handover = handover.sign(Signer.OUTGOING)
    assert handover.closed
    assert handover.step is None


def test_one_person_cannot_sign_twice() -> None:
    handover = _to_dual_sign().sign(Signer.INCOMING)

    with pytest.raises(HandoverError):
        handover.sign(Signer.INCOMING)


def test_a_closed_handover_takes_no_more_transitions() -> None:
    closed = _to_dual_sign().sign(Signer.INCOMING).sign(Signer.OUTGOING)

    with pytest.raises(HandoverError):
        closed.advance()
    with pytest.raises(HandoverError):
        closed.sign(Signer.INCOMING)
    with pytest.raises(HandoverError):
        closed.report_early_leave()


def test_a_normal_handover_lands_a_discrepancy_on_the_outgoing_person() -> None:
    assert _start().culprit_id == "staff-out"


def test_the_outgoing_person_leaving_stops_the_flow_until_the_manager_confirms() -> None:
    handover = _start().report_early_leave()

    assert handover.early_left
    with pytest.raises(HandoverError):
        handover.advance()


def test_the_emergency_start_needs_a_reported_early_leave_and_a_manager() -> None:
    with pytest.raises(HandoverError):
        _start().confirm_emergency_start("manager-1")
    with pytest.raises(HandoverError):
        _start().report_early_leave().confirm_emergency_start("   ")


def test_an_early_leave_cannot_be_reported_twice_or_after_an_emergency_start() -> None:
    with pytest.raises(HandoverError):
        _start().report_early_leave().confirm_emergency_start("manager-1").report_early_leave()


def test_an_emergency_start_is_confirmed_once() -> None:
    confirmed = _start().report_early_leave().confirm_emergency_start("manager-1")

    with pytest.raises(HandoverError):
        confirmed.confirm_emergency_start("manager-2")


def test_a_closed_handover_cannot_be_confirmed_after_the_fact() -> None:
    emergency = _start().report_early_leave().confirm_emergency_start("manager-1")
    while emergency.step is not HandoverStep.DUAL_SIGN:
        emergency = emergency.advance()
    closed = emergency.sign(Signer.INCOMING)
    assert closed.closed

    with pytest.raises(HandoverError):
        closed.confirm_emergency_start("manager-2")


def test_a_confirmed_emergency_start_is_unnamed_and_needs_only_the_incoming_signature() -> None:
    handover = _start().report_early_leave().confirm_emergency_start("manager-1")
    assert handover.culprit_id is None

    handover = handover.advance()
    while handover.step is not HandoverStep.DUAL_SIGN:
        handover = handover.advance()
    with pytest.raises(HandoverError):
        handover.sign(Signer.OUTGOING)

    handover = handover.sign(Signer.INCOMING)
    assert handover.closed


def test_confirming_the_emergency_start_closes_a_handover_the_incoming_person_already_signed() -> None:
    """H-08: the incoming person signed first, the outgoing one then left without signing."""
    waiting = _to_dual_sign().sign(Signer.INCOMING).report_early_leave()
    assert not waiting.closed

    closed = waiting.confirm_emergency_start("manager-1")

    assert closed.closed
    assert closed.step is None
    assert closed.culprit_id is None
    with pytest.raises(HandoverError):
        closed.sign(Signer.OUTGOING)
