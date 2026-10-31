"""The purchasing chain's API (SHF-4): one read model and one write door.

The page is not a phone wizard: it shows the whole chain at once, so it asks for its own filtered state
and posts one named action at a time. Both routes are guarded only by `events.append`, exactly like the
phone's event endpoint — the *real* permission belongs to the action (`purchase.order.decide`,
`purchase.payment.record`, `stock.count.submit`, …) and the service checks it with the caller's session
before it appends anything, so one route cannot become a way around another role's limits.

Answers never carry a figure the caller may not see: the state is cut down by permission in the
service, no section carries a projected quantity (the storekeeper's weekly count is blind), and a
refusal is one short Persian message with no count and no price in it (R-02).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from shift.api.deps import require, running_shift
from shift.domain.permissions import Permission
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.purchase import PurchaseRuleError, PurchaseService

router = APIRouter(prefix="/api/v1/purchase", tags=["purchase"])

Appender = Annotated[Principal, Depends(require(Permission.EVENTS_APPEND))]


class ActionIn(BaseModel):
    """One named action of the chain and its own fields; the service validates the fields."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=64, description="order.draft, delivery.receive, …")
    data: dict[str, Any] = Field(default_factory=dict, description="The action's own fields")


class ActionOut(BaseModel):
    """What one action wrote: its status, the record it appended, and the id it minted if it did."""

    status: str
    event_id: str
    order_id: str | None = None
    delivery_id: str | None = None


def get_purchase(request: Request) -> PurchaseService:
    """The purchase service created in the lifespan (routes never build their own)."""
    return request.app.state.purchase


@router.get("/state", summary="The purchasing chain as this signed-in person may see it")
def get_purchase_state(
    principal: Appender,
    shift: Annotated[ShiftKey, Depends(running_shift)],
    service: Annotated[PurchaseService, Depends(get_purchase)],
) -> dict[str, Any]:
    """The filtered read model: orders, parcels, invoices, warehouse and the buttons to draw.

    A section the caller's roles do not open is `null`, which is not the same as an empty list: the page
    hides a section it may not see but still says "nothing here" for one that is merely empty.
    """
    return service.state(principal, shift).to_payload()


@router.post("/action", response_model=ActionOut, summary="Write one action of the purchasing chain")
def post_purchase_action(
    body: ActionIn,
    principal: Appender,
    shift: Annotated[ShiftKey, Depends(running_shift)],
    service: Annotated[PurchaseService, Depends(get_purchase)],
) -> ActionOut:
    """Append the record of one action, or answer 422 with the rule that refused it.

    Nothing is written on a refusal: the service checks the permission and the transition before the
    first append, and answers one Persian sentence for the person to read (S01 §4).
    """
    try:
        result = service.perform(principal, shift, body.action, body.data)
    except PurchaseRuleError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return ActionOut(**result)
