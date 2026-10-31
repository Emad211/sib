"""A flow's cash box: reception owns the clinic's one box, nursing has none (H-12, D2)."""

from shift.domain.cash_box import CashBox, cash_box_id_for
from shift.domain.handover_fsm import HandoverFlow


def test_reception_hands_over_the_clinic_box_and_nursing_hands_over_no_cash() -> None:
    assert cash_box_id_for(HandoverFlow.RECEPTION) == CashBox.RECEPTION == "reception"
    assert cash_box_id_for(HandoverFlow.NURSING) is None
