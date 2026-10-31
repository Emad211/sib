"""v14 — the monthly team goal the manager sets beside the reward rates (owner decision 7, D2).

Older than the app itself: «هدف تیمی» is one number per Jalali month, so `reward_team_goals` holds one
row per month and saving the same month again fixes the number instead of stacking a second one. The
goal is only a display target — nothing is paid from it and no projection reads it — so it sits beside
`reward_rates` and shares its shape: who set it and when.

Additive only: a new table, nothing else is touched, so an installed v13 database keeps every row it
had (ADR-002).
"""

from sqlalchemy import Connection

_STATEMENTS = (
    """CREATE TABLE reward_team_goals (
        month      TEXT NOT NULL PRIMARY KEY,
        goal_works INTEGER NOT NULL,
        set_by     TEXT NOT NULL,
        set_at     TEXT NOT NULL
    )""",
)


def up(connection: Connection) -> None:
    """Add the monthly team-goal table; nothing else is touched."""
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
