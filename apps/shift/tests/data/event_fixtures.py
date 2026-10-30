"""Synthetic event factories; every value here is made up (never real patient data)."""

import random
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
    if event_type == "acc.cash_reversal":
        return {"amount": 100_000, "reason": "voided"}
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


def random_events(seed: int, count: int = 10_000, key_prefix: str = "event") -> list[NewEvent]:
    """A deterministic random mix of handover, cash and stock events for property tests."""
    if count < 1:
        return []
    rng = random.Random(seed)
    shifts = [f"shift-{index}" for index in range(20)]
    items = [f"item-{index}" for index in range(8)]
    locations = [f"location-{index}" for index in range(3)]
    events: list[NewEvent] = []
    for index, shift in enumerate(shifts):
        events.append(
            build_event(
                "handover.started",
                shift_id=shift,
                payload={"opening_cash": rng.randrange(0, 5_000_001)},
                idempotency_key=f"{key_prefix}-open-{index}",
            )
        )
    kinds = [
        ("acc.cash_receipt", "cash", 22),
        ("acc.cash_reversal", "cash", 5),
        ("cash.withdrawn", "cash", 10),
        ("cash.deposited", "cash", 10),
        ("handover.cash_counted", "cash", 10),
        ("handover.item_counted", "count", 10),
        ("stock.shelf_restocked", "stock", 10),
        ("acc.consumption", "stock", 10),
        ("stock.emergency_withdrawal", "stock", 5),
        ("stock.waste_reported", "stock", 5),
        ("stock.weekly_count", "count", 8),
    ]
    weights = [weight for _, _, weight in kinds]
    while len(events) < count:
        event_type, shape, _ = rng.choices(kinds, weights=weights, k=1)[0]
        shift = rng.choice(shifts)
        item = rng.choice(items)
        location = rng.choice(locations)
        if shape == "cash":
            payload: dict[str, Any] = {"amount": rng.randrange(0, 500_001)}
            if event_type == "handover.cash_counted":
                payload = {"counted": rng.randrange(0, 5_000_001)}
            elif event_type == "acc.cash_reversal":
                payload = {"amount": payload["amount"], "reason": "reclosed"}
        elif shape == "count":
            payload = {"item_id": item, "location_id": location, "counted": rng.randrange(0, 50)}
        else:
            payload = {"item_id": item, "location_id": location, "quantity": rng.randrange(0, 20)}
        events.append(
            build_event(
                event_type,
                shift_id=shift,
                payload=payload,
                idempotency_key=f"{key_prefix}-event-{len(events)}",
            )
        )
    return events[:count]
