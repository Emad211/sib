"""v9 — handover flows persist, cash keys by its box, and an event can name its flow (H-11, H-12).

`handover` holds one row per flow of one shift: the shift being closed (`shift_id`, D1), the flow, and
the instant the flow started — which is that flow's frozen cutoff (H-11) — plus the two people, the
signature, the early-leave flag and the manager who confirmed an emergency start (H-08). The unique
`(shift_id, flow)` key allows one reception flow and one nursing flow per shift and no third (H-12).

`proj_cash` is re-keyed per cash box (ADR-017 item 8). This clinic has a single box, `reception` (D2),
so the rows of the old per-shift table collapse onto it: the newest shift's row is carried over and the
older ones are dropped, because the event log still holds every count and receipt in full. `shift_id`
sorted as text is chronological (`YYYY-MM-DD#rank`). `meta_projection` keeps the row name `proj_cash`,
so a rebuild and the live table stay comparable, and the copy is empty on a fresh install (H-12).

`events.handover_id` is metadata, never part of the hash: `domain/event_hash.py` and every stored hash
stay exactly as they are, so an upgraded database verifies to the same chain it had before, and a
`NULL` reads as "this event belongs to no flow".
"""

from sqlalchemy import Connection

# D2: one cash box exists and the reception flow owns it (`CashBox.RECEPTION`, frozen here on purpose).
RECEPTION_BOX_ID = "reception"


def up(connection: Connection) -> None:
    """Add the flow table, re-key the cash projection by box, and let events name their flow."""
    _handover(connection)
    _cash_box_projection(connection)
    connection.exec_driver_sql("ALTER TABLE events ADD COLUMN handover_id TEXT")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_events_handover_seq ON events (handover_id, seq)")


def _handover(connection: Connection) -> None:
    """One row per flow; `opened_at` is the flow's own cutoff (H-11) and the pair of flows is unique."""
    connection.exec_driver_sql(
        """CREATE TABLE handover (
        handover_id  TEXT PRIMARY KEY,
        shift_id     TEXT NOT NULL,
        flow         TEXT NOT NULL CHECK (flow IN ('reception', 'nursing')),
        opened_at    TEXT NOT NULL,
        work_date    TEXT NOT NULL,
        outgoing_id  TEXT NOT NULL,
        incoming_id  TEXT NOT NULL,
        signed_at    TEXT,
        early_left   INTEGER NOT NULL DEFAULT 0,
        emergency_by TEXT,
        created_at   TEXT NOT NULL,
        UNIQUE (shift_id, flow)
    )"""
    )
    connection.exec_driver_sql("CREATE INDEX idx_handover_signed_at ON handover (signed_at)")
    connection.exec_driver_sql("CREATE INDEX idx_handover_flow_opened_at ON handover (flow, opened_at)")


def _cash_box_projection(connection: Connection) -> None:
    """Move `proj_cash` from a row per shift to a row per cash box, keeping the newest figure."""
    connection.exec_driver_sql(
        "CREATE TABLE proj_cash_box (cash_box_id TEXT PRIMARY KEY, expected INTEGER NOT NULL, "
        "counted INTEGER NOT NULL, variance INTEGER NOT NULL)"
    )
    connection.exec_driver_sql(
        "INSERT INTO proj_cash_box (cash_box_id, expected, counted, variance) "
        "SELECT ?, expected, counted, variance FROM proj_cash ORDER BY shift_id DESC LIMIT 1",
        (RECEPTION_BOX_ID,),
    )
    connection.exec_driver_sql("DROP TABLE proj_cash")
    connection.exec_driver_sql("ALTER TABLE proj_cash_box RENAME TO proj_cash")
