"""Every transition of the item (H-05, Q-09) and cash (H-13) blind chains, with both people and alone."""

from collections.abc import Callable

import pytest

from shift.domain.handover_counts import CashCount, CountStatus, ItemCount
from shift.domain.handover_formulas import Discrepancy, HandoverError

OUTGOING = "staff-out"


def _item(reference: int = 10) -> ItemCount:
    return ItemCount(reference=reference, culprit_id=OUTGOING)


def _cash(reference: int = 1_000) -> CashCount:
    return CashCount(reference=reference, culprit_id=OUTGOING)


def test_an_item_that_reads_the_reference_matches_on_the_first_count() -> None:
    settled, status = _item().count(10)

    assert status is CountStatus.MATCHED
    assert settled.settled
    assert settled.agreed == 10
    assert settled.discrepancy is None


def test_a_mismatched_item_asks_for_the_incoming_recount_then_the_outgoing_blind_count() -> None:
    after_first, first_status = _item().count(7)
    after_recount, recount_status = after_first.count(8)
    after_outgoing, outgoing_status = after_recount.count(9)

    assert (first_status, after_first.status) == (CountStatus.RECOUNT, CountStatus.RECOUNT)
    assert (recount_status, after_recount.status) == (CountStatus.OUTGOING, CountStatus.OUTGOING)
    assert (outgoing_status, after_outgoing.status) == (CountStatus.JOINT, CountStatus.JOINT)


def test_an_outgoing_count_that_reads_the_reference_matches_without_a_joint_count() -> None:
    after_first, _ = _item().count(7)
    after_recount, _ = after_first.count(8)
    settled, status = after_recount.count(10)

    assert status is CountStatus.MATCHED
    assert settled.agreed == 10
    assert settled.discrepancy is None


def test_a_joint_count_settles_the_gap_against_the_outgoing_person() -> None:
    after_first, _ = _item().count(7)
    after_recount, _ = after_first.count(8)
    after_outgoing, _ = after_recount.count(9)
    settled, status = after_outgoing.count(8)

    assert status is CountStatus.SETTLED
    assert settled.agreed == 8
    assert settled.discrepancy == Discrepancy(difference=2, culprit_id=OUTGOING)


def test_a_joint_count_that_reads_the_reference_still_matches() -> None:
    after_first, _ = _item().count(7)
    after_recount, _ = after_first.count(8)
    after_outgoing, _ = after_recount.count(9)
    settled, status = after_outgoing.count(10)

    assert status is CountStatus.MATCHED
    assert settled.discrepancy is None


def test_a_joint_count_above_the_reference_is_a_surplus() -> None:
    after_first, _ = _item().count(7)
    after_recount, _ = after_first.count(8)
    after_outgoing, _ = after_recount.count(9)
    settled, _ = after_outgoing.count(12)

    assert settled.discrepancy == Discrepancy(difference=-2, culprit_id=OUTGOING)


def test_a_settled_item_rejects_another_count() -> None:
    settled, _ = _item().count(10)

    with pytest.raises(HandoverError):
        settled.count(10)


def test_alone_the_incoming_person_gets_one_self_recount_and_stays_unnamed() -> None:
    alone = ItemCount(reference=10, culprit_id=None)
    after_first, first_status = alone.count(7)
    settled, second_status = after_first.count(8)

    assert first_status is CountStatus.RECOUNT
    assert second_status is CountStatus.SETTLED
    assert settled.discrepancy == Discrepancy(difference=2, culprit_id=None)


def test_alone_a_self_recount_that_reads_the_reference_matches() -> None:
    after_first, _ = ItemCount(reference=10, culprit_id=None).count(7)
    settled, status = after_first.count(10)

    assert status is CountStatus.MATCHED
    assert settled.discrepancy is None


def test_the_cash_box_waits_for_the_second_blind_count() -> None:
    after_outgoing, status = _cash().count(980)

    assert status is CountStatus.INCOMING
    assert after_outgoing.first == 980
    assert after_outgoing.agreed is None
    assert not after_outgoing.settled


def test_two_equal_blind_counts_settle_and_name_the_gap_against_the_outgoing_person() -> None:
    after_outgoing, _ = _cash().count(980)
    settled, status = after_outgoing.count(980)

    assert status is CountStatus.SETTLED
    assert settled.agreed == 980
    assert settled.discrepancy == Discrepancy(difference=20, culprit_id=OUTGOING)


def test_two_equal_blind_counts_on_the_reference_match() -> None:
    after_outgoing, _ = _cash().count(1_000)
    settled, status = after_outgoing.count(1_000)

    assert status is CountStatus.MATCHED
    assert settled.discrepancy is None


def test_different_blind_counts_need_one_joint_recount() -> None:
    after_outgoing, _ = _cash().count(980)
    after_incoming, mismatched = after_outgoing.count(990)
    settled, final_status = after_incoming.count(970)

    assert (mismatched, after_incoming.status) == (CountStatus.JOINT, CountStatus.JOINT)
    assert final_status is CountStatus.SETTLED
    assert settled.agreed == 970
    assert settled.discrepancy == Discrepancy(difference=30, culprit_id=OUTGOING)


def test_a_joint_cash_count_on_the_reference_matches() -> None:
    after_outgoing, _ = _cash().count(980)
    after_incoming, _ = after_outgoing.count(990)
    settled, status = after_incoming.count(1_000)

    assert status is CountStatus.MATCHED
    assert settled.discrepancy is None


def test_a_settled_cash_box_rejects_another_count() -> None:
    after_outgoing, _ = _cash().count(1_000)
    settled, _ = after_outgoing.count(1_000)

    with pytest.raises(HandoverError):
        settled.count(1_000)


def test_alone_the_cash_box_gets_one_self_recount_and_stays_unnamed() -> None:
    alone = CashCount(reference=1_000, culprit_id=None)
    after_first, first_status = alone.count(900)
    settled, second_status = after_first.count(910)

    assert first_status is CountStatus.RECOUNT
    assert second_status is CountStatus.SETTLED
    assert settled.discrepancy == Discrepancy(difference=90, culprit_id=None)


def test_alone_a_cash_self_recount_that_reads_the_reference_matches() -> None:
    after_first, _ = CashCount(reference=1_000, culprit_id=None).count(900)
    settled, status = after_first.count(1_000)

    assert status is CountStatus.MATCHED
    assert settled.discrepancy is None


@pytest.mark.parametrize("new_chain", [_item, _cash])
@pytest.mark.parametrize("counted", [-1, -500_000])
def test_a_count_is_never_negative(new_chain: Callable[[], ItemCount | CashCount], counted: int) -> None:
    with pytest.raises(HandoverError):
        new_chain().count(counted)


@pytest.mark.parametrize("new_chain", [_item, _cash])
def test_a_negative_count_is_refused_once_the_chain_is_underway(
    new_chain: Callable[[], ItemCount | CashCount],
) -> None:
    started = new_chain()
    underway, _ = started.count(started.reference - 1)

    with pytest.raises(HandoverError):
        underway.count(-1)


@pytest.mark.parametrize(
    "alone",
    [ItemCount(reference=10, culprit_id=None), CashCount(reference=1_000, culprit_id=None)],
    ids=["item", "cash"],
)
def test_a_lone_counter_is_never_asked_for_a_negative_figure(
    alone: ItemCount | CashCount,
) -> None:
    with pytest.raises(HandoverError):
        alone.count(-1)
