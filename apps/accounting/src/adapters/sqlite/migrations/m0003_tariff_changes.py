"""v3 — who changed which tariff, when, from what to what (ACC-2.8, A06 §3.5).

A new table only, so older program versions keep working on this database (ADR-002): they
simply do not write to it. Rows are never updated or deleted by the program.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tariff_changes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               changed_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
               username TEXT,
               kind TEXT NOT NULL,
               tariff_id INTEGER,
               label TEXT,
               action TEXT NOT NULL,
               field TEXT,
               old_value TEXT,
               new_value TEXT
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tariff_changes_changed_at ON tariff_changes (changed_at)")
