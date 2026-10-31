"""v13 — the reward rate table and the accounting identity map (S01 §5, SHF-5).

Two additive changes and nothing else. `reward_rates` is the manager's amount per work type per
month: one row per (work type, month), so saving the same month again fixes the amount instead of
stacking a second one. An earning record freezes the amount it was issued with, so a later change
never rewrites a payment — the table only answers "what is this work worth in that month".

`staff.acc_user` maps a shift staff row to the accounting user id that did the work. Accounting user
ids are not shift staff ids (shf5-DESIGN.md §3, decision 4), so the manager sets the mapping; a
person he has not mapped earns nothing and is listed for him instead of being invented. The index is
partial — `acc_user` is nullable and only a mapped row must be unique.

Additive only: a new table, one nullable column and one partial unique index — no trigger, no view,
no stored projection — so an installed v12 database keeps every row it had (ADR-002).
"""

from sqlalchemy import Connection

_STATEMENTS = (
    """CREATE TABLE reward_rates (
        work_type    TEXT NOT NULL,
        month        TEXT NOT NULL,
        amount_toman INTEGER NOT NULL,
        set_by       TEXT NOT NULL,
        set_at       TEXT NOT NULL,
        PRIMARY KEY (work_type, month)
    )""",
    "ALTER TABLE staff ADD COLUMN acc_user TEXT",
    "CREATE UNIQUE INDEX idx_staff_acc_user ON staff (acc_user) WHERE acc_user IS NOT NULL",
)


def up(connection: Connection) -> None:
    """Add the rate table and the one nullable mapping column; nothing else is touched."""
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
