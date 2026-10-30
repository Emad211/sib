"""PIN policy (S4): six digits that are not a guessable pattern."""

import pytest
from hypothesis import given, strategies

from shift.domain.pin_policy import (
    LOCKOUT_MINUTES,
    MAX_FAILED_ATTEMPTS,
    PIN_LENGTH,
    PinPolicyError,
    PinProblem,
    normalise_digits,
    validate_new_pin,
)


def test_policy_constants_match_the_owner_rules() -> None:
    assert PIN_LENGTH == 6
    assert MAX_FAILED_ATTEMPTS == 5
    assert LOCKOUT_MINUTES == 15


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("۴۸۲۹۱۶", "482916"),
        ("٤٨٢٩١٦", "482916"),
        ("  482916  ", "482916"),
        ("482916", "482916"),
    ],
)
def test_persian_and_arabic_digits_are_normalised(raw: str, expected: str) -> None:
    assert normalise_digits(raw) == expected


def test_a_plain_pin_is_accepted_and_returned_normalised() -> None:
    assert validate_new_pin("۴۸۲۹۱۶") == "482916"


@pytest.mark.parametrize(
    ("pin", "problem"),
    [
        ("111111", PinProblem.REPEATED),
        ("000000", PinProblem.REPEATED),
        ("121212", PinProblem.PATTERN),
        ("123123", PinProblem.PATTERN),
        ("494949", PinProblem.PATTERN),
        ("123456", PinProblem.SEQUENCE),
        ("654321", PinProblem.SEQUENCE),
        ("012345", PinProblem.SEQUENCE),
        ("48291", PinProblem.LENGTH),
        ("4829166", PinProblem.LENGTH),
        ("", PinProblem.LENGTH),
        ("48a916", PinProblem.NOT_DIGITS),
        ("۱۳۹۸x۵", PinProblem.NOT_DIGITS),
        ("139845", PinProblem.YEAR),
        ("841391", PinProblem.YEAR),
        ("019900", PinProblem.YEAR),
    ],
)
def test_guessable_pins_name_the_rule_that_rejected_them(pin: str, problem: PinProblem) -> None:
    with pytest.raises(PinPolicyError) as caught:
        validate_new_pin(pin)
    assert caught.value.problem == problem


def test_sequence_detection_needs_the_whole_pin_to_move_by_one() -> None:
    assert validate_new_pin("112233") == "112233", "a pair run is guessable, but it is not a repetition"
    assert validate_new_pin("234561") == "234561"


@pytest.mark.parametrize("pin", ["482482", "913913", "262626", "808080"])
def test_a_pin_that_repeats_a_two_or_three_digit_block_is_refused(pin: str) -> None:
    with pytest.raises(PinPolicyError) as caught:
        validate_new_pin(pin)
    assert caught.value.problem == PinProblem.PATTERN


@given(block=strategies.from_regex(r"[0-9]{2,3}", fullmatch=True))
def test_a_pin_built_from_one_repeated_block_is_never_accepted(block: str) -> None:
    with pytest.raises(PinPolicyError):
        validate_new_pin((block * 3)[:PIN_LENGTH])


@given(digit=strategies.integers(min_value=0, max_value=9))
def test_repeated_digits_are_always_refused(digit: int) -> None:
    with pytest.raises(PinPolicyError) as caught:
        validate_new_pin(str(digit) * PIN_LENGTH)
    assert caught.value.problem == PinProblem.REPEATED


@given(start=strategies.integers(min_value=0, max_value=4), step=strategies.sampled_from([1]))
def test_rising_counting_sequences_are_always_refused(start: int, step: int) -> None:
    digits = [start + step * offset for offset in range(PIN_LENGTH)]
    with pytest.raises(PinPolicyError) as caught:
        validate_new_pin("".join(str(digit) for digit in digits))
    assert caught.value.problem == PinProblem.SEQUENCE


@pytest.mark.parametrize("pin", ["987654", "876543", "765432", "654321", "543210"])
def test_falling_counting_sequences_are_refused(pin: str) -> None:
    with pytest.raises(PinPolicyError) as caught:
        validate_new_pin(pin)
    assert caught.value.problem == PinProblem.SEQUENCE


@given(number=strategies.integers(min_value=0, max_value=999_999))
def test_accepted_pins_are_six_digits_and_stable(number: int) -> None:
    pin = f"{number:06d}"
    try:
        accepted = validate_new_pin(pin)
    except PinPolicyError:
        return
    assert accepted == pin
    assert len(accepted) == PIN_LENGTH and accepted.isdigit()
