"""SQL for versioned settings; every row is one version bounded by a starting shift."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_COLUMNS = ("id", "setting_key", "value", "version", "effective_from_shift", "changed_at", "changed_by")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM setting_versions"
_COLUMN_LIST = ", ".join(_COLUMNS)

# One seek per setting key on the per-key index: no window and no sort over four rows.
_EFFECTIVE_SQL = (
    f"SELECT {_COLUMN_LIST} FROM setting_versions AS current "
    "WHERE current.id = ("
    "SELECT candidate.id FROM setting_versions AS candidate "
    "WHERE candidate.setting_key = current.setting_key AND candidate.effective_from_shift <= ? "
    "ORDER BY candidate.effective_from_shift DESC, candidate.version DESC LIMIT 1)"
)


@dataclass(frozen=True, slots=True)
class SettingRow:
    """One stored version of one setting."""

    id: int
    setting_key: str
    value: str
    version: int
    effective_from_shift: str
    changed_at: str
    changed_by: str


def _row(row: Mapping[str, Any]) -> SettingRow:
    return SettingRow(
        id=int(row["id"]),
        setting_key=str(row["setting_key"]),
        value=str(row["value"]),
        version=int(row["version"]),
        effective_from_shift=str(row["effective_from_shift"]),
        changed_at=str(row["changed_at"]),
        changed_by=str(row["changed_by"]),
    )


def next_version(connection: Connection, setting_key: str) -> int:
    """Version number for the next change; `Database.write` holds `BEGIN IMMEDIATE` around it."""
    highest = connection.exec_driver_sql(
        "SELECT COALESCE(MAX(version), 0) FROM setting_versions WHERE setting_key = ?", (setting_key,)
    ).scalar_one()
    return int(highest) + 1


def insert_version(
    connection: Connection,
    *,
    setting_key: str,
    value: str,
    version: int,
    effective_from_shift: str,
    changed_at: str,
    changed_by: str,
) -> int:
    """Append one version; versions are never updated or deleted."""
    result = connection.exec_driver_sql(
        "INSERT INTO setting_versions (setting_key, value, version, effective_from_shift, changed_at, changed_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (setting_key, value, version, effective_from_shift, changed_at, changed_by),
    )
    return int(result.lastrowid or 0)


def find_effective(connection: Connection, setting_key: str, shift_key: str) -> SettingRow | None:
    """The version of one setting that a shift sees, i.e. the newest one that has started."""
    row = (
        connection.exec_driver_sql(
            f"{_SELECT} WHERE setting_key = ? AND effective_from_shift <= ? "
            "ORDER BY effective_from_shift DESC, version DESC LIMIT 1",
            (setting_key, shift_key),
        )
        .mappings()
        .first()
    )
    return _row(row) if row is not None else None


def list_effective(connection: Connection, shift_key: str) -> list[SettingRow]:
    """Every setting as one shift sees it."""
    rows = connection.exec_driver_sql(_EFFECTIVE_SQL, (shift_key,))
    return [_row(row) for row in rows.mappings()]


def find_latest(connection: Connection, setting_key: str) -> SettingRow | None:
    """The version that governs the farthest future shift, i.e. newest by effective shift.

    Ordering by `version` alone is wrong once legacy rows can be out of order; idempotency
    must compare against the value later shifts actually read, not the highest row number.
    """
    row = (
        connection.exec_driver_sql(
            f"{_SELECT} WHERE setting_key = ? ORDER BY effective_from_shift DESC, version DESC LIMIT 1",
            (setting_key,),
        )
        .mappings()
        .first()
    )
    return _row(row) if row is not None else None


def history(connection: Connection, setting_key: str) -> list[SettingRow]:
    """Every version of one setting, newest first."""
    rows = connection.exec_driver_sql(f"{_SELECT} WHERE setting_key = ? ORDER BY version DESC", (setting_key,))
    return [_row(row) for row in rows.mappings()]
