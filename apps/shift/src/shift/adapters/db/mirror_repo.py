"""SQL for the accounting mirrors: append-only facts copied from the `v_shift_*` views."""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection

CASH = "acc_cash"
CONSUMPTION = "acc_consumption"
VITAL = "acc_vital"
REGISTRATION = "acc_registration"
OPEN_INVOICE = "acc_open_invoice"
VOID = "acc_void"

_COLUMNS: Mapping[str, tuple[str, ...]] = {
    CASH: ("invoice_id", "closed_at", "amount", "amount_raw", "ingested_at"),
    CONSUMPTION: (
        "ledger_id",
        "invoice_id",
        "closed_at",
        "acc_code",
        "quantity",
        "quantity_raw",
        "item_name",
        "ingested_at",
    ),
    VITAL: ("injection_id", "invoice_id", "nurse_id", "kind", "closed_at", "ingested_at"),
    REGISTRATION: (
        "patient_id",
        "created_by",
        "created_at",
        "has_valid_nid",
        "has_valid_phone",
        "is_foreign",
        "ingested_at",
    ),
    OPEN_INVOICE: ("invoice_id", "opened_at", "opened_by", "shift", "work_date", "refreshed_at"),
    VOID: (
        "void_id",
        "invoice_id",
        "kind",
        "amount",
        "amount_raw",
        "refund_method",
        "reason",
        "voided_by",
        "voided_at",
        "work_date",
        "shift",
        "ingested_at",
    ),
}
_PRIMARY_KEY: Mapping[str, str] = {
    CASH: "invoice_id",
    CONSUMPTION: "ledger_id",
    VITAL: "injection_id",
    REGISTRATION: "patient_id",
    OPEN_INVOICE: "invoice_id",
    VOID: "void_id",
}


def _table(name: str) -> str:
    if name not in _COLUMNS:
        raise ValueError(f"unknown accounting mirror: {name}")
    return name


def _insert_ignoring(connection: Connection, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert mirror rows, keeping the copy already there for a row seen twice."""
    if not rows:
        return 0
    columns = _COLUMNS[_table(table)]
    placeholders = ", ".join("?" for _ in columns)
    result = connection.exec_driver_sql(
        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )
    return int(result.rowcount)


def insert_cash(connection: Connection, rows: Sequence[Mapping[str, Any]]) -> int:
    """Copy cash receipts per invoice; `invoice_id` is the identity."""
    return _insert_ignoring(connection, CASH, rows)


def insert_consumption(connection: Connection, rows: Sequence[Mapping[str, Any]]) -> int:
    """Copy stock consumption per ledger line; `ledger_id` is the identity."""
    return _insert_ignoring(connection, CONSUMPTION, rows)


def insert_vitals(connection: Connection, rows: Sequence[Mapping[str, Any]]) -> int:
    """Copy the sugar and blood-pressure services; `injection_id` is the identity."""
    return _insert_ignoring(connection, VITAL, rows)


def insert_registrations(connection: Connection, rows: Sequence[Mapping[str, Any]]) -> int:
    """Copy patient registrations with validity flags only, never the national ID or phone."""
    return _insert_ignoring(connection, REGISTRATION, rows)


def insert_voids(connection: Connection, rows: Sequence[Mapping[str, Any]]) -> int:
    """Copy refunds and cancellations; `void_id` is the identity, one void per invoice."""
    return _insert_ignoring(connection, VOID, rows)


def replace_open_invoices(connection: Connection, rows: Sequence[Mapping[str, Any]], refreshed_at: str) -> int:
    """Refresh the open-invoice snapshot: it is state, not a stream, so it replaces wholesale."""
    connection.exec_driver_sql(f"DELETE FROM {OPEN_INVOICE}")
    if not rows:
        return 0
    columns = _COLUMNS[OPEN_INVOICE]
    placeholders = ", ".join("?" for _ in columns)
    connection.exec_driver_sql(
        f"INSERT INTO {OPEN_INVOICE} ({', '.join(columns)}) VALUES ({placeholders})",
        [tuple({**row, "refreshed_at": refreshed_at}[column] for column in columns) for row in rows],
    )
    return len(rows)


def rows(connection: Connection, table: str) -> list[dict[str, Any]]:
    """Read one mirror wholesale, in primary-key order; tests and diagnostics only."""
    table = _table(table)
    result = connection.exec_driver_sql(f"SELECT * FROM {table} ORDER BY {_PRIMARY_KEY[table]}")
    return [dict(row._mapping) for row in result]


def count(connection: Connection, table: str) -> int:
    """Number of mirrored rows in one table."""
    return int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {_table(table)}").scalar_one())
