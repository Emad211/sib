"""Versioned clinic settings: a read resolves by shift, a change starts at the next shift (R-09)."""

from dataclasses import dataclass
from datetime import datetime, time

from shift.adapters.db import settings_repo
from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, to_tehran, utc_iso
from shift.domain.policy import (
    PolicyError,
    SettingKey,
    ShiftKey,
    canonical_setting,
    parse_setting,
    spec_for,
)
from shift.domain.shift_clock import covering, effective_from, require_monotonic


class SettingError(RuntimeError):
    """A known setting has no row; the migration seeds every key, so this is a broken install."""


@dataclass(frozen=True, slots=True)
class SettingValue:
    """One setting as a shift sees it, plus who set the version that answered."""

    key: SettingKey
    value: str
    version: int
    effective_from_shift: ShiftKey
    changed_at: str
    changed_by: str


@dataclass(frozen=True, slots=True)
class SettingChange:
    """What a `change` produced: the version now carrying the value, and whether it is new."""

    value: SettingValue
    created: bool


def _key(key: SettingKey | str) -> SettingKey:
    """A known setting key; `spec_for` raises `PolicyError` for anything else."""
    spec_for(key)
    return SettingKey(key)


def _value(row: settings_repo.SettingRow) -> SettingValue:
    return SettingValue(
        key=SettingKey(row.setting_key),
        value=row.value,
        version=row.version,
        effective_from_shift=ShiftKey.parse(row.effective_from_shift),
        changed_at=row.changed_at,
        changed_by=row.changed_by,
    )


class SettingsService:
    """Reads settings for a shift and appends versions that only later shifts can see."""

    def __init__(self, database: Database, *, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or SystemClock()

    def _tehran_now(self) -> datetime:
        return to_tehran(self._clock.now_utc())

    def _read_shift(self, shift: ShiftKey | None) -> ShiftKey:
        """The shift a caller asked about, or the one the clock says has started."""
        return shift if shift is not None else covering(self._tehran_now())

    def snapshot(self, shift: ShiftKey | None = None) -> dict[SettingKey, SettingValue]:
        """Every current setting as `shift` sees it; with no shift, as the clock's shift sees it.

        A stored row whose key is no longer a policy key is history, not an option: it stays in
        `setting_versions` (R-03) and is ignored here, e.g. `daily_card_skip_weekday` after ADR-018.
        """
        at_shift = self._read_shift(shift).text
        with self._database.read() as connection:
            rows = settings_repo.list_effective(connection, at_shift)
        known = {key.value for key in SettingKey}
        values = {SettingKey(row.setting_key): _value(row) for row in rows if row.setting_key in known}
        missing = [key.value for key in SettingKey if key not in values]
        if missing:
            raise SettingError(f"settings missing from the database: {missing}")
        return values

    def value(self, key: SettingKey | str, shift: ShiftKey | None = None) -> SettingValue:
        """One setting as `shift` sees it."""
        resolved = _key(key)
        at_shift = self._read_shift(shift).text
        with self._database.read() as connection:
            row = settings_repo.find_effective(connection, resolved.value, at_shift)
        if row is None:
            raise SettingError(f"{resolved.value} has no version at or before {at_shift}")
        return _value(row)

    def number(self, key: SettingKey | str, shift: ShiftKey | None = None) -> int:
        """A whole-number setting: minutes, Toman or hours."""
        parsed = parse_setting(key, self.value(key, shift).value)
        if not isinstance(parsed, int):
            raise PolicyError(f"{key} is not a whole number")
        return parsed

    def clock_time(self, key: SettingKey | str, shift: ShiftKey | None = None) -> time:
        """A clock-time setting, such as the daily card time."""
        parsed = parse_setting(key, self.value(key, shift).value)
        if not isinstance(parsed, time):
            raise PolicyError(f"{key} is not a clock time")
        return parsed

    def change(
        self,
        key: SettingKey | str,
        raw_value: object,
        *,
        actor_id: str,
        running_shift: ShiftKey | None = None,
    ) -> SettingChange:
        """Append a version effective from the shift after `running_shift`, never during it.

        With no running shift the target comes from the Tehran clock, and a change can never
        start before the newest version. Repeating the value already stored changes nothing
        and reports `created=False`, which keeps install scripts and retries idempotent.
        """
        resolved = _key(key)
        actor = actor_id.strip()
        if not actor:
            raise PolicyError("actor_id is required")
        canonical = canonical_setting(resolved, raw_value)
        with self._database.write() as connection:
            latest = settings_repo.find_latest(connection, resolved.value)
            if latest is not None and latest.value == canonical:
                return SettingChange(_value(latest), created=False)
            newest = None if latest is None else ShiftKey.parse(latest.effective_from_shift)
            now = self._clock.now_utc()
            target = require_monotonic(effective_from(running_shift, now=to_tehran(now)), newest)
            version = settings_repo.next_version(connection, resolved.value)
            changed_at = utc_iso(now)
            settings_repo.insert_version(
                connection,
                setting_key=resolved.value,
                value=canonical,
                version=version,
                effective_from_shift=target.text,
                changed_at=changed_at,
                changed_by=actor,
            )
        return SettingChange(SettingValue(resolved, canonical, version, target, changed_at, actor), created=True)

    def history(self, key: SettingKey | str) -> list[SettingValue]:
        """Every version of one setting, newest first."""
        resolved = _key(key)
        with self._database.read() as connection:
            rows = settings_repo.history(connection, resolved.value)
        return [_value(row) for row in rows]
