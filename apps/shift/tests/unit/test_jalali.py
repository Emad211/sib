"""Jalali conversion: known dates, full-range round trips and invalid input."""

from datetime import date, timedelta

import pytest

from shift.common.jalali import format_jalali, gregorian_to_jalali, jalali_to_gregorian, parse_jalali


@pytest.mark.parametrize(
    ("gregorian", "jalali"),
    [
        (date(2026, 10, 26), (1405, 8, 4)),
        (date(2024, 3, 20), (1403, 1, 1)),
        (date(2025, 3, 21), (1404, 1, 1)),
        (date(2024, 2, 29), (1402, 12, 10)),
    ],
)
def test_known_dates(gregorian: date, jalali: tuple[int, int, int]) -> None:
    assert gregorian_to_jalali(gregorian.year, gregorian.month, gregorian.day) == jalali
    assert jalali_to_gregorian(*jalali) == (gregorian.year, gregorian.month, gregorian.day)


def test_every_day_round_trips_for_a_decade() -> None:
    current = date(2020, 1, 1)
    end = date(2030, 12, 31)
    while current <= end:
        jalali = gregorian_to_jalali(current.year, current.month, current.day)
        assert jalali_to_gregorian(*jalali) == (current.year, current.month, current.day)
        current += timedelta(days=1)


def test_format_and_parse() -> None:
    assert format_jalali(date(2026, 10, 26)) == "1405/08/04"
    assert parse_jalali("1405/08/04") == date(2026, 10, 26)


@pytest.mark.parametrize("text", ["1405-08-04", "1405/8", "1405/08/xx", ""])
def test_parse_rejects_malformed_text(text: str) -> None:
    with pytest.raises(ValueError):
        parse_jalali(text)


@pytest.mark.parametrize(("year", "month", "day"), [(1405, 13, 1), (1405, 0, 1), (1405, 12, 31), (1405, 8, 32)])
def test_invalid_jalali_dates_are_rejected(year: int, month: int, day: int) -> None:
    with pytest.raises(ValueError):
        jalali_to_gregorian(year, month, day)
