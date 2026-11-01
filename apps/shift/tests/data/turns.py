"""Count as whoever the flow asks for next (A4).

Every count belongs to one side: the outgoing person counts the cash box first, the incoming person counts
the shelves first. Service-level tests that only want a flow to reach a state use this instead of
juggling two callers; the tests that exercise the rule itself post as a chosen caller on purpose.
"""

from typing import Any

from shift.domain.handover_counts import CountStatus, counter_side
from shift.services.handover import Caller


def count_on_turn(service: Any, handover_id: str, target: Any, counted: int, *, caller: Caller) -> CountStatus:
    """Take one count as the person whose turn it is, keeping the caller's device for the record."""
    state = service.state(handover_id)
    status = state.cash_status if target.scope == "cash" else state.item_statuses.get(str(target.item_id))
    if status is None:
        return service.count(handover_id, target, counted, caller=caller)
    side = counter_side(target.scope, status, alone=state.emergency_by is not None)
    who = state.outgoing_id if side == "outgoing" else state.incoming_id
    return service.count(handover_id, target, counted, caller=Caller(actor_id=who, device_id=caller.device_id))
