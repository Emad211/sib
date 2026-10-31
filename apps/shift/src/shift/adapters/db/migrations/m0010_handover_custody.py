"""v10 — who holds each flow's cash box and shelves now (SHF-7.1).

The manager's baseline names the custodian of a flow, and a flow's close hands that custody to the
incoming person, so the next flow derives its outgoing person from the log instead of staff typing one.
One row per flow kind, replaced on every baseline and every close: the cash box and the shelves of a
flow always belong to exactly one person (H-10, H-12).

The row is a read model of the event log, not a source of truth, so it is rebuilt from the events by
the service rather than by the projection engine. Additive only: no applied migration changes (ADR-002).
"""

from sqlalchemy import Connection


def up(connection: Connection) -> None:
    """Add the custody read model, one row per flow kind."""
    connection.exec_driver_sql(
        """CREATE TABLE handover_custody (
        flow         TEXT PRIMARY KEY CHECK (flow IN ('reception', 'nursing')),
        custodian_id TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        updated_by   TEXT NOT NULL
    )"""
    )
