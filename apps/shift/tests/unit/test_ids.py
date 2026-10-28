"""ULIDs are 26 characters, time-sortable and safe to mint offline."""

from datetime import UTC, datetime

import pytest

from shift.common.ids import is_ulid, new_ulid, ulid_timestamp


def test_ulid_shape_and_timestamp() -> None:
    instant = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

    value = new_ulid(instant)

    assert len(value) == 26
    assert is_ulid(value)
    assert ulid_timestamp(value) == instant


def test_ulids_sort_by_creation_time() -> None:
    early = new_ulid(datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    late = new_ulid(datetime(2026, 10, 28, 12, 0, 1, tzinfo=UTC))

    assert early < late


def test_ulids_are_unique() -> None:
    assert len({new_ulid() for _ in range(1000)}) == 1000


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        new_ulid(datetime(2026, 10, 28))


@pytest.mark.parametrize("value", ["", "0" * 25, "I" * 26, "!" * 26, "0" * 27])
def test_invalid_ulids_are_rejected(value: str) -> None:
    assert not is_ulid(value)
    with pytest.raises(ValueError):
        ulid_timestamp(value)
