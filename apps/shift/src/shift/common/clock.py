"""Tehran time is a fixed UTC+03:30 offset (Iran has had no DST since 2022)."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol

TEHRAN_OFFSET = timedelta(hours=3, minutes=30)
TEHRAN = timezone(TEHRAN_OFFSET, name="Asia/Tehran")


class Clock(Protocol):
    """Injectable time source; production uses `SystemClock`, tests use a fixed one."""

    def now_utc(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """Wall-clock implementation of `Clock`."""

    def now_utc(self) -> datetime:
        return datetime.now(UTC)


def require_aware(value: datetime) -> datetime:
    """Reject naive datetimes; mixing them with Tehran would silently shift times."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def to_tehran(value: datetime) -> datetime:
    """Convert an aware instant to the fixed Tehran offset."""
    return require_aware(value).astimezone(TEHRAN)


def to_utc(value: datetime) -> datetime:
    """Convert an aware instant to UTC."""
    return require_aware(value).astimezone(UTC)


def utc_iso(value: datetime) -> str:
    """Serialise an instant as UTC ISO-8601 with a trailing Z."""
    return to_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def tehran_iso(value: datetime) -> str:
    """Serialise an instant in Tehran local time with its explicit +03:30 offset."""
    return to_tehran(value).isoformat(timespec="microseconds")


def parse_iso(text: str) -> datetime:
    """Parse ISO-8601 text, accepting both `Z` and numeric offsets."""
    return require_aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
