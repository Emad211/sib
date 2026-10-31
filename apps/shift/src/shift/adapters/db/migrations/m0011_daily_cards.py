"""v11 — `daily_cards`: the manager's daily card, one derived document per card date (S01 §6).

A card is a read model of the event log, the handover flows and the audit trail, not a record of the
clinic: regenerating a day rewrites its one row, while the event log stays append-only (R-03). The key
is the day the card is for, so a second run of the same day replaces the document instead of stacking
a duplicate. `payload` is the versioned card as canonical JSON (`CARD_SCHEMA_VERSION`); the date and
stamp columns exist so the page and an operator can find a card without decoding one.

Additive only: a new table, no trigger and no view, so an installed v10 database keeps every row it
had (ADR-002).
"""

from sqlalchemy import Connection


def up(connection: Connection) -> None:
    """Add the stored daily cards, keyed by the day the card is for."""
    connection.exec_driver_sql(
        """CREATE TABLE daily_cards (
        card_date           TEXT PRIMARY KEY,
        covered_from        TEXT NOT NULL,
        covered_to          TEXT NOT NULL,
        schema_version      INTEGER NOT NULL,
        generated_at_utc    TEXT NOT NULL,
        generated_at_tehran TEXT NOT NULL,
        payload             TEXT NOT NULL
    )"""
    )
