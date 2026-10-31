"""v12 — `campaigns`: the discount campaigns a manager defines before the shift runs (S01 §3 «ت»).

The shift event «ت» may only name a campaign the manager defined here: a name, the one service it
covers, the amount and the days it runs. A campaign is configuration, not a record of the clinic — the
manager fixes a typo by saving the same id again — so the table keeps one row per campaign and the
event log stays append-only (R-03). `starts_on` and `ends_on` are ISO `YYYY-MM-DD` text, so a range
compares and sorts in SQL without decoding it.

Additive only: a new table, no trigger and no view, so an installed v11 database keeps every row it
had (ADR-002).
"""

from sqlalchemy import Connection


def up(connection: Connection) -> None:
    """Add the campaign calendar the shift event «ت» reads; the service owns the date order."""
    connection.exec_driver_sql(
        """CREATE TABLE campaigns (
        campaign_id   TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        service       TEXT NOT NULL,
        amount_toman  INTEGER NOT NULL,
        starts_on     TEXT NOT NULL,
        ends_on       TEXT NOT NULL,
        created_by    TEXT NOT NULL,
        created_at    TEXT NOT NULL
    )"""
    )
