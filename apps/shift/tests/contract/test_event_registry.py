"""Contract of the event registry: every type, payload model and projection effect."""

import pytest
from pydantic import ValidationError

from event_fixtures import default_payload
from shift.domain.effects import CashOperation, Effects, StockOperation
from shift.domain.events import EVENT_SPECS, EventType, UnknownEventType, effects_for, spec_for, validate_payload

# What each event changes: the stock operations it applies, in order, and its cash operation (if any).
# A blind count changes nothing: only `count_settled` may move a shelf or the box (H-05, H-13).
EXPECTED_EFFECTS = {
    EventType.HANDOVER_BASELINE_RECORDED: ((StockOperation.SET,), CashOperation.OPEN),
    EventType.HANDOVER_STARTED: ((), CashOperation.OPEN),
    EventType.HANDOVER_CASH_COUNTED: ((), None),
    EventType.HANDOVER_ITEM_COUNTED: ((), None),
    EventType.HANDOVER_RECOUNT_REQUESTED: ((), None),
    EventType.HANDOVER_STEP_ADVANCED: ((), None),
    EventType.HANDOVER_OUTGOING_COUNTED: ((), None),
    EventType.HANDOVER_JOINT_COUNTED: ((), None),
    EventType.HANDOVER_COUNT_SETTLED: ((), CashOperation.SETTLE),
    EventType.HANDOVER_CHECKLIST_ANSWERED: ((), None),
    EventType.HANDOVER_PARCEL_HANDED: ((), None),
    EventType.HANDOVER_SIGNED: ((), None),
    EventType.HANDOVER_EARLY_LEAVE_REPORTED: ((), None),
    EventType.HANDOVER_EMERGENCY_START_CONFIRMED: ((), None),
    # H-11's two internal records: the adjustment explains a moved cash reference, and the late-cash
    # notice cancels the movement it explains (its sample is a withdrawal, so the effect is an ADD).
    EventType.HANDOVER_CASH_REFERENCE_ADJUSTED: ((), None),
    EventType.HANDOVER_LATE_CASH_RECORDED: ((), CashOperation.ADD),
    EventType.DISCREPANCY_RECORDED: ((), None),
    EventType.CASH_WITHDRAWN: ((), CashOperation.SUBTRACT),
    EventType.CASH_DEPOSITED: ((), CashOperation.SUBTRACT),
    EventType.STOCK_WEEKLY_COUNT: ((StockOperation.SET,), None),
    EventType.STOCK_SHELF_RESTOCKED: ((StockOperation.ADD,), None),
    EventType.STOCK_EMERGENCY_WITHDRAWAL: ((StockOperation.SUBTRACT,), None),
    EventType.STOCK_WASTE_REPORTED: ((StockOperation.SUBTRACT,), None),
    EventType.ACC_CASH_RECEIPT: ((), CashOperation.ADD),
    EventType.ACC_CASH_REVERSAL: ((), CashOperation.SUBTRACT),
    EventType.ACC_CONSUMPTION: ((StockOperation.SUBTRACT,), None),
    EventType.ACC_CONSUMPTION_REVERSAL: ((StockOperation.ADD,), None),
    EventType.CARD_TRANSFER_REPORTED: ((), None),
    EventType.REFUND_REQUESTED: ((), None),
    EventType.CAMPAIGN_DISCOUNT_APPLIED: ((), None),
    EventType.PATIENT_ITEM_HELD: ((), None),
    EventType.PATIENT_ITEM_DELIVERED: ((), None),
    EventType.EQUIPMENT_FAULT_REPORTED: ((), None),
    EventType.EQUIPMENT_FAULT_ASSIGNED: ((), None),
    EventType.EQUIPMENT_FAULT_FIXED: ((), None),
    EventType.VITALS_RECORDED: ((), None),
    EventType.VITALS_FOLLOWUP_OPENED: ((), None),
    EventType.CLEANING_CONFIRMED: ((), None),
    EventType.EVENT_APPROVED: ((), None),
    # F5's held-record records: keeping, releasing and refusing a record change no projection.
    EventType.EVENT_HELD: ((), None),
    EventType.EVENT_RELEASED: ((), None),
    EventType.EVENT_HOLD_REJECTED: ((), None),
    EventType.EVENT_REJECTED: ((), None),
    # ADR-021: «حضور در شیفت» is a record of who declared presence, never a movement.
    EventType.SHIFT_PRESENCE_DECLARED: ((), None),
    # SHF-5's three reward records: the ledger is derived from them on every read, so a rebuild is
    # still exactly the two projections the clinic keeps (stock, cash).
    EventType.REWARD_EARNED: ((), None),
    EventType.REWARD_CONFIRMED: ((), None),
    EventType.REWARD_REVOKED: ((), None),
    # SHF-4: the purchasing documents change no projection; the shelf charge moves both ends at once.
    EventType.ORDER_DRAFTED: ((), None),
    EventType.ORDER_DECIDED: ((), None),
    EventType.ORDER_PLACED: ((), None),
    EventType.DELIVERY_RECEIVED: ((), None),
    EventType.DELIVERY_FLAGGED: ((), None),
    EventType.DELIVERY_TRANSFERRED: ((), None),
    EventType.INVOICE_DIGITIZED: ((), None),
    EventType.PAYMENT_RECORDED: ((), None),
    EventType.VENDOR_RETURN: ((), None),
    EventType.VENDOR_CREDIT: ((), None),
    EventType.RESTOCK_CONFIRMED: ((StockOperation.SUBTRACT, StockOperation.ADD), None),
    EventType.EXPIRY_FLAGGED: ((), None),
    # B1 and G3: the receiver's count adds the parcel to the main store, the storekeeper's opening count
    # sets the whole store, and the giver's half of a shelf charge moves nothing until its confirmation.
    EventType.DELIVERY_CONFIRMED: ((StockOperation.ADD,), None),
    EventType.RESTOCK_OFFERED: ((), None),
    EventType.STOCK_STORE_BASELINED: ((StockOperation.SET,), None),
    EventType.STOCK_SAMPLE_COUNT: ((), None),
    EventType.MANAGER_REVIEWED: ((), None),
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
    stock_operations, cash_operation = EXPECTED_EFFECTS[event_type]
    assert tuple(effect.operation for effect in effects.stock) == stock_operations
    assert (None if effects.cash is None else effects.cash.operation) == cash_operation


def test_a_settled_cash_chain_rebases_the_box_and_a_joint_count_moves_it_not() -> None:
    """H-13: the agreed figure is the next shift's N0, so only the settlement rebases the projection."""
    joint = validate_payload("handover.joint_counted", {"scope": "cash", "counted": 1_200_000})
    settled = validate_payload(
        "handover.count_settled", {"scope": "cash", "counted": 1_200_000, "basis": "agreeing_blind", "carry": 0}
    )

    assert effects_for("handover.joint_counted", joint, "shift-1") == Effects()
    cash = effects_for("handover.count_settled", settled, "shift-1").cash
    assert cash is not None
    assert (cash.operation, cash.amount, cash.cash_box_id, cash.carry) == (
        CashOperation.SETTLE,
        1_200_000,
        "reception",
        0,
    )


def test_only_a_settled_count_moves_a_projection() -> None:
    """H-05: a blind count of 3 never turns a shelf of 10 into 3; the settlement writes the shelf."""
    blind = validate_payload("handover.item_counted", {"item_id": "item-1", "location_id": "shelf", "counted": 3})
    outgoing = validate_payload(
        "handover.outgoing_counted", {"item_id": "item-1", "location_id": "shelf", "counted": 3}
    )
    joint = validate_payload(
        "handover.joint_counted", {"scope": "item", "item_id": "item-1", "location_id": "shelf", "counted": 9}
    )
    settled = validate_payload(
        "handover.count_settled",
        {"scope": "item", "item_id": "item-1", "location_id": "shelf", "counted": 9, "basis": "joint"},
    )

    for event_type, payload in (
        ("handover.item_counted", blind),
        ("handover.outgoing_counted", outgoing),
        ("handover.joint_counted", joint),
    ):
        assert effects_for(event_type, payload, "shift-1") == Effects()

    effects = effects_for("handover.count_settled", settled, "shift-1")
    assert [(effect.item_id, effect.location_id, effect.operation, effect.quantity) for effect in effects.stock] == [
        ("item-1", "shelf", StockOperation.SET, 9)
    ]
    assert effects.cash is None


def test_an_item_settlement_needs_an_item_basis_and_carries_no_cash() -> None:
    settled = validate_payload(
        "handover.count_settled",
        {"scope": "item", "item_id": "item-1", "location_id": "shelf", "counted": 9, "basis": "matched"},
    )

    assert effects_for("handover.count_settled", settled, "shift-1").cash is None


def test_a_settlement_carries_every_cash_movement_of_the_flow_even_a_negative_one() -> None:
    """H-11: cash recorded during the flow stays on top of the agreed figure, a withdrawal included."""
    settled = validate_payload(
        "handover.count_settled", {"scope": "cash", "counted": 1_200_000, "basis": "joint", "carry": -20_000}
    )

    cash = effects_for("handover.count_settled", settled, "shift-1").cash

    assert cash is not None
    assert (cash.amount, cash.carry) == (1_200_000, -20_000)


@pytest.mark.parametrize("carry", [1.5, True, "1000", None])
def test_a_settlement_carry_is_a_whole_number(carry: object) -> None:
    with pytest.raises(ValidationError):
        validate_payload("handover.count_settled", {"scope": "cash", "counted": 1, "basis": "joint", "carry": carry})


def test_a_settlement_without_its_carry_is_refused() -> None:
    """A stored settlement always names what the box took in during the flow, so a rebuild can replay it."""
    with pytest.raises(ValidationError):
        validate_payload("handover.count_settled", {"scope": "cash", "counted": 1, "basis": "joint"})


@pytest.mark.parametrize(
    "payload",
    [
        # a settlement names its target, and an item one carries no cash
        {"scope": "item", "counted": 1, "basis": "joint"},
        {"scope": "item", "item_id": "item-1", "location_id": "l", "counted": 1, "basis": "joint", "carry": 0},
        # a settlement says what really happened, within its own scope
        {"scope": "item", "item_id": "item-1", "location_id": "l", "counted": 1, "basis": "agreeing_blind"},
        {"scope": "cash", "counted": 1, "basis": "matched", "carry": 0},
        {"scope": "cash", "counted": 1, "carry": 0},
        {"scope": "cash", "counted": 1, "basis": "guess", "carry": 0},
        {"scope": "cash", "item_id": "item-1", "counted": 1, "basis": "joint", "carry": 0},
    ],
)
def test_a_settlement_is_a_real_settlement_of_its_own_scope(payload: dict) -> None:
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
        # `reason` is now a real field of a withdrawal (SHF-3 «د»), so only a non-text one is invalid.
        {"amount": 1, "reason": 5},
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
        EventType.HANDOVER_BASELINE_RECORDED,
        EventType.HANDOVER_STARTED,
        EventType.HANDOVER_CASH_COUNTED,
        EventType.HANDOVER_ITEM_COUNTED,
        EventType.HANDOVER_RECOUNT_REQUESTED,
        EventType.HANDOVER_STEP_ADVANCED,
        EventType.HANDOVER_OUTGOING_COUNTED,
        EventType.HANDOVER_JOINT_COUNTED,
        EventType.HANDOVER_COUNT_SETTLED,
        EventType.HANDOVER_CHECKLIST_ANSWERED,
        EventType.HANDOVER_PARCEL_HANDED,
        EventType.HANDOVER_SIGNED,
        EventType.HANDOVER_EARLY_LEAVE_REPORTED,
        EventType.HANDOVER_EMERGENCY_START_CONFIRMED,
        EventType.HANDOVER_CASH_REFERENCE_ADJUSTED,
        EventType.HANDOVER_LATE_CASH_RECORDED,
        EventType.DISCREPANCY_RECORDED,
        EventType.CASH_WITHDRAWN,
        EventType.CASH_DEPOSITED,
        EventType.STOCK_SHELF_RESTOCKED,
        EventType.STOCK_EMERGENCY_WITHDRAWAL,
        EventType.STOCK_WASTE_REPORTED,
        EventType.ACC_CASH_RECEIPT,
        EventType.ACC_CASH_REVERSAL,
        EventType.CARD_TRANSFER_REPORTED,
        EventType.REFUND_REQUESTED,
        EventType.CAMPAIGN_DISCOUNT_APPLIED,
        EventType.PATIENT_ITEM_HELD,
        EventType.PATIENT_ITEM_DELIVERED,
        EventType.EQUIPMENT_FAULT_REPORTED,
        EventType.EQUIPMENT_FAULT_ASSIGNED,
        EventType.EQUIPMENT_FAULT_FIXED,
        EventType.VITALS_RECORDED,
        EventType.VITALS_FOLLOWUP_OPENED,
        EventType.CLEANING_CONFIRMED,
        EventType.EVENT_APPROVED,
        EventType.EVENT_REJECTED,
        EventType.EVENT_HELD,
        EventType.EVENT_RELEASED,
        EventType.EVENT_HOLD_REJECTED,
        EventType.SHIFT_PRESENCE_DECLARED,
        EventType.REWARD_EARNED,
        EventType.REWARD_CONFIRMED,
        EventType.REWARD_REVOKED,
        EventType.ORDER_DRAFTED,
        EventType.ORDER_DECIDED,
        EventType.ORDER_PLACED,
        EventType.DELIVERY_RECEIVED,
        EventType.DELIVERY_FLAGGED,
        EventType.DELIVERY_TRANSFERRED,
        EventType.INVOICE_DIGITIZED,
        EventType.PAYMENT_RECORDED,
        EventType.VENDOR_RETURN,
        EventType.VENDOR_CREDIT,
        EventType.RESTOCK_CONFIRMED,
        EventType.EXPIRY_FLAGGED,
        EventType.DELIVERY_CONFIRMED,
        EventType.RESTOCK_OFFERED,
        EventType.STOCK_STORE_BASELINED,
        EventType.STOCK_SAMPLE_COUNT,
        EventType.MANAGER_REVIEWED,
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


def test_a_baseline_sets_every_shelf_it_names_and_seeds_the_box() -> None:
    """SHF-7.1: one event sets many shelves, in the order the manager recorded them."""
    payload = validate_payload(
        "handover.baseline_recorded",
        {
            "flow": "reception",
            "custodian_id": "staff-1",
            "manager_id": "manager-1",
            "opening_cash": 1_000_000,
            "items": [
                {"item_id": "item-1", "location_id": "shelf", "quantity": 10},
                {"item_id": "item-2", "location_id": "shelf", "quantity": 0},
            ],
        },
    )

    effects = effects_for("handover.baseline_recorded", payload, "shift-1")

    assert [(effect.item_id, effect.quantity) for effect in effects.stock] == [("item-1", 10), ("item-2", 0)]
    assert effects.cash is not None
    assert (effects.cash.operation, effects.cash.amount) == (CashOperation.OPEN, 1_000_000)


def test_a_nursing_baseline_holds_no_cash() -> None:
    payload = validate_payload(
        "handover.baseline_recorded",
        {
            "flow": "nursing",
            "custodian_id": "nurse-1",
            "manager_id": "manager-1",
            "opening_cash": None,
            "items": [{"item_id": "item-1", "location_id": "cpr_room", "quantity": 4}],
        },
    )

    assert effects_for("handover.baseline_recorded", payload, "shift-1").cash is None


@pytest.mark.parametrize(
    "payload",
    [
        # the reception flow owns the box, so its baseline names the opening cash
        {
            "flow": "reception",
            "custodian_id": "s",
            "manager_id": "m",
            "opening_cash": None,
            "items": [{"item_id": "i", "location_id": "l", "quantity": 0}],
        },
        # the nursing flow holds no cash (H-12)
        {
            "flow": "nursing",
            "custodian_id": "s",
            "manager_id": "m",
            "opening_cash": 1,
            "items": [{"item_id": "i", "location_id": "l", "quantity": 0}],
        },
        # an opening count with no shelf says nothing
        {"flow": "reception", "custodian_id": "s", "manager_id": "m", "opening_cash": 1, "items": []},
        # a repeated target would leave the baseline's meaning open
        {
            "flow": "reception",
            "custodian_id": "s",
            "manager_id": "m",
            "opening_cash": 1,
            "items": [
                {"item_id": "i", "location_id": "l", "quantity": 0},
                {"item_id": "i", "location_id": "l", "quantity": 2},
            ],
        },
        # a shelf never holds a negative quantity
        {
            "flow": "reception",
            "custodian_id": "s",
            "manager_id": "m",
            "opening_cash": 1,
            "items": [{"item_id": "i", "location_id": "l", "quantity": -1}],
        },
        # unknown fields never slip into a frozen payload
        {
            "flow": "reception",
            "custodian_id": "s",
            "manager_id": "m",
            "opening_cash": 1,
            "items": [{"item_id": "i", "location_id": "l", "quantity": 0}],
            "extra": 1,
        },
        {
            "flow": "reception",
            "custodian_id": "",
            "manager_id": "m",
            "opening_cash": 1,
            "items": [{"item_id": "i", "location_id": "l", "quantity": 0}],
        },
    ],
)
def test_a_baseline_is_one_complete_opening_count(payload: dict) -> None:
    with pytest.raises(ValidationError):
        validate_payload("handover.baseline_recorded", payload)
