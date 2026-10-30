"""v8 — the schedule the periodic jobs read, so no hour or weekday needs a deploy (S01 §4, S02 §2).

Additive only: three `setting_versions` rows at `version = 1`, effective from every shift, so an
upgraded database reads the same schedule as a fresh install. The defaults come from the clinic
documents: `daily_card_skip_weekday` is Friday (O04 §4, «جمعه کارت ندارد»); `weekly_count_weekday`
is Monday (S02 §2 and O04 §4, «هشدار شمارش هفتگی دوشنبه»); `weekly_count_time` is the daily card
time by owner decision 2026-10-31, because the warning rides the card that carries it, and a
manager can still change it in the settings screen.

A weekday here is a Python weekday number (`date.weekday()`: Monday=0 … Sunday=6).
"""

from sqlalchemy import Connection

BASELINE_SHIFT = "0001-01-01#1"  # `ShiftKey.baseline()`: applies to every shift ever

DEFAULTS: tuple[tuple[str, str], ...] = (
    ("daily_card_skip_weekday", "4"),  # Friday
    ("weekly_count_time", "07:30"),  # owner decision 2026-10-31: the daily card's own time
    ("weekly_count_weekday", "0"),  # Monday
)


def up(connection: Connection) -> None:
    connection.exec_driver_sql(
        "INSERT INTO setting_versions (setting_key, value, version, effective_from_shift, changed_at, changed_by) "
        "VALUES (?, ?, 1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'migration:m0008')",
        [(key, value, BASELINE_SHIFT) for key, value in DEFAULTS],
    )
