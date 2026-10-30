"""SQL for the rebuildable projection tables and their shadow copies."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Connection

from shift.common.hashing import canonical_json, sha256_hex
from shift.domain.effects import CashEffect, CashOperation, StockEffect, StockOperation

STOCK = "proj_stock"
CASH = "proj_cash"
STOCK_SHADOW = "proj_stock__rebuild"
CASH_SHADOW = "proj_cash__rebuild"
PROJECTIONS = (STOCK, CASH)
_TABLES = frozenset({STOCK, CASH, STOCK_SHADOW, CASH_SHADOW})

_STOCK_DDL = "CREATE TABLE {table} (item_id TEXT NOT NULL, location_id TEXT NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY (item_id, location_id))"
_CASH_DDL = "CREATE TABLE {table} (cash_box_id TEXT PRIMARY KEY, expected INTEGER NOT NULL, counted INTEGER NOT NULL, variance INTEGER NOT NULL)"


def _table(name: str) -> str:
    if name not in _TABLES:
        raise ValueError(f"unknown projection table: {name}")
    return name


def ensure_meta(connection: Connection) -> None:
    """Make sure both projections have a bookkeeping row."""
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO meta_projection (name, last_seq) VALUES (?, 0), (?, 0)",
        PROJECTIONS,
    )


def last_seq(connection: Connection, name: str) -> int:
    """Last event sequence applied to a projection."""
    row = connection.exec_driver_sql("SELECT last_seq FROM meta_projection WHERE name = ?", (name,)).first()
    return int(row[0]) if row is not None else 0


def set_meta(
    connection: Connection, name: str, last_seq_value: int, checksum: str | None, built_at: str | None
) -> None:
    """Record how far a projection has been applied and its last full checksum."""
    connection.exec_driver_sql(
        "UPDATE meta_projection SET last_seq = ?, checksum = ?, built_at = ? WHERE name = ?",
        (last_seq_value, checksum, built_at, name),
    )


def apply_stock(connection: Connection, table: str, effect: StockEffect) -> None:
    """Apply one stock effect with a single upsert (no read-modify-write race)."""
    table = _table(table)
    if effect.operation is StockOperation.SET:
        sql = (
            f"INSERT INTO {table} (item_id, location_id, qty) VALUES (?, ?, ?) "
            "ON CONFLICT(item_id, location_id) DO UPDATE SET qty = excluded.qty"
        )
    elif effect.operation is StockOperation.ADD:
        sql = (
            f"INSERT INTO {table} (item_id, location_id, qty) VALUES (?, ?, ?) "
            "ON CONFLICT(item_id, location_id) DO UPDATE SET qty = qty + excluded.qty"
        )
    else:
        sql = (
            f"INSERT INTO {table} (item_id, location_id, qty) VALUES (?, ?, -?) "
            "ON CONFLICT(item_id, location_id) DO UPDATE SET qty = qty + excluded.qty"
        )
    connection.exec_driver_sql(sql, (effect.item_id, effect.location_id, effect.quantity))


def apply_cash(connection: Connection, table: str, effect: CashEffect) -> None:
    """Apply one cash effect to its box, always keeping variance = counted − expected.

    The table is keyed per cash box, not per shift (ADR-017 item 8), so a correction or a receipt
    lands on the same row whatever shift it was stamped in.
    """
    table = _table(table)
    box = effect.cash_box_id
    if not box:
        raise ValueError("a cash effect needs its cash box")
    if effect.operation is CashOperation.OPEN:
        # The opening only seeds a box that has no figure yet; after the first shift the box's figure
        # comes from the counts and from the settlement that follows them (ADR-017 item 8, H-13).
        sql = (
            f"INSERT INTO {table} (cash_box_id, expected, counted, variance) VALUES (?, ?, ?, 0) ON CONFLICT DO NOTHING"
        )
        parameters = (box, effect.amount, effect.amount)
    elif effect.operation is CashOperation.COUNT:
        sql = (
            f"INSERT INTO {table} (cash_box_id, expected, counted, variance) VALUES (?, 0, ?, ?) "
            "ON CONFLICT(cash_box_id) DO UPDATE SET counted = excluded.counted, "
            f"variance = excluded.counted - {table}.expected"
        )
        parameters = (box, effect.amount, effect.amount)
    elif effect.operation is CashOperation.SETTLE:
        # The agreed figure becomes the box's base, so the next shift's N0 is that figure and a gap
        # from the shift before cannot reappear in every later reference (H-02, H-13).
        sql = (
            f"INSERT INTO {table} (cash_box_id, expected, counted, variance) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(cash_box_id) DO UPDATE SET expected = excluded.expected, "
            "counted = excluded.counted, variance = 0"
        )
        parameters = (box, effect.amount, effect.amount)
    else:
        delta = effect.amount if effect.operation is CashOperation.ADD else -effect.amount
        sql = (
            f"INSERT INTO {table} (cash_box_id, expected, counted, variance) VALUES (?, ?, 0, ?) "
            "ON CONFLICT(cash_box_id) DO UPDATE SET expected = "
            f"{table}.expected + excluded.expected, variance = {table}.counted - "
            f"({table}.expected + excluded.expected)"
        )
        parameters = (box, delta, -delta)
    connection.exec_driver_sql(sql, parameters)


def create_shadow_tables(connection: Connection) -> None:
    """Create empty shadow tables for a rebuild; they are swapped in only on success."""
    connection.exec_driver_sql(f"DROP TABLE IF EXISTS {STOCK_SHADOW}")
    connection.exec_driver_sql(f"DROP TABLE IF EXISTS {CASH_SHADOW}")
    connection.exec_driver_sql(_STOCK_DDL.format(table=STOCK_SHADOW))
    connection.exec_driver_sql(_CASH_DDL.format(table=CASH_SHADOW))


def swap_shadow(connection: Connection) -> None:
    """Replace both live projections with their rebuilt shadows in one transaction."""
    connection.exec_driver_sql(f"DROP TABLE {STOCK}")
    connection.exec_driver_sql(f"ALTER TABLE {STOCK_SHADOW} RENAME TO {STOCK}")
    connection.exec_driver_sql(f"DROP TABLE {CASH}")
    connection.exec_driver_sql(f"ALTER TABLE {CASH_SHADOW} RENAME TO {CASH}")


def checksum(connection: Connection, name: str, table: str) -> str:
    """Deterministic checksum of a projection's full ordered contents."""
    table = _table(table)
    if name == STOCK:
        rows = connection.exec_driver_sql(
            f"SELECT item_id, location_id, qty FROM {table} ORDER BY item_id, location_id"
        ).mappings()
        values = [{"item_id": row["item_id"], "location_id": row["location_id"], "qty": row["qty"]} for row in rows]
    elif name == CASH:
        rows = connection.exec_driver_sql(
            f"SELECT cash_box_id, expected, counted, variance FROM {table} ORDER BY cash_box_id"
        ).mappings()
        values = [
            {
                "cash_box_id": row["cash_box_id"],
                "expected": row["expected"],
                "counted": row["counted"],
                "variance": row["variance"],
            }
            for row in rows
        ]
    else:
        raise ValueError(f"unknown projection: {name}")
    return sha256_hex(canonical_json(values))


def stock_rows(connection: Connection, table: str = STOCK) -> list[dict[str, Any]]:
    """Read the stock projection for tests and diagnostics."""
    table = _table(table)
    rows = connection.exec_driver_sql(
        f"SELECT item_id, location_id, qty FROM {table} ORDER BY item_id, location_id"
    ).mappings()
    return [dict(row) for row in rows]


def cash_rows(connection: Connection, table: str = CASH) -> list[dict[str, Any]]:
    """Read the cash projection, one row per cash box, for tests and diagnostics."""
    table = _table(table)
    rows = connection.exec_driver_sql(
        f"SELECT cash_box_id, expected, counted, variance FROM {table} ORDER BY cash_box_id"
    ).mappings()
    return [dict(row) for row in rows]


def meta_row(connection: Connection, name: str) -> Mapping[str, Any]:
    """Read one projection's bookkeeping row."""
    row = (
        connection.exec_driver_sql(
            "SELECT name, last_seq, checksum, built_at FROM meta_projection WHERE name = ?", (name,)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else {}
