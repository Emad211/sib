"""The manager's initial baseline: the one count the app starts from (SHF-7.1).

The clinic does not start from old papers: the manager records what the cash box and the shelves hold
on the day the app goes live, and every later blind count compares against that figure. A baseline is
therefore not a staff handover — staff never type a declared figure into a handover route — and nothing
is recorded against anyone by it: no culprit, no discrepancy (S01 §6, owner's rule).

One event carries the whole baseline, because one act sets many shelves and seeds the box at once. The
stock side is a sequence of set effects, so the projection applies each shelf the manager recorded, in
the order they were recorded.
"""

from pydantic import model_validator

from shift.domain.handover_fsm import HandoverFlow
from shift.domain.money import Toman
from shift.domain.payloads import Identifier, Payload, Quantity


class BaselineItem(Payload):
    """One shelf at install time and what it holds; zero is a real, empty shelf."""

    item_id: Identifier
    location_id: Identifier
    quantity: Quantity


class BaselineRecorded(Payload):
    """The manager's opening count: who holds the cash box and shelves, and what they hold (H-01, H-02).

    `opening_cash` is required for the reception flow, which owns the box (H-12), and forbidden for
    nursing, which holds no cash. `items` names every target once: the event sets each shelf it
    names, and a repeated target would leave the baseline's own meaning open to interpretation.
    """

    flow: HandoverFlow
    custodian_id: Identifier
    manager_id: Identifier
    opening_cash: Toman | None
    items: tuple[BaselineItem, ...]

    @model_validator(mode="after")
    def _one_real_baseline(self) -> "BaselineRecorded":
        if not self.items:
            raise ValueError("a baseline records at least one shelf")
        targets = {(item.item_id, item.location_id) for item in self.items}
        if len(targets) != len(self.items):
            raise ValueError("a baseline names each target once")
        if self.flow is HandoverFlow.RECEPTION and self.opening_cash is None:
            raise ValueError("the reception baseline opens the cash box and needs its opening cash")
        if self.flow is HandoverFlow.NURSING and self.opening_cash is not None:
            raise ValueError("the nursing baseline opens no cash box (H-12)")
        return self
