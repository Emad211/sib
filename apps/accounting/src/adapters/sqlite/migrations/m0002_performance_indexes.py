"""v2 — indexes for the slow manager reports (PERF-01).

Measured on the 2026-09-24 production copy: the per-day "closed invoices of a work_date"
join used `idx_invoices_status_opened_at` (status is not selective: 99.97% closed) and
took ~11 ms per day; with (work_date, status) it is a covering-index search at ~0.15 ms.
Indexes are additive, so older program versions keep working on this database.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_work_date_status ON invoices (work_date, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_patient_work_date ON invoices (patient_id, work_date)")
