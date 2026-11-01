"""v22 — the two-step login secrets of the head and the manager (ADR-027).

One row per person who started a two-step setup: the sealed TOTP secret (never in clear text, see
`common/seal.py`), when it was confirmed with a first code (NULL while still being set up), and the
last time step used, so a code cannot be replayed. Additive only: a v21 database keeps every row.
"""

from sqlalchemy import Connection

_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS staff_totp (
        staff_id     TEXT PRIMARY KEY REFERENCES staff(staff_id),
        secret       TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        enabled_at   TEXT,
        last_step    INTEGER NOT NULL DEFAULT 0
    )""",
)


def up(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
