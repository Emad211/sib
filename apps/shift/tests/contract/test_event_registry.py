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
    EventType.HANDOVER_RECOUNT_REQUESTED: None,
    EventType.HANDOVER_OUTGOING_COUNTED: ("stock", StockOperation.SET),
    EventType.HANDOVER_JOINT_COUNTED: ("stock", StockOperation.SET),
    EventType.HANDOVER_COUNT_SETTLED: ("cash", CashOperation.SETTLE),
    EventType.HANDOVER_CHECKLIST_ANSWERED: None,
    EventType.HANDOVER_PARCEL_HANDED: None,
    EventType.HANDOVER_SIGNED: None,
    EventType.HANDOVER_EARLY_LEAVE_REPORTED: None,
    EventType.HANDOVER_EMERGENCY_START_CONFIRMED: None,
    EventType.DISCREPANCY_RECORDED: None,
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
    expected = EXPECTED_EFFECTS[event_type]
    if expected is None:
        assert effects.stock is None
        assert effects.cash is None
        return
    kind, operation = expected
    effect = effects.cash if kind == "cash" else effects.stock
    assert effect is not None
    assert effect.operation == operation


def test_a_settled_cash_chain_rebases_the_box_and_a_joint_count_moves_it_not() -> None:
    """H-13: the agreed figure is the next shift's N0, so only the settlement rebases the projection."""
    joint = validate_payload("handover.joint_counted", {"scope": "cash", "counted": 1_200_000})
    settled = validate_payload(
        "handover.count_settled", {"scope": "cash", "counted": 1_200_000, "basis": "agreeing_blind"}
    )

    assert effects_for("handover.joint_counted", joint, "shift-1").cash is None
    cash = effects_for("handover.count_settled", settled, "shift-1").cash
    assert cash is not None
    assert (cash.operation, cash.amount, cash.cash_box_id) == (CashOperation.SETTLE, 1_200_000, "reception")


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "item", "item_id": "item-1", "location_id": "location-1", "counted": 1, "basis": "joint"},
        {"scope": "cash", "counted": 1},
        {"scope": "cash", "counted": 1, "basis": "guess"},
        {"scope": "cash", "item_id": "item-1", "counted": 1, "basis": "joint"},
    ],
)
def test_a_settlement_is_a_cash_chain_with_a_real_basis(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validate_payload("handover.count_settled", payload)


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
        EventType.HANDOVER_RECOUNT_REQUESTED,
        EventType.HANDOVER_OUTGOING_COUNTED,
        EventType.HANDOVER_JOINT_COUNTED,
        EventType.HANDOVER_COUNT_SETTLED,
        EventType.HANDOVER_CHECKLIST_ANSWERED,
        EventType.HANDOVER_PARCEL_HANDED,
        EventType.HANDOVER_SIGNED,
        EventType.HANDOVER_EARLY_LEAVE_REPORTED,
        EventType.HANDOVER_EMERGENCY_START_CONFIRMED,
        EventType.DISCREPANCY_RECORDED,
        EventType.CASH_WITHDRAWN,
        EventType.CASH_DEPOSITED,
        EventType.ACC_CASH_RECEIPT,
        EventType.ACC_CASH_REVERSAL,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "cash", "item_id": "item-1", "location_id": "location-1", "counted": 1},
        {"scope": "item", "counted": 1},
        {"scope": "item", "item_id": "item-1", "counted": 1},
        {"scope": "cash", "counted": -1},
    ],
)
def test_a_joint_count_names_its_target(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validate_payload("handover.joint_counted", payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "item", "item_id": "item-1", "location_id": "location-1", "difference": 0},
        {"scope": "item", "difference": 5},
        {"scope": "cash", "item_id": "item-1", "difference": 5},
        {"scope": "item", "item_id": "item-1", "location_id": "location-1", "difference": 2.5},
    ],
)
def test_a_discrepancy_names_a_real_gap(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validate_payload("discrepancy.recorded", payload)
