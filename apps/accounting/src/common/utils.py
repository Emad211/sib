from datetime import datetime, timedelta, timezone
from src.common.jalali import Gregorian

# Iran is UTC+3:30
IRAN_UTC_OFFSET = timedelta(hours=3, minutes=30)
IRAN_TZ = timezone(IRAN_UTC_OFFSET)


def iran_now() -> datetime:
    """Return current Tehran time as a naive datetime.

    The app stores timestamps in the DB as Tehran local time (either via SQLite
    `datetime('now', '+3 hours', '+30 minutes')` or via Python).
    Using `datetime.utcnow() + IRAN_UTC_OFFSET` avoids dependence on the OS timezone.
    """
    return datetime.utcnow() + IRAN_UTC_OFFSET


def parse_datetime(dt: datetime | str | None) -> datetime | None:
    """
    Parse a datetime string to datetime object.
    Since database now stores Iran local time directly, no timezone conversion needed.
    Accepts datetime object or string in format 'YYYY-MM-DD HH:MM:SS'.
    """
    if dt is None:
        return None
    
    if isinstance(dt, datetime):
        return dt
    
    if isinstance(dt, str):
        if not dt or dt == '—':
            return None
        try:
            return datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                return datetime.strptime(dt, '%Y-%m-%d')
            except ValueError:
                return None
    return None


def format_jalali_datetime(dt: datetime | str | None, include_seconds: bool = False) -> str:
    """
    Format datetime as Jalali date string.
    Database stores Iran local time directly, so no timezone conversion needed.
    """
    parsed_dt = parse_datetime(dt)
    if parsed_dt is None:
        return '—'
    
    # Convert to Jalali
    jy, jm, jd = gregorian_to_jalali(parsed_dt.year, parsed_dt.month, parsed_dt.day)
    
    if include_seconds:
        return f"{jy}/{jm:02d}/{jd:02d} {parsed_dt.hour:02d}:{parsed_dt.minute:02d}:{parsed_dt.second:02d}"
    return f"{jy}/{jm:02d}/{jd:02d} {parsed_dt.hour:02d}:{parsed_dt.minute:02d}"


# Keep old function name for backward compatibility
def format_iran_datetime(dt: datetime | str | None, include_seconds: bool = False) -> str:
    """Alias for format_jalali_datetime for backward compatibility."""
    return format_jalali_datetime(dt, include_seconds)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Convert Gregorian date to Jalali (Persian) date using jalali.py."""
    g = Gregorian(gy, gm, gd)
    return g.persian_tuple()


def get_current_shift_name(reference: datetime | None = None) -> str:
    """Return the *manually selected* active shift.

    The system uses manual shift switching. Time-based boundaries are not used.
    If called during a Flask request, we read from `g.user_shift_status`.
    Otherwise we fall back to 'morning'.
    """
    try:
        from flask import has_request_context, g
        if has_request_context() and hasattr(g, 'user_shift_status') and g.user_shift_status:
            return g.user_shift_status.get('active_shift') or 'morning'
    except Exception:
        pass
    return 'morning'


def get_current_shift_window(reference: datetime | None = None, as_utc: bool = False):
    """Return (shift_name, start_datetime, end_datetime) for the *manual* shift.

    With manual shift switching, the shift window is defined as:
    - start: user's `shift_started_at`
    - end: now
    """
    now = reference or iran_now()
    shift = get_current_shift_name(now)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        from flask import has_request_context, g
        if has_request_context() and hasattr(g, 'user_shift_status') and g.user_shift_status:
            start_str = g.user_shift_status.get('shift_started_at')
            if start_str:
                parsed = parse_datetime(start_str)
                if parsed:
                    start = parsed
    except Exception:
        pass

    return shift, start, now


def get_work_date_for_datetime(dt: datetime | str | None = None) -> str:
    """Return the *manual* work date for the given datetime.

    If no datetime is provided, we return the current manual work_date from `g`.
    Otherwise, we fall back to the calendar date.
    """
    try:
        from flask import has_request_context, g
        if dt is None and has_request_context() and hasattr(g, 'user_shift_status') and g.user_shift_status:
            return g.user_shift_status.get('work_date') or iran_now().strftime('%Y-%m-%d')
    except Exception:
        pass

    if dt is None:
        dt = iran_now()
    elif isinstance(dt, str):
        dt = parse_datetime(dt) or iran_now()

    return dt.strftime('%Y-%m-%d')


def get_datetime_range_for_date_range(date_from: str, date_to: str) -> tuple[str, str]:
    """Convert a date range to a datetime range (calendar-day based).

    With manual shifts, reports should not rely on shift-time boundaries.
    We therefore interpret a date range as:
    - from: date_from 00:00:00
    - to:   (date_to + 1 day) 00:00:00
    """
    datetime_from = f"{date_from} 00:00:00"

    end_date = datetime.strptime(date_to, '%Y-%m-%d')
    next_day = end_date + timedelta(days=1)
    datetime_to = next_day.strftime('%Y-%m-%d') + ' 00:00:00'

    return datetime_from, datetime_to
