"""The «حضور در شیفت» record of a second receptionist or the shift doctor (ADR-021).

Taking over the box is how a receptionist or nurse comes on shift; the two extra roles of a shift
say so with this one button instead, and the manager's card shows it. No approval is involved.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from shift.api.deps import require, running_shift
from shift.api.errors import Forbidden
from shift.common.clock import utc_iso
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.permissions import Permission
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.event_store import NewEvent

router = APIRouter(tags=["on-shift"])

NOT_MY_ROLE = "این نقش برای شما ثبت نشده است."
DECLARED = "حضور شما در این شیفت ثبت شد."


class PresenceIn(BaseModel):
    """The bound role being claimed; everything else comes from the session and the clock."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["reception", "shift_doctor"]


@router.post(
    "/api/v1/shift/presence",
    status_code=201,
    summary="Declare presence on the running shift",
)
def declare_presence(
    body: PresenceIn,
    request: Request,
    principal: Annotated[Principal, Depends(require(Permission.SHIFT_PRESENCE_DECLARE))],
    shift: Annotated[ShiftKey, Depends(running_shift)],
) -> dict[str, object]:
    """Record who declared presence and when; being on shift is derived from these records."""
    if body.role not in principal.roles:
        raise Forbidden(NOT_MY_ROLE)
    stamp = request.app.state.clock.now_utc()
    request.app.state.events.append(
        NewEvent(
            client_event_id=new_ulid(),
            type=EventType.SHIFT_PRESENCE_DECLARED.value,
            payload={"role": body.role},
            actor_id=principal.staff_id,
            device_id=principal.device_id,
            shift_id=shift.text,
            work_date=shift.work_date,
            client_at=stamp,
        )
    )
    return {"role": body.role, "declared_at": utc_iso(stamp), "message": DECLARED}
