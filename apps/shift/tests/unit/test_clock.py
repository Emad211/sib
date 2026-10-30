"""Tehran is fixed UTC+03:30 all year; naive datetimes are rejected."""

from datetime import UTC, datetime, timedelta

import pytest

from shift.common.clock import (
    TEHRAN_OFFSET,
    SystemClock,
    parse_iso,
    tehran_iso,
    to_tehran,
    to_utc,
    utc_iso,
)


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now_utc()

    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)


@pytest.mark.parametrize("month", [1, 3, 6, 9, 12])
def test_tehran_offset_has_no_dst(month: int) -> None:
    instant = datetime(2026, month, 15, 12, tzinfo=UTC)

    assert to_tehran(instant).utcoffset() == TEHRAN_OFFSET


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        to_tehran(datetime(2026, 10, 28, 12, 0))


def test_iso_helpers_round_trip() -> None:
    instant = datetime(2026, 10, 28, 12, 30, 15, 123456, tzinfo=UTC)

    assert utc_iso(instant).endswith("Z")
    assert parse_iso(utc_iso(instant)) == instant
    assert tehran_iso(instant).endswith("+03:30")
    assert parse_iso(tehran_iso(instant)) == instant
    assert to_utc(parse_iso(tehran_iso(instant))) == instant
