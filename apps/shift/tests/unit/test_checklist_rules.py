"""Which flow's incoming person answers for each role's checklist lines (ADR-035)."""

from shift.domain.checklist_rules import asked_in_flow
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import Role


def test_reception_answers_for_reception_and_the_store_and_nursing_for_nurses() -> None:
    assert asked_in_flow(Role.RECEPTION, HandoverFlow.RECEPTION)
    assert asked_in_flow(Role.STOREKEEPER, HandoverFlow.RECEPTION)
    assert asked_in_flow(Role.NURSE, HandoverFlow.NURSING)


def test_no_line_is_asked_in_the_other_flow_and_the_manager_owns_none() -> None:
    assert not asked_in_flow(Role.NURSE, HandoverFlow.RECEPTION)
    assert not asked_in_flow(Role.RECEPTION, HandoverFlow.NURSING)
    assert not asked_in_flow(Role.STOREKEEPER, HandoverFlow.NURSING)
    assert not asked_in_flow(Role.MANAGER, HandoverFlow.RECEPTION)
    assert not asked_in_flow(Role.MANAGER, HandoverFlow.NURSING)
