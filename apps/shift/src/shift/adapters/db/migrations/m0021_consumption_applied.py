"""v21 — accounting consumption becomes stock events: what the mirror remembers about each line (A1).

`acc_consumption` was a copy and nothing else. Each line now also says what the bridge did with it:

- `applied_event_id` / `applied_key` — the `acc.consumption` event that took the line off a shelf,
  and the idempotency key it was written under. The key names the close and the quantity, so a line
  accounting rewrote shows a different desired key and is reversed before it is applied again.
- `skipped` — decided without an event: the line arrived after the item's shelf had already been
  settled for the flow it belongs to (the physical count saw the consumption, H-11 for stock), or it
  rounds to nothing.

A line with neither column set is still waiting: its accounting code has no counted catalog item yet,
and the bridge retries it on every poll. Additive only, so an installed v20 database keeps every row.
"""

from sqlalchemy import Connection

_STATEMENTS = (
    "ALTER TABLE acc_consumption ADD COLUMN applied_event_id TEXT",
    "ALTER TABLE acc_consumption ADD COLUMN applied_key TEXT",
    "ALTER TABLE acc_consumption ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_acc_consumption_invoice ON acc_consumption (invoice_id)",
)


def up(connection: Connection) -> None:
    """Add the three bookkeeping columns and the invoice index the reconcile step reads."""
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
