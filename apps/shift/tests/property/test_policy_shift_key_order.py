"""A shift key's text sorts as the shift runs, and a change always starts after it (R-09)."""

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from shift.domain.policy import SHIFT_RANKS, ShiftKey, effective_from

dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31))
shift_keys = st.builds(ShiftKey, work_date=dates, rank=st.sampled_from(SHIFT_RANKS))


@given(key=shift_keys)
@settings(max_examples=200)
def test_text_round_trips_through_parse(key: ShiftKey) -> None:
    assert ShiftKey.parse(key.text) == key


@given(left=shift_keys, right=shift_keys)
@settings(max_examples=200)
def test_text_order_is_shift_order(left: ShiftKey, right: ShiftKey) -> None:
    assert (left < right) == (left.text < right.text)
    assert (left == right) == (left.text == right.text)


@given(key=shift_keys)
@settings(max_examples=100)
def test_following_is_the_nearest_later_shift(key: ShiftKey) -> None:
    same_day = [ShiftKey(key.work_date, rank) for rank in SHIFT_RANKS]
    next_day = [ShiftKey(key.work_date + timedelta(days=1), rank) for rank in SHIFT_RANKS]
    later = [candidate for candidate in same_day + next_day if candidate > key]

    assert key.following() == min(later)


@given(key=shift_keys)
@settings(max_examples=200)
def test_a_change_never_starts_during_the_running_shift(key: ShiftKey) -> None:
    assert effective_from(key) > key
