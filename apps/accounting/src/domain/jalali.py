"""Jalali calendar helpers on top of `jdatetime` (ACC-2.2). Dates cross the app boundary as
Gregorian ISO strings (`YYYY-MM-DD`, what the database stores) and Jalali `YYYY/MM/DD` strings
(what people read and type)."""

from __future__ import annotations

from datetime import date, datetime

import jdatetime

from src.domain.money import normalize_digits


def to_jalali(value: date | datetime | str | None) -> tuple[int, int, int] | None:
    """Gregorian date (or ISO string, time part ignored) -> (year, month, day); None if unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return None
    if isinstance(value, datetime):
        value = value.date()
    j = jdatetime.date.fromgregorian(date=value)
    return j.year, j.month, j.day


def jalali_text(value: date | datetime | str | None, fallback: str = "—") -> str:
    """'2026-09-24' -> '1405/07/02'."""
    parts = to_jalali(value)
    return f"{parts[0]}/{parts[1]:02d}/{parts[2]:02d}" if parts else fallback


def parse_jalali(text: str | None) -> date | None:
    """'1405/07/02' (Persian digits and '-' accepted) -> Gregorian date, None if not a real date."""
    if not text:
        return None
    parts = normalize_digits(text).strip().replace("-", "/").split("/")
    if len(parts) != 3:
        return None
    try:
        return jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2])).togregorian()
    except (ValueError, TypeError):
        return None


def month_start(value: date) -> date:
    """First day (Gregorian) of the Jalali month containing `value`."""
    j = jdatetime.date.fromgregorian(date=value)
    return jdatetime.date(j.year, j.month, 1).togregorian()


def elapsed_days_in_month(value: date) -> int:
    """How many days of the Jalali month have passed including `value` (1..31)."""
    return jdatetime.date.fromgregorian(date=value).day


def date_range(date_from: str | None, date_to: str | None, default_days: int, today: date) -> tuple[date, date]:
    """Resolve two Jalali filter strings to a (start, end) pair.

    Unreadable or missing input falls back to the last `default_days` days ending today; a reversed
    pair is swapped. This is the rule every report used (they differed only in the default span).
    """
    from datetime import timedelta

    start, end = parse_jalali(date_from), parse_jalali(date_to)
    if not start or not end:
        end = today
        start = today - timedelta(days=default_days - 1)
    if start > end:
        start, end = end, start
    return start, end
