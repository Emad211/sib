"""Which cash box a flow's money belongs to: this clinic keeps one, and reception owns it (D2).

The cash projection keys on the box, not on the shift (ADR-017 item 8), so every cash effect names
its box. There is exactly one box today and it is a domain constant, never a payload field: a client
cannot name another box. When the clinic ever has a second box it becomes a payload field and a new
decision, and this mapping is the one place that grows.
"""

from enum import StrEnum

from shift.domain.handover_fsm import HandoverFlow


class CashBox(StrEnum):
    """The clinic's cash boxes; only reception's exists today (D2, ADR-017 item 8)."""

    RECEPTION = "reception"


def cash_box_id_for(flow: HandoverFlow) -> str | None:
    """The box one flow hands over: reception's, or none because nursing has no cash (H-12)."""
    return CashBox.RECEPTION.value if flow is HandoverFlow.RECEPTION else None
