"""SQL for the versioned catalog: locations, items and staff roles."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_LOCATION_COLUMNS = ("location_id", "name", "retired", "version", "effective_from_shift")
_ITEM_COLUMNS = (
    "item_id",
    "name",
    "kind",
    "location_id",
    "unit",
    "minimum",
    "acc_code",
    "stock_factor",
    "retired",
    "responsible_role",
    "frequency",
    "sort_order",
    "version",
    "effective_from_shift",
)
_ROLE_COLUMNS = ("staff_id", "role", "granted", "version", "effective_from_shift")


@dataclass(frozen=True, slots=True)
class LocationRow:
    """One stored version of one location."""

    location_id: str
    name: str
    retired: bool
    version: int
    effective_from_shift: str


@dataclass(frozen=True, slots=True)
class ItemRow:
    """One stored version of one item; `acc_code` is the accounting stock code."""

    item_id: str
    name: str
    kind: str
    location_id: str
    unit: str
    minimum: int
    acc_code: str | None
    stock_factor: int
    retired: bool
    version: int
    effective_from_shift: str
    responsible_role: str | None = None
    frequency: str = "every_shift"
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class RoleRow:
    """One stored version of one staff member's hold on one role."""

    staff_id: str
    role: str
    granted: bool
    version: int
    effective_from_shift: str


def _location(row: Mapping[str, Any]) -> LocationRow:
    return LocationRow(
        location_id=str(row["location_id"]),
        name=str(row["name"]),
        retired=bool(row["retired"]),
        version=int(row["version"]),
        effective_from_shift=str(row["effective_from_shift"]),
    )


def _item(row: Mapping[str, Any]) -> ItemRow:
    return ItemRow(
        item_id=str(row["item_id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        location_id=str(row["location_id"]),
        unit=str(row["unit"]),
        minimum=int(row["minimum"]),
        acc_code=row["acc_code"],
        stock_factor=int(row["stock_factor"]),
        retired=bool(row["retired"]),
        version=int(row["version"]),
        effective_from_shift=str(row["effective_from_shift"]),
        responsible_role=row["responsible_role"],
        frequency=str(row["frequency"]),
        sort_order=int(row["sort_order"]),
    )


def _role(row: Mapping[str, Any]) -> RoleRow:
    return RoleRow(
        staff_id=str(row["staff_id"]),
        role=str(row["role"]),
        granted=bool(row["granted"]),
        version=int(row["version"]),
        effective_from_shift=str(row["effective_from_shift"]),
    )


def _newest(connection: Connection, sql: str, parameters: tuple[Any, ...]) -> Mapping[str, Any] | None:
    return connection.exec_driver_sql(sql, parameters).mappings().first()


def _next_version(connection: Connection, table: str, key_columns: tuple[str, ...], keys: tuple[str, ...]) -> int:
    """Version number for the next change; `Database.write` holds `BEGIN IMMEDIATE` around it."""
    where = " AND ".join(f"{column} = ?" for column in key_columns)
    highest = connection.exec_driver_sql(
        f"SELECT COALESCE(MAX(version), 0) FROM {table} WHERE {where}", keys
    ).scalar_one()
    return int(highest) + 1


def _effective_sql(
    table: str, columns: tuple[str, ...], key_columns: tuple[str, ...], *, filter_column: str | None = None
) -> str:
    """SQL for one row per entity: the newest version that has started at the shift parameter.

    A correlated seek on the per-entity index replaces the window function. The window needed
    a sort over every entity before the final `ORDER BY`; this form finds each newest row
    directly, and callers sort the small result set in Python.
    """
    column_list = ", ".join(columns)
    match = " AND ".join(f"candidate.{column} = current.{column}" for column in key_columns)
    tie = " AND ".join(f"tie.{column} = current.{column}" for column in key_columns)
    scope = f"current.{filter_column} = ? AND " if filter_column else ""
    return (
        f"SELECT {column_list} FROM {table} AS current "
        f"WHERE {scope}current.effective_from_shift = ("
        f"SELECT MAX(candidate.effective_from_shift) FROM {table} AS candidate "
        f"WHERE {match} AND candidate.effective_from_shift <= ?) "
        "AND current.version = ("
        f"SELECT MAX(tie.version) FROM {table} AS tie "
        f"WHERE {tie} AND tie.effective_from_shift = current.effective_from_shift)"
    )


def _effective_rows(
    connection: Connection,
    table: str,
    columns: tuple[str, ...],
    key_columns: tuple[str, ...],
    shift_key: str,
    *,
    filter_column: str | None = None,
    filter_value: str | None = None,
) -> list[Mapping[str, Any]]:
    """One row per entity: the newest version that has started at `shift_key`."""
    sql = _effective_sql(table, columns, key_columns, filter_column=filter_column)
    parameters = (filter_value, shift_key) if filter_column else (shift_key,)
    return list(connection.exec_driver_sql(sql, parameters).mappings())


def location_next_version(connection: Connection, location_id: str) -> int:
    return _next_version(connection, "catalog_location_versions", ("location_id",), (location_id,))


def insert_location_version(
    connection: Connection,
    *,
    location_id: str,
    name: str,
    retired: bool,
    version: int,
    effective_from_shift: str,
    changed_at: str,
    changed_by: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO catalog_location_versions "
        "(location_id, name, retired, version, effective_from_shift, changed_at, changed_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (location_id, name, int(retired), version, effective_from_shift, changed_at, changed_by),
    )


def find_location_latest(connection: Connection, location_id: str) -> LocationRow | None:
    """The version that governs the farthest future shift: newest by effective shift."""
    row = _newest(
        connection,
        f"SELECT {', '.join(_LOCATION_COLUMNS)} FROM catalog_location_versions "
        "WHERE location_id = ? ORDER BY effective_from_shift DESC, version DESC LIMIT 1",
        (location_id,),
    )
    return _location(row) if row is not None else None


def find_location_effective(connection: Connection, location_id: str, shift_key: str) -> LocationRow | None:
    row = _newest(
        connection,
        f"SELECT {', '.join(_LOCATION_COLUMNS)} FROM catalog_location_versions "
        "WHERE location_id = ? AND effective_from_shift <= ? "
        "ORDER BY effective_from_shift DESC, version DESC LIMIT 1",
        (location_id, shift_key),
    )
    return _location(row) if row is not None else None


def list_locations_effective(connection: Connection, shift_key: str) -> list[LocationRow]:
    rows = _effective_rows(connection, "catalog_location_versions", _LOCATION_COLUMNS, ("location_id",), shift_key)
    return [_location(row) for row in rows]


def item_next_version(connection: Connection, item_id: str) -> int:
    return _next_version(connection, "catalog_item_versions", ("item_id",), (item_id,))


def insert_item_version(
    connection: Connection,
    *,
    item_id: str,
    name: str,
    kind: str,
    location_id: str,
    unit: str,
    minimum: int,
    acc_code: str | None,
    stock_factor: int,
    retired: bool,
    version: int,
    effective_from_shift: str,
    changed_at: str,
    changed_by: str,
    responsible_role: str | None = None,
    frequency: str = "every_shift",
    sort_order: int = 0,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO catalog_item_versions (item_id, name, kind, location_id, unit, minimum, acc_code, "
        "stock_factor, retired, version, effective_from_shift, changed_at, changed_by, "
        "responsible_role, frequency, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item_id,
            name,
            kind,
            location_id,
            unit,
            minimum,
            acc_code,
            stock_factor,
            int(retired),
            version,
            effective_from_shift,
            changed_at,
            changed_by,
            responsible_role,
            frequency,
            sort_order,
        ),
    )


def next_sort_order(connection: Connection) -> int:
    """Where a new item lands: ten places after the last item of the whole list (the end of the shelf order)."""
    highest = connection.exec_driver_sql("SELECT COALESCE(MAX(sort_order), 0) FROM catalog_item_versions").scalar_one()
    return int(highest) + 10


def find_item_latest(connection: Connection, item_id: str) -> ItemRow | None:
    """The version that governs the farthest future shift: newest by effective shift."""
    row = _newest(
        connection,
        f"SELECT {', '.join(_ITEM_COLUMNS)} FROM catalog_item_versions WHERE item_id = ? "
        "ORDER BY effective_from_shift DESC, version DESC LIMIT 1",
        (item_id,),
    )
    return _item(row) if row is not None else None


def list_items_effective(connection: Connection, shift_key: str) -> list[ItemRow]:
    rows = _effective_rows(connection, "catalog_item_versions", _ITEM_COLUMNS, ("item_id",), shift_key)
    return [_item(row) for row in rows]


def find_item_with_acc_code(connection: Connection, acc_code: str, *, exclude_item_id: str) -> str | None:
    """The item that already owns `acc_code` anywhere in the history, retired or not.

    The lookup spans every version rather than one shift's slice: an accounting code names
    one item for ever, so a code registered for a later shift is still taken for an earlier one.
    """
    row = connection.exec_driver_sql(
        "SELECT item_id FROM catalog_item_versions WHERE acc_code = ? AND item_id != ? LIMIT 1",
        (acc_code, exclude_item_id),
    ).first()
    return None if row is None else str(row[0])


def role_next_version(connection: Connection, staff_id: str, role: str) -> int:
    return _next_version(connection, "catalog_staff_role_versions", ("staff_id", "role"), (staff_id, role))


def insert_role_version(
    connection: Connection,
    *,
    staff_id: str,
    role: str,
    granted: bool,
    version: int,
    effective_from_shift: str,
    changed_at: str,
    changed_by: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO catalog_staff_role_versions "
        "(staff_id, role, granted, version, effective_from_shift, changed_at, changed_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (staff_id, role, int(granted), version, effective_from_shift, changed_at, changed_by),
    )


def find_role_latest(connection: Connection, staff_id: str, role: str) -> RoleRow | None:
    """The version that governs the farthest future shift: newest by effective shift."""
    row = _newest(
        connection,
        f"SELECT {', '.join(_ROLE_COLUMNS)} FROM catalog_staff_role_versions "
        "WHERE staff_id = ? AND role = ? ORDER BY effective_from_shift DESC, version DESC LIMIT 1",
        (staff_id, role),
    )
    return _role(row) if row is not None else None


def list_staff_roles_effective(connection: Connection, staff_id: str, shift_key: str) -> list[RoleRow]:
    """The newest grant decision per role of one person, as a given shift sees it."""
    rows = _effective_rows(
        connection,
        "catalog_staff_role_versions",
        _ROLE_COLUMNS,
        ("staff_id", "role"),
        shift_key,
        filter_column="staff_id",
        filter_value=staff_id,
    )
    return [_role(row) for row in rows]
