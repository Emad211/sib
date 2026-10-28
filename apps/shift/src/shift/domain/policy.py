"""Clinic policy a manager tunes at runtime instead of a deploy (R-08, R-09).

Only rules live here, never defaults: the values themselves are rows in `setting_versions`,
each starting at an `effective_from_shift`. A shift is identified by a `ShiftKey` whose text
form sorts chronologically, which is what lets a change made during a running shift apply
only from the next one.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta
from enum import StrEnum

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
SHIFT_RANKS = (1, 2, 3)  # morning 07:30, evening 13:30, night 19:30 (accounting schedule)
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")


class PolicyError(ValueError):
    """A value violates clinic policy; callers reject it instead of clamping it."""


class SettingKey(StrEnum):
    """Settings a manager may change; the values are the keys stored in the database."""

    CARD_TIME = "card_time"
    TAKEOVER_WINDOW_MINUTES = "takeover_window_minutes"
    WITHDRAWAL_CAP_TOMAN = "withdrawal_cap_toman"
    SESSION_TTL_HOURS = "session_ttl_hours"


class SettingKind(StrEnum):
    CLOCK_TIME = "clock_time"
    INTEGER = "integer"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """The kind of value a setting takes and the bounds policy allows for it."""

    kind: SettingKind
    minimum: int | None = None
    maximum: int | None = None


SETTING_SPECS: Mapping[SettingKey, SettingSpec] = {
    SettingKey.CARD_TIME: SettingSpec(SettingKind.CLOCK_TIME),
    SettingKey.TAKEOVER_WINDOW_MINUTES: SettingSpec(SettingKind.INTEGER, 0, 180),
    SettingKey.WITHDRAWAL_CAP_TOMAN: SettingSpec(SettingKind.INTEGER, 0, 100_000_000),
    SettingKey.SESSION_TTL_HOURS: SettingSpec(SettingKind.INTEGER, 1, 24),
}


class Role(StrEnum):
    """Staff roles (S03 §1); `purchaser` is a role, never a person (S01 §7)."""

    RECEPTION = "reception"
    NURSE = "nurse"
    STOREKEEPER = "storekeeper"
    PURCHASER = "purchaser"
    ACCOUNTANT = "accountant"
    SHIFT_DOCTOR = "shift_doctor"
    MANAGER = "manager"


def spec_for(key: SettingKey | str) -> SettingSpec:
    """Policy for one setting; an unknown key is a programming error, not a value error."""
    try:
        return SETTING_SPECS[SettingKey(key)]
    except ValueError as exc:
        raise PolicyError(f"unknown setting: {key!r}") from exc


def _clock_time(key: SettingKey | str, text: str, raw: object) -> time:
    match = _CLOCK.fullmatch(text)
    if match is None:
        raise PolicyError(f"{key}: expected a HH:MM clock time, got {raw!r}")
    try:
        return time(int(match.group(1)), int(match.group(2)))
    except ValueError as exc:
        raise PolicyError(f"{key}: not a valid clock time: {raw!r}") from exc


def parse_setting(key: SettingKey | str, raw: object) -> int | time:
    """Typed value of a setting, after Persian-digit folding and the policy bounds."""
    spec = spec_for(key)
    text = str(raw).translate(PERSIAN_DIGITS).strip()
    if spec.kind is SettingKind.CLOCK_TIME:
        return _clock_time(key, text, raw)
    try:
        number = int(text)
    except ValueError as exc:
        raise PolicyError(f"{key}: expected a whole number, got {raw!r}") from exc
    if spec.minimum is not None and number < spec.minimum:
        raise PolicyError(f"{key}: {number} is below {spec.minimum}")
    if spec.maximum is not None and number > spec.maximum:
        raise PolicyError(f"{key}: {number} is above {spec.maximum}")
    return number


def canonical_setting(key: SettingKey | str, raw: object) -> str:
    """The stored text form of a setting: stable, comparable and re-parsable."""
    value = parse_setting(key, raw)
    return f"{value.hour:02d}:{value.minute:02d}" if isinstance(value, time) else str(value)


@dataclass(frozen=True, order=True, slots=True)
class ShiftKey:
    """One shift of the clinic day, ordered exactly as it runs."""

    work_date: date
    rank: int = SHIFT_RANKS[0]

    def __post_init__(self) -> None:
        if type(self.work_date) is not date:
            raise PolicyError(f"shift work_date must be a date, got {self.work_date!r}")
        if self.rank not in SHIFT_RANKS:
            raise PolicyError(f"shift rank must be one of {SHIFT_RANKS}, got {self.rank!r}")

    @classmethod
    def parse(cls, text: str) -> "ShiftKey":
        """Read the `YYYY-MM-DD#<rank>` text stored in `effective_from_shift`."""
        day, _, rank = text.partition("#")
        try:
            return cls(date.fromisoformat(day), int(rank))
        except ValueError as exc:
            raise PolicyError(f"not a shift key: {text!r}") from exc

    @classmethod
    def baseline(cls) -> "ShiftKey":
        """The install-time seed marker; runtime changes must use `effective_from`."""
        return cls(date.min, SHIFT_RANKS[0])

    @property
    def text(self) -> str:
        """Canonical text; the ISO date comes first, so text order is chronological order."""
        return f"{self.work_date.isoformat()}#{self.rank}"

    def following(self) -> "ShiftKey":
        """The shift that starts next; a night shift rolls into the next morning."""
        if self.rank < SHIFT_RANKS[-1]:
            return ShiftKey(self.work_date, self.rank + 1)
        return ShiftKey(self.work_date + timedelta(days=1), SHIFT_RANKS[0])
