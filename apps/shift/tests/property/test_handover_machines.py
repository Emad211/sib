"""Machine invariants for both handover flows and both blind chains (H-04, H-05, H-13, R-02).

Steps never skip or reorder, a closed handover and a settled chain are terminal, and every
client-facing count answer is a number-free status: the reference never appears in it. A named
item gap settles only after the joint count, so no count settles an item before `JOINT` (H-05).
"""

from datetime import UTC, date, datetime

import pytest
from handover_oracle import cash_reference, shelf_reference
from hypothesis import given, settings
from hypothesis import strategies as st

from shift.domain.handover_counts import CashCount, CountStatus, ItemCount
from shift.domain.handover_formulas import difference
from shift.domain.handover_fsm import (
    FLOW_STEPS,
    Handover,
    HandoverError,
    HandoverFlow,
    HandoverStep,
    Signer,
)

FLOWS = st.sampled_from(list(HandoverFlow))
NUMBERS = st.integers(min_value=0, max_value=10_000)
COUNTS = st.lists(NUMBERS, max_size=6)

ALLOWED = {
    ("item", False): {
        CountStatus.FIRST: {CountStatus.RECOUNT, CountStatus.MATCHED},
        CountStatus.RECOUNT: {CountStatus.OUTGOING, CountStatus.MATCHED},
        CountStatus.OUTGOING: {CountStatus.JOINT, CountStatus.MATCHED},
        CountStatus.JOINT: {CountStatus.MATCHED, CountStatus.SETTLED},
    },
    ("item", True): {
        CountStatus.FIRST: {CountStatus.RECOUNT, CountStatus.MATCHED},
        CountStatus.RECOUNT: {CountStatus.MATCHED, CountStatus.SETTLED},
    },
    ("cash", False): {
        CountStatus.FIRST: {CountStatus.INCOMING},
        CountStatus.INCOMING: {CountStatus.JOINT, CountStatus.MATCHED, CountStatus.SETTLED},
        CountStatus.JOINT: {CountStatus.MATCHED, CountStatus.SETTLED},
    },
    ("cash", True): {
        CountStatus.FIRST: {CountStatus.RECOUNT, CountStatus.MATCHED},
        CountStatus.RECOUNT: {CountStatus.MATCHED, CountStatus.SETTLED},
    },
}
LIMITS = {("item", False): 4, ("item", True): 2, ("cash", False): 3, ("cash", True): 2}


def _start(flow: HandoverFlow) -> Handover:
    return Handover.start(
        flow,
        shift_id="shift-1",
        work_date=date(2026, 10, 31),
        started_at=datetime(2026, 10, 31, 4, 0, tzinfo=UTC),
        open_invoices=0,
        outgoing_id="staff-out",
        incoming_id="staff-in",
    )


def _chain(kind: str, alone: bool, reference: int) -> ItemCount | CashCount:
    culprit = None if alone else "staff-out"
    machine = ItemCount if kind == "item" else CashCount
    return machine(reference=reference, culprit_id=culprit)


@given(flow=FLOWS, advances=st.integers(min_value=0, max_value=10))
@settings(max_examples=50)
def test_steps_run_in_the_flow_order_without_skipping(flow: HandoverFlow, advances: int) -> None:
    handover = _start(flow)
    walked: list[HandoverStep | None] = [handover.step]
    for _ in range(advances):
        try:
            handover = handover.advance()
        except HandoverError:
            break
        walked.append(handover.step)

    assert walked == list(FLOW_STEPS[flow][: len(walked)])
    assert None not in walked


@given(flow=FLOWS)
@settings(max_examples=10)
def test_a_signed_handover_is_terminal(flow: HandoverFlow) -> None:
    handover = _start(flow)
    while handover.step is not HandoverStep.DUAL_SIGN:
        handover = handover.advance()
    handover = handover.sign(Signer.INCOMING).sign(Signer.OUTGOING)

    assert handover.closed
    with pytest.raises(HandoverError):
        handover.advance()
    with pytest.raises(HandoverError):
        handover.sign(Signer.INCOMING)
    with pytest.raises(HandoverError):
        handover.report_early_leave()


@given(kind=st.sampled_from(["item", "cash"]), alone=st.booleans(), reference=NUMBERS, counts=COUNTS)
@settings(max_examples=200)
def test_a_blind_chain_moves_forward_only_and_settles(
    kind: str, alone: bool, reference: int, counts: list[int]
) -> None:
    chain = _chain(kind, alone, reference)
    for counted in counts:
        if chain.settled:
            break
        before = chain.status
        chain, status = chain.count(counted)

        assert isinstance(status, CountStatus)
        assert status in ALLOWED[(kind, alone)][before]
        assert str(reference) not in status.value
        assert not any(character.isdigit() for character in status.value)
        assert not hasattr(status, "agreed")
        assert not hasattr(status, "discrepancy")

    if len(counts) >= LIMITS[(kind, alone)]:
        assert chain.settled
    if chain.settled:
        assert chain.agreed is not None
        assert (chain.discrepancy is None) == (chain.status is CountStatus.MATCHED)
        if chain.discrepancy is not None:
            assert chain.discrepancy.difference == difference(reference, chain.agreed)
            assert chain.discrepancy.culprit_id == (None if alone else "staff-out")
        with pytest.raises(HandoverError):
            chain.count(reference)


@given(
    last_count=NUMBERS,
    restocked=NUMBERS,
    consumed=NUMBERS,
    emergency=NUMBERS,
    waste=NUMBERS,
)
@settings(max_examples=200)
def test_the_shelf_reference_is_the_formula_or_a_number_free_refusal(
    last_count: int, restocked: int, consumed: int, emergency: int, waste: int
) -> None:
    expected = last_count + restocked - consumed - emergency - waste

    if expected < 0:
        with pytest.raises(ValueError) as raised:
            shelf_reference(last_count, restocked, consumed, emergency, waste)
        assert not any(character.isdigit() for character in str(raised.value))
    else:
        assert shelf_reference(last_count, restocked, consumed, emergency, waste) == expected


@given(opening=NUMBERS, receipts=NUMBERS, withdrawals=NUMBERS, deposits=NUMBERS)
@settings(max_examples=200)
def test_the_cash_reference_is_the_formula_or_a_number_free_refusal(
    opening: int, receipts: int, withdrawals: int, deposits: int
) -> None:
    expected = opening + receipts - withdrawals - deposits

    if expected < 0:
        with pytest.raises(ValueError) as raised:
            cash_reference(opening, receipts, withdrawals, deposits)
        assert not any(character.isdigit() for character in str(raised.value))
    else:
        assert cash_reference(opening, receipts, withdrawals, deposits) == expected
