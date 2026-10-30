"""Contract of the event registry: every type, payload model and projection effect."""

import pytest
from pydantic import ValidationError

from event_fixtures import default_payload
from shift.domain.effects import CashOperation, StockOperation
from shift.domain.events import EVENT_SPECS, EventType, UnknownEventType, effects_for, spec_for, validate_payload

EXPECTED_EFFECTS = {
    EventType.HANDOVER_STARTED: ("cash", CashOperation.OPEN),
    EventType.HANDOVER_CASH_COUNTED: ("cash", CashOperation.COUNT),
    EventType.HANDOVER_ITEM_COUNTED: ("stock", StockOperation.SET),
    EventType.CASH_WITHDRAWN: ("cash", CashOperation.SUBTRACT),
    EventType.CASH_DEPOSITED: ("cash", CashOperation.SUBTRACT),
    EventType.STOCK_WEEKLY_COUNT: ("stock", StockOperation.SET),
    EventType.STOCK_SHELF_RESTOCKED: ("stock", StockOperation.ADD),
    EventType.STOCK_EMERGENCY_WITHDRAWAL: ("stock", StockOperation.SUBTRACT),
    EventType.STOCK_WASTE_REPORTED: ("stock", StockOperation.SUBTRACT),
    EventType.ACC_CASH_RECEIPT: ("cash", CashOperation.ADD),
    EventType.ACC_CASH_REVERSAL: ("cash", CashOperation.SUBTRACT),
    EventType.ACC_CONSUMPTION: ("stock", StockOperation.SUBTRACT),
}


def test_registry_covers_every_event_type() -> None:
    assert set(EVENT_SPECS) == set(EventType)
    assert set(EXPECTED_EFFECTS) == set(EventType)


@pytest.mark.parametrize("event_type", list(EventType))
def test_payload_and_effect_contract(event_type: EventType) -> None:
    spec = spec_for(event_type.value)
    payload = validate_payload(event_type.value, default_payload(event_type.value))
    effects = effects_for(event_type.value, payload, "shift-1" if spec.requires_shift else None)

    assert spec.schema_v == 1
    kind, operation = EXPECTED_EFFECTS[event_type]
    effect = effects.cash if kind == "cash" else effects.stock
    assert effect is not None
    assert effect.operation == operation


def test_unknown_type_has_no_spec() -> None:
    with pytest.raises(UnknownEventType, match="unknown.event"):
        spec_for("unknown.event")


@pytest.mark.parametrize(
    "payload",
    [
        {"amount": -1},
        {"amount": 1.5},
        {"amount": True},
        {"amount": 1, "extra": "x"},
        {},
        {"amount": 1, "reason": "voided"},
        {"amount": 1, "reason": "something-else"},
        {"amount": -1, "reason": "voided"},
    ],
)
def test_invalid_cash_payloads_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validate_payload("cash.withdrawn", payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"amount": 1},
        {"amount": 1, "reason": "something-else"},
        {"amount": -1, "reason": "voided"},
        {"amount": 1, "reason": "voided", "amount_raw": -0.5},
    ],
)
def test_a_reversal_needs_a_known_reason_and_a_non_negative_amount(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validate_payload("acc.cash_reversal", payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"item_id": "", "location_id": "loc", "quantity": 1},
        {"item_id": "item", "location_id": "loc", "quantity": -1},
        {"item_id": "item", "location_id": "loc"},
    ],
)
def test_invalid_stock_payloads_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validate_payload("acc.consumption", payload)


def test_cash_effects_require_a_shift() -> None:
    payload = validate_payload("cash.withdrawn", {"amount": 1000})

    with pytest.raises(ValueError, match="requires shift_id"):
        effects_for("cash.withdrawn", payload, None)


def test_shift_scoped_events_match_the_table() -> None:
    shift_types = {event_type for event_type in EventType if EVENT_SPECS[event_type].requires_shift}
    assert shift_types == {
        EventType.HANDOVER_STARTED,
        EventType.HANDOVER_CASH_COUNTED,
        EventType.HANDOVER_ITEM_COUNTED,
        EventType.CASH_WITHDRAWN,
        EventType.CASH_DEPOSITED,
        EventType.ACC_CASH_RECEIPT,
        EventType.ACC_CASH_REVERSAL,
    }
