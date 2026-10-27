"""v5 — clinic expenses (FB-20), and person+date indexes for payroll.

Payroll asked "this doctor's / nurse's work in these days" and the planner walked every invoice
(the status index is not selective): one call took ~0.4 s, a 90-day salary chart 35 s. With these
indexes the 90-day chart takes 0.4 s (measured on the 2026-09-24 production copy).

A new table only (ADR-002). Rows are never deleted; a wrong entry is voided with a reason.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS expenses (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               spent_on TEXT NOT NULL,
               category TEXT NOT NULL,
               amount REAL NOT NULL,
               note TEXT,
               created_by TEXT,
               created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
               is_void INTEGER NOT NULL DEFAULT 0,
               void_reason TEXT,
               voided_by TEXT,
               voided_at TIMESTAMP
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_spent_on ON expenses (spent_on)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_injections_doctor_work_date ON injections (doctor_id, work_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_injections_nurse_work_date ON injections (nurse_id, work_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_doctor_work_date ON visits (doctor_id, work_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_procedures_doctor_work_date ON procedures (doctor_id, work_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_procedures_nurse_work_date ON procedures (nurse_id, work_date)")
