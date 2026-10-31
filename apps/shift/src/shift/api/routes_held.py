"""The manager's held records (F5): list what waits, release it, or keep it out (ADR-016).

A record whose own instant is in the future or more than three days old never moves the box or a
shelf on its own. It waits here — with the reason and the instant it claims — until the manager
releases it (the original record lands, once, with its own identity) or rejects it (it stays out of
the books for good). The numbers a held record carries are its own; no reference number appears in
any answer (R-02).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from shift.api.deps import require, running_shift
from shift.domain.permissions import Permission
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.held_events import HeldEvents, UnknownHeld
from shift.services.outbox import EventSubmission, SubmissionResult

router = APIRouter(prefix="/api/v1/manager", tags=["manager"])

Manager = Annotated[Principal, Depends(require(Permission.DAILY_CARD_VIEW))]


class HeldOut(BaseModel):
    """One held record as the manager's page reads it."""

    held_event_id: str
    event_id: str
    type: str
    reason: str
    message: str
    occurred_at: str
    received_at: str
    actor_id: str


class HeldOutcome(BaseModel):
    """What the manager's decision did: release answers with the record it let through."""

    held_event_id: str
    event_id: str
    status: str


def get_held(request: Request) -> HeldEvents:
    """The held store built in the lifespan; routes never build their own."""
    return request.app.state.held


def get_shift_events(request: Request) -> Any:
    """The shift-event service, so a released record runs exactly the path it would have run."""
    return request.app.state.shift_events


@router.get("/held-events", response_model=list[HeldOut], summary="Records waiting for a manager")
def list_held(principal: Manager, held: Annotated[HeldEvents, Depends(get_held)]) -> list[HeldOut]:
    """Every held record no manager has decided yet, oldest first."""
    return [HeldOut(**entry.to_payload()) for entry in held.pending()]


@router.post(
    "/held-events/{held_event_id}/release",
    response_model=HeldOutcome,
    summary="Let a held record through, once",
)
def release_held(
    held_event_id: str,
    principal: Manager,
    shift: Annotated[ShiftKey, Depends(running_shift)],
    held: Annotated[HeldEvents, Depends(get_held)],
    events: Annotated[Any, Depends(get_shift_events)],
) -> HeldOutcome:
    """Append the record the manager released; its movement applies now and never twice."""
    try:
        entry = held.release(
            held_event_id,
            record=lambda submission: _record(events, submission, principal, shift),
            actor=principal,
        )
    except UnknownHeld as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return HeldOutcome(held_event_id=entry.held_event_id, event_id=entry.event_id, status="released")


@router.post(
    "/held-events/{held_event_id}/reject",
    response_model=HeldOutcome,
    summary="Keep a held record out of the books",
)
def reject_held(
    held_event_id: str,
    principal: Manager,
    held: Annotated[HeldEvents, Depends(get_held)],
) -> HeldOutcome:
    """Record the manager's refusal; nothing is applied and nothing is deleted (R-03)."""
    try:
        entry = held.reject(held_event_id, actor=principal)
    except UnknownHeld as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return HeldOutcome(held_event_id=entry.held_event_id, event_id=entry.event_id, status="rejected")


def _record(events: Any, submission: EventSubmission, principal: Principal, shift: ShiftKey) -> SubmissionResult:
    """Run one released submission through the shift-event writer, skipping the hold check."""
    return events.record(submission, principal=principal, shift=shift, held_checked=True)
