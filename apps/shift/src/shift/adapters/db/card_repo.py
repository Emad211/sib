"""SQL for the derived `daily_cards` table: one stored card per card date (SHF-6.1).

A row is a document, not a fact: `upsert` replaces the card of a day when the builder regenerates it,
which is safe because the event log and the audit trail stay append-only (R-03). `payload` is decoded
here the way `audit_repo` decodes `detail`, so a caller reads a card's own JSON and never a column
list that would grow with the payload.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection

_COLUMNS = (
    "card_date",
    "covered_from",
    "covered_to",
    "schema_version",
    "generated_at_utc",
    "generated_at_tehran",
    "payload",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM daily_cards"


@dataclass(frozen=True, slots=True)
class DailyCardRow:
    """One stored card: the day it is for, the days it covers, when it was built and its document."""

    card_date: date
    covered_from: date
    covered_to: date
    schema_version: int
    generated_at_utc: str
    generated_at_tehran: str
    payload: dict[str, Any]


def _row(row: Mapping[str, Any]) -> DailyCardRow:
    return DailyCardRow(
        card_date=date.fromisoformat(str(row["card_date"])),
        covered_from=date.fromisoformat(str(row["covered_from"])),
        covered_to=date.fromisoformat(str(row["covered_to"])),
        schema_version=int(row["schema_version"]),
        generated_at_utc=str(row["generated_at_utc"]),
        generated_at_tehran=str(row["generated_at_tehran"]),
        payload=json.loads(row["payload"]),
    )


def upsert(connection: Connection, values: Mapping[str, Any]) -> DailyCardRow:
    """Store one card, replacing the document of the same card date; dates are ISO text columns."""
    updates = ", ".join(f"{column} = excluded.{column}" for column in _COLUMNS[1:])
    placeholders = ", ".join("?" for _ in _COLUMNS)
    connection.exec_driver_sql(
        f"INSERT INTO daily_cards ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT (card_date) DO UPDATE SET {updates}",
        tuple(values[column] for column in _COLUMNS),
    )
    return DailyCardRow(
        card_date=date.fromisoformat(str(values["card_date"])),
        covered_from=date.fromisoformat(str(values["covered_from"])),
        covered_to=date.fromisoformat(str(values["covered_to"])),
        schema_version=int(values["schema_version"]),
        generated_at_utc=str(values["generated_at_utc"]),
        generated_at_tehran=str(values["generated_at_tehran"]),
        payload=json.loads(str(values["payload"])),
    )


def get(connection: Connection, card_date: date) -> DailyCardRow | None:
    """The card of one day, or None when no run has produced it yet."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE card_date = ?", (card_date.isoformat(),)).mappings().first()
    return None if row is None else _row(row)


def latest(connection: Connection) -> DailyCardRow | None:
    """The newest card, i.e. the one the manager's page opens on."""
    row = connection.exec_driver_sql(f"{_SELECT} ORDER BY card_date DESC LIMIT 1").mappings().first()
    return None if row is None else _row(row)


def history(connection: Connection, *, limit: int = 50) -> list[DailyCardRow]:
    """Every stored card, newest first, capped at `limit`."""
    rows = connection.exec_driver_sql(f"{_SELECT} ORDER BY card_date DESC LIMIT ?", (limit,)).mappings()
    return [_row(row) for row in rows]


def newest_date(connection: Connection) -> date | None:
    """The newest card date, so the builder knows how far back a restart has to fill in."""
    row = connection.exec_driver_sql("SELECT MAX(card_date) FROM daily_cards").first()
    return None if row is None or row[0] is None else date.fromisoformat(str(row[0]))
