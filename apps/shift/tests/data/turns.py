"""Count as whoever the flow asks for next (A4).

Every count belongs to one side: the outgoing person counts the cash box first, the incoming person counts
the shelves first. Service-level tests that only want a flow to reach a state use this instead of
juggling two callers; the tests that exercise the rule itself post as a chosen caller on purpose.
"""

from typing import Any

from shift.domain.handover_counts import CountStatus, counter_side
from shift.services.handover import Caller

RECEIPT_HASH = "a" * 64


def send_receipt(service: Any, handover_id: str, *, device_id: str = "device-1") -> None:
    """The outgoing person's card reader receipt, which the cash step wants before it can be left (ADR-034)."""
    state = service.state(handover_id)
    if state.card_receipt or state.emergency_by is not None or state.flow.value != "reception":
        return
    service.send_card_receipt(handover_id, RECEIPT_HASH, caller=Caller(actor_id=state.outgoing_id, device_id=device_id))


FAKE_JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"card-reader-receipt"


def send_receipt_api(client: Any, headers: dict[str, str], handover_id: str) -> Any:
    """Upload a receipt photo and name it on the cash step, as the outgoing person's phone (`headers`)."""
    import hashlib

    sha = hashlib.sha256(FAKE_JPEG).hexdigest()
    put = client.put(f"/api/v1/photos/{sha}", content=FAKE_JPEG, headers={**headers, "Content-Type": "image/jpeg"})
    assert put.status_code in (200, 201), put.text
    return client.post(f"/api/v1/handover/{handover_id}/card-receipt", json={"photo_sha256": sha}, headers=headers)


def count_on_turn(service: Any, handover_id: str, target: Any, counted: int, *, caller: Caller) -> CountStatus:
    """Take one count as the person whose turn it is, keeping the caller's device for the record."""
    state = service.state(handover_id)
    status = state.cash_status if target.scope == "cash" else state.item_statuses.get(str(target.item_id))
    if status is None:
        return service.count(handover_id, target, counted, caller=caller)
    if status is CountStatus.JOINT:  # the cash box opens as a joint count: both people, one figure
        if target.scope == "cash":
            send_receipt(service, handover_id, device_id=caller.device_id)
        return service.joint(handover_id, target, counted, caller=caller)
    side = counter_side(target.scope, status, alone=state.emergency_by is not None)
    who = state.outgoing_id if side == "outgoing" else state.incoming_id
    return service.count(handover_id, target, counted, caller=Caller(actor_id=who, device_id=caller.device_id))
