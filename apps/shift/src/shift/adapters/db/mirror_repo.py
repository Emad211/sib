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
    # A cash row is one version of one close, so an invoice may legitimately have several rows.
    CASH: "invoice_id, closed_at",
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


def cash_versions_for(connection: Connection, invoice_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    """Every ingested close of these invoices, oldest first, with `superseded_at` (correction state)."""
    if not invoice_ids:
        return {}
    placeholders = ", ".join("?" for _ in invoice_ids)
    result = connection.exec_driver_sql(
        f"SELECT invoice_id, closed_at, amount, amount_raw, ingested_at, superseded_at FROM {CASH} "
        f"WHERE invoice_id IN ({placeholders}) ORDER BY invoice_id, closed_at",
        tuple(invoice_ids),
    )
    versions: dict[int, list[dict[str, Any]]] = {}
    for row in result.mappings():
        versions.setdefault(int(row["invoice_id"]), []).append(dict(row))
    return versions


def supersede_cash(connection: Connection, invoice_id: int, closed_at: str, superseded_at: str) -> None:
    """Mark one cash version as replaced by a newer close; the row stays for audit (R-03)."""
    connection.exec_driver_sql(
        f"UPDATE {CASH} SET superseded_at = ? WHERE invoice_id = ? AND closed_at = ? AND superseded_at IS NULL",
        (superseded_at, invoice_id, closed_at),
    )


def update_cash_amount(
    connection: Connection, invoice_id: int, closed_at: str, amount: int, amount_raw: float, ingested_at: str
) -> None:
    """Correct the amount of a close that accounting changed without moving its timestamp."""
    connection.exec_driver_sql(
        f"UPDATE {CASH} SET amount = ?, amount_raw = ?, ingested_at = ? WHERE invoice_id = ? AND closed_at = ?",
        (amount, amount_raw, ingested_at, invoice_id, closed_at),
    )


def uncompensated_voids(connection: Connection) -> list[dict[str, Any]]:
    """Voids that still need a reversing event, with the cash version they must cancel (if any)."""
    result = connection.exec_driver_sql(
        f"SELECT v.void_id, v.invoice_id, v.kind, v.voided_at, "
        f"c.closed_at AS cash_closed_at, c.amount AS cash_amount, c.amount_raw AS cash_amount_raw "
        f"FROM {VOID} v LEFT JOIN {CASH} c "
        "ON c.invoice_id = v.invoice_id AND c.superseded_at IS NULL "
        "WHERE v.compensated_at IS NULL ORDER BY v.void_id"
    )
    return [dict(row._mapping) for row in result]


def mark_void_compensated(connection: Connection, void_id: int, compensated_at: str) -> None:
    """Remember that a void was turned into an `acc.cash_reversal` (or had no cash to reverse)."""
    connection.exec_driver_sql(f"UPDATE {VOID} SET compensated_at = ? WHERE void_id = ?", (compensated_at, void_id))


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
