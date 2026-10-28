"""Synthetic event factories; every value here is made up (never real patient data)."""

from datetime import UTC, date, datetime
from typing import Any

from shift.common.ids import new_ulid
from shift.services.event_store import NewEvent

WORK_DATE = date(2026, 10, 28)
CLIENT_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def default_payload(event_type: str) -> dict[str, Any]:
    """A valid minimal payload for every registered event type."""
    if event_type == "handover.started":
        return {"opening_cash": 1_000_000}
    if event_type == "handover.cash_counted":
        return {"counted": 1_000_000}
    if event_type in {"cash.withdrawn", "cash.deposited", "acc.cash_receipt"}:
        return {"amount": 100_000}
    if event_type in {"handover.item_counted", "stock.weekly_count"}:
        return {"item_id": "item-1", "location_id": "location-1", "counted": 10}
    if event_type in {
        "stock.shelf_restocked",
        "stock.emergency_withdrawal",
        "stock.waste_reported",
        "acc.consumption",
    }:
        return {"item_id": "item-1", "location_id": "location-1", "quantity": 2}
    raise ValueError(f"no default payload for {event_type}")


def build_event(
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    shift_id: str | None = "shift-1",
    actor_id: str = "actor-1",
    device_id: str = "device-1",
    client_event_id: str | None = None,
    client_at: datetime | None = None,
    work_date: date = WORK_DATE,
    ref_event_id: str | None = None,
    idempotency_key: str | None = None,
) -> NewEvent:
    """Build a NewEvent with synthetic identity and time."""
    return NewEvent(
        client_event_id=client_event_id or new_ulid(client_at or CLIENT_AT),
        type=event_type,
        payload=default_payload(event_type) if payload is None else payload,
        actor_id=actor_id,
        device_id=device_id,
        work_date=work_date,
        client_at=client_at or CLIENT_AT,
        shift_id=shift_id,
        ref_event_id=ref_event_id,
        idempotency_key=idempotency_key,
    )
