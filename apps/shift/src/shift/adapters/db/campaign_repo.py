"""SQL for the `campaigns` table: the discounts a manager defines before the shift runs (S01 §3 «ت»).

A row is configuration, not a fact: `upsert` replaces the content of the same id when the manager
fixes a typo, and keeps the creation stamp it was stored with, which is safe because the event log
stays append-only (R-03). Dates are ISO `YYYY-MM-DD` columns, so a range compares in SQL.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_COLUMNS = (
    "campaign_id",
    "name",
    "service",
    "amount_toman",
    "starts_on",
    "ends_on",
    "created_by",
    "created_at",
)
# Everything a fix may change; the primary key addresses the row and `created_at` stays as it was.
_UPDATED = _COLUMNS[1:-1]
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM campaigns"


@dataclass(frozen=True, slots=True)
class CampaignRow:
    """One stored campaign: its range as ISO text, and who defined it and when."""

    campaign_id: str
    name: str
    service: str
    amount_toman: int
    starts_on: str
    ends_on: str
    created_by: str
    created_at: str


def _row(row: Mapping[str, Any]) -> CampaignRow:
    return CampaignRow(
        campaign_id=str(row["campaign_id"]),
        name=str(row["name"]),
        service=str(row["service"]),
        amount_toman=int(row["amount_toman"]),
        starts_on=str(row["starts_on"]),
        ends_on=str(row["ends_on"]),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
    )


def upsert(connection: Connection, values: Mapping[str, Any]) -> CampaignRow:
    """Store one campaign, replacing the content of the same id; dates are ISO text columns.

    `RETURNING` answers the row as stored, so a fix reports the creation stamp the row kept instead
    of the value this call carried.
    """
    updates = ", ".join(f"{column} = excluded.{column}" for column in _UPDATED)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    row = (
        connection.exec_driver_sql(
            f"INSERT INTO campaigns ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT (campaign_id) DO UPDATE SET {updates} "
            f"RETURNING {', '.join(_COLUMNS)}",
            tuple(values[column] for column in _COLUMNS),
        )
        .mappings()
        .one()
    )
    return _row(row)


def get(connection: Connection, campaign_id: str) -> CampaignRow | None:
    """One campaign by id, or None when the manager never defined it."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE campaign_id = ?", (campaign_id,)).mappings().first()
    return None if row is None else _row(row)


def newest_first(connection: Connection) -> list[CampaignRow]:
    """Every campaign, most recently defined first: the order the manager's page lists."""
    rows = connection.exec_driver_sql(f"{_SELECT} ORDER BY created_at DESC, campaign_id ASC").mappings()
    return [_row(row) for row in rows]


def active_on(connection: Connection, day: str) -> list[CampaignRow]:
    """The campaigns whose range includes `day`, by name, which is how the phone offers them."""
    rows = connection.exec_driver_sql(
        f"{_SELECT} WHERE starts_on <= ? AND ends_on >= ? ORDER BY name ASC, campaign_id ASC",
        (day, day),
    ).mappings()
    return [_row(row) for row in rows]
