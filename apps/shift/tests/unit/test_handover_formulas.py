"""Shelf and cash references (H-01, H-02) and the discrepancy arithmetic (R-02)."""

import pytest

from shift.domain.handover_formulas import cash_reference, difference, shelf_reference


def test_shelf_reference_follows_the_h_01_formula() -> None:
    assert shelf_reference(last_count=100, restocked=20, consumed=5, emergency=2, waste=3) == 110


def test_shelf_reference_of_an_empty_shift_is_zero() -> None:
    assert shelf_reference(0, 0, 0, 0, 0) == 0


def test_cash_reference_follows_the_h_02_formula() -> None:
    assert cash_reference(opening=1_000_000, receipts=300_000, withdrawals=100_000, deposits=50_000) == 1_150_000


@pytest.mark.parametrize(
    "amounts",
    [
        (0, 0, 1, 0, 0),
        (10, 0, 0, 11, 0),
        (10, 1, 0, 0, 12),
    ],
)
def test_a_negative_shelf_reference_is_rejected_without_naming_it(amounts: tuple[int, ...]) -> None:
    with pytest.raises(ValueError) as raised:
        shelf_reference(*amounts)
    assert not any(character.isdigit() for character in str(raised.value))


def test_a_negative_cash_reference_is_rejected_without_naming_it() -> None:
    with pytest.raises(ValueError) as raised:
        cash_reference(opening=0, receipts=0, withdrawals=1, deposits=0)
    assert not any(character.isdigit() for character in str(raised.value))


@pytest.mark.parametrize(
    ("reference", "counted", "expected"),
    [(100, 90, 10), (100, 100, 0), (100, 110, -10)],
)
def test_difference_is_positive_for_a_shortage(reference: int, counted: int, expected: int) -> None:
    assert difference(reference, counted) == expected
