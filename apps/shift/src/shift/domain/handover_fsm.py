"""Pure handover flow: two independent flows, each with its own cutoff, steps and signatures.

Reception hands over cash then the reception shelf; nursing hands over the CPR room then the
trolley. Neither flow waits for the other (H-12, ADR-017). Each freezes its own cutoff at the
moment it starts (H-11), walks H-04's order to a dual signature, and, when the outgoing person
has already left, can be finished alone only after the manager's phone confirmation (H-08).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from shift.domain.cutoff import ShiftCutoff
from shift.domain.handover_formulas import HandoverError


class HandoverFlow(StrEnum):
    """The two independent handovers of one shift (H-12, ADR-017)."""

    RECEPTION = "reception"
    NURSING = "nursing"


class HandoverStep(StrEnum):
    """One step of a flow; `FLOW_STEPS` holds the only order each flow may run in (H-04)."""

    CASH = "cash"
    SHELF = "shelf"
    CPR_ROOM = "cpr_room"
    TROLLEY = "trolley"
    CHECKLISTS = "checklists"
    PARCELS = "parcels"
    DUAL_SIGN = "dual_sign"


FLOW_STEPS: Mapping[HandoverFlow, tuple[HandoverStep, ...]] = {
    HandoverFlow.RECEPTION: (
        HandoverStep.CASH,
        HandoverStep.SHELF,
        HandoverStep.CHECKLISTS,
        HandoverStep.PARCELS,
        HandoverStep.DUAL_SIGN,
    ),
    HandoverFlow.NURSING: (
        HandoverStep.CPR_ROOM,
        HandoverStep.TROLLEY,
        HandoverStep.CHECKLISTS,
        HandoverStep.PARCELS,
        HandoverStep.DUAL_SIGN,
    ),
}


class Signer(StrEnum):
    """Who signs: both people normally, only the incoming one on an emergency start (H-08)."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


@dataclass(frozen=True, slots=True)
class Handover:
    """One flow of one shift: its own cutoff, its steps in order and its own signatures (H-12)."""

    flow: HandoverFlow
    cutoff: ShiftCutoff
    outgoing_id: str
    incoming_id: str
    position: int = 0
    early_left: bool = False
    emergency_by: str | None = None
    signatures: frozenset[Signer] = frozenset()

    @classmethod
    def start(
        cls,
        flow: HandoverFlow,
        *,
        shift_id: str,
        work_date: date,
        started_at: datetime,
        open_invoices: int,
        outgoing_id: str,
        incoming_id: str,
    ) -> Self:
        """Open one flow at its own cutoff; H-03 forbids starting while invoices are still open."""
        if open_invoices:
            raise HandoverError("the handover cannot start while invoices are open (H-03)")
        if not outgoing_id.strip() or not incoming_id.strip():
            raise HandoverError("a handover starts with both people named")
        return cls(flow, ShiftCutoff(shift_id, work_date, started_at), outgoing_id, incoming_id)

    @property
    def closed(self) -> bool:
        """True once every required signature is in."""
        return self.position >= len(FLOW_STEPS[self.flow])

    @property
    def step(self) -> HandoverStep | None:
        """The step the handover stands on, or None once it is closed."""
        return None if self.closed else FLOW_STEPS[self.flow][self.position]

    @property
    def culprit_id(self) -> str | None:
        """Who a discrepancy lands on; None once the emergency start is confirmed (H-08)."""
        return None if self.emergency_by else self.outgoing_id

    def advance(self) -> Self:
        """Move to the next step; the dual-sign step is left only by signing (H-04)."""
        if self.closed:
            raise HandoverError("the handover is already closed")
        if self.early_left and not self.emergency_by:
            raise HandoverError("the outgoing person left; wait for the manager's confirmation (H-08)")
        if self.step is HandoverStep.DUAL_SIGN:
            raise HandoverError("dual sign closes the handover; it cannot be skipped")
        return replace(self, position=self.position + 1)

    def report_early_leave(self) -> Self:
        """Note that the outgoing person left before handing over (H-08); nothing happens by itself."""
        if self.closed or self.emergency_by:
            raise HandoverError("the handover is past an early-leave report")
        return replace(self, early_left=True)

    def confirm_emergency_start(self, manager_id: str) -> Self:
        """Record the manager's phone confirmation that lets the incoming person finish alone (H-08)."""
        if not self.early_left:
            raise HandoverError("no early leave was reported")
        if not manager_id.strip():
            raise HandoverError("the confirming manager is required")
        return replace(self, emergency_by=manager_id)

    def sign(self, signer: Signer) -> Self:
        """Add one signature at the dual-sign step; the last required one closes the handover."""
        if self.closed or self.step is not HandoverStep.DUAL_SIGN:
            raise HandoverError("signatures belong to the dual-sign step")
        if signer is Signer.OUTGOING and self.emergency_by:
            raise HandoverError("the outgoing person is absent; the incoming one signs alone (H-08)")
        if signer in self.signatures:
            raise HandoverError("this person has already signed")
        signatures = self.signatures | {signer}
        required = {Signer.INCOMING} if self.emergency_by else set(Signer)
        return replace(
            self,
            signatures=signatures,
            position=len(FLOW_STEPS[self.flow]) if required <= signatures else self.position,
        )
