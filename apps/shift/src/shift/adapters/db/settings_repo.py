"""SQL for versioned settings; every row is one version bounded by a starting shift."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_COLUMNS = ("id", "setting_key", "value", "version", "effective_from_shift", "changed_at", "changed_by")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM setting_versions"
_COLUMN_LIST = ", ".join(_COLUMNS)

# The newest version per key among those that have started: one row per setting, one pass.
_NEWEST_PER_KEY = (
    f"SELECT {_COLUMN_LIST} FROM ("
    f"SELECT {_COLUMN_LIST}, "
    "ROW_NUMBER() OVER (PARTITION BY setting_key ORDER BY effective_from_shift DESC, version DESC) AS age "
    "FROM setting_versions {where}) WHERE age = 1 ORDER BY setting_key"
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
    """Version number for the next change; the single writer process makes MAX+1 safe."""
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
    rows = connection.exec_driver_sql(_NEWEST_PER_KEY.format(where="WHERE effective_from_shift <= ?"), (shift_key,))
    return [_row(row) for row in rows.mappings()]


def find_latest(connection: Connection, setting_key: str) -> SettingRow | None:
    """The newest version of one setting, whatever shift it starts at."""
    row = (
        connection.exec_driver_sql(f"{_SELECT} WHERE setting_key = ? ORDER BY version DESC LIMIT 1", (setting_key,))
        .mappings()
        .first()
    )
    return _row(row) if row is not None else None


def history(connection: Connection, setting_key: str) -> list[SettingRow]:
    """Every version of one setting, newest first."""
    rows = connection.exec_driver_sql(f"{_SELECT} WHERE setting_key = ? ORDER BY version DESC", (setting_key,))
    return [_row(row) for row in rows.mappings()]
