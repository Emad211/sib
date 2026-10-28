"""Jalali (Solar Hijri) <-> Gregorian conversion without third-party packages.

The arithmetic follows the 33-year cycle used by the Iranian civil calendar; dates
outside 1..9999 are outside the supported range.
"""

from datetime import date

_MONTH_STARTS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def _gregorian_day_number(year: int, month: int, day: int) -> int:
    days = 365 * (year - 1600) + (year - 1600 + 3) // 4 - (year - 1600 + 99) // 100
    days += (year - 1600 + 399) // 400 + _MONTH_STARTS[month - 1] + day - 1
    if month > 2 and (year % 4 == 0 and year % 100 != 0 or year % 400 == 0):
        days += 1
    return days


def gregorian_to_jalali(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Convert a Gregorian civil date to (Jalali year, month, day)."""
    date(year, month, day)  # range validation
    days = _gregorian_day_number(year, month, day) - 79
    cycles, days = divmod(days, 12053)
    jalali_year = 979 + 33 * cycles + 4 * (days // 1461)
    days %= 1461
    if days >= 366:
        jalali_year += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jalali_year, 1 + days // 31, 1 + days % 31
    return jalali_year, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def _jalali_day_number(year: int, month: int, day: int) -> int:
    days = 365 * (year - 979) + (year - 979) // 33 * 8 + ((year - 979) % 33 + 3) // 4
    for index in range(month - 1):
        days += 31 if index < 6 else 30
    return days + day - 1


def jalali_to_gregorian(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Convert a Jalali civil date to (Gregorian year, month, day)."""
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError("invalid Jalali month or day")
    days = _jalali_day_number(year, month, day) + 79
    gregorian_year = 1600 + 400 * (days // 146097)
    days %= 146097
    leap = True
    if days >= 36525:
        days -= 1
        gregorian_year += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
        else:
            leap = False
    gregorian_year += 4 * (days // 1461)
    days %= 1461
    if days >= 366:
        leap = False
        days -= 1
        gregorian_year += days // 365
        days %= 365
    month_starts = list(_MONTH_STARTS)
    if leap:
        month_starts = [start + (1 if index >= 2 else 0) for index, start in enumerate(month_starts)]
    gregorian_month = 1
    while gregorian_month < 12 and days >= month_starts[gregorian_month]:
        gregorian_month += 1
    gregorian_day = days - month_starts[gregorian_month - 1] + 1
    result = (gregorian_year, gregorian_month, gregorian_day)
    if gregorian_to_jalali(*result) != (year, month, day):
        raise ValueError("invalid Jalali date")
    return result


def format_jalali(value: date) -> str:
    """Format a Gregorian date as `YYYY/MM/DD` in the Jalali calendar."""
    year, month, day = gregorian_to_jalali(value.year, value.month, value.day)
    return f"{year:04d}/{month:02d}/{day:02d}"


def parse_jalali(text: str) -> date:
    """Parse `YYYY/MM/DD` Jalali text into a Gregorian `date`."""
    parts = text.split("/")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Jalali date must be YYYY/MM/DD")
    return date(*jalali_to_gregorian(*(int(part) for part in parts)))
