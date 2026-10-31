"""Small writers shared by the purchasing chain and the main store's actions (S01 §4)."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from shift.common.clock import Clock
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.event_store import NewEvent
from shift.services.purchase_rules import BAD_DATA, PurchaseRuleError


def new_event(
    clock: Clock,
    event_type: EventType,
    payload: BaseModel,
    principal: Principal,
    shift: ShiftKey,
    *,
    ref_event_id: str | None = None,
) -> NewEvent:
    """One record with the session's identity and the server's own time (E-14)."""
    moment = clock.now_utc()
    return NewEvent(
        client_event_id=new_ulid(moment),
        type=event_type.value,
        payload=payload.model_dump(mode="json"),
        actor_id=principal.staff_id,
        device_id=principal.device_id,
        work_date=shift.work_date,
        client_at=moment,
        shift_id=shift.text,
        ref_event_id=ref_event_id,
    )


def body[Model: BaseModel](model: type[Model], data: Mapping[str, Any]) -> Model:
    """Validate the action's fields against its payload model, or refuse in the clinic's words."""
    try:
        return model.model_validate(dict(data))
    except ValidationError as exc:
        raise PurchaseRuleError(BAD_DATA) from exc
