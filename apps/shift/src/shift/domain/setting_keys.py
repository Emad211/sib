"""The settings a manager may change and the bounds policy allows for each (R-08, R-09).

Kept apart from `policy` so that file stays small; `policy` re-exports these names. A setting marked
`optional` may hold no number at all: the value stored is then an empty text, which means "the manager
has not set it", and every rule that reads it makes no suggestion until a number is entered (E1, owner
decision 11, 2026-10-31).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class SettingKey(StrEnum):
    """Settings a manager may change; the values are the keys stored in the database."""

    CARD_TIME = "card_time"
    TAKEOVER_WINDOW_MINUTES = "takeover_window_minutes"
    WITHDRAWAL_CAP_TOMAN = "withdrawal_cap_toman"
    SESSION_TTL_HOURS = "session_ttl_hours"
    WEEKLY_COUNT_TIME = "weekly_count_time"
    WEEKLY_COUNT_WEEKDAY = "weekly_count_weekday"
    SUGAR_THRESHOLD = "sugar_threshold"
    SYSTOLIC_THRESHOLD = "systolic_threshold"
    DIASTOLIC_THRESHOLD = "diastolic_threshold"
    OXYGEN_PRESSURE_THRESHOLD = "oxygen_pressure_threshold"
    CASH_FLOAT_TOMAN = "cash_float_toman"


class SettingKind(StrEnum):
    """How a value is read. A `WEEKDAY` is `date.weekday()` (Monday=0 … Sunday=6), not the Persian week."""

    CLOCK_TIME = "clock_time"
    INTEGER = "integer"
    WEEKDAY = "weekday"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """The kind of value a setting takes and the bounds policy allows for it."""

    kind: SettingKind
    minimum: int | None = None
    maximum: int | None = None
    optional: bool = False


SETTING_SPECS: Mapping[SettingKey, SettingSpec] = {
    SettingKey.CARD_TIME: SettingSpec(SettingKind.CLOCK_TIME),
    SettingKey.TAKEOVER_WINDOW_MINUTES: SettingSpec(SettingKind.INTEGER, 0, 180),
    SettingKey.WITHDRAWAL_CAP_TOMAN: SettingSpec(SettingKind.INTEGER, 0, 100_000_000),
    SettingKey.SESSION_TTL_HOURS: SettingSpec(SettingKind.INTEGER, 1, 24),
    SettingKey.WEEKLY_COUNT_TIME: SettingSpec(SettingKind.CLOCK_TIME),
    SettingKey.WEEKLY_COUNT_WEEKDAY: SettingSpec(SettingKind.WEEKDAY, 0, 6),
    SettingKey.SUGAR_THRESHOLD: SettingSpec(SettingKind.INTEGER, 1, 2000, optional=True),
    SettingKey.SYSTOLIC_THRESHOLD: SettingSpec(SettingKind.INTEGER, 1, 400, optional=True),
    SettingKey.DIASTOLIC_THRESHOLD: SettingSpec(SettingKind.INTEGER, 1, 300, optional=True),
    SettingKey.OXYGEN_PRESSURE_THRESHOLD: SettingSpec(SettingKind.INTEGER, 1, 1000, optional=True),
    SettingKey.CASH_FLOAT_TOMAN: SettingSpec(SettingKind.INTEGER, 0, 100_000_000, optional=True),
}
