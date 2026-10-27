"""v6 — voiding / refunding a closed invoice (FB-25).

An invoice is never deleted: the manager marks it status='voided' with a reason, and the original total
and (for refunds) the payment method are kept here. Every report counts status='closed' only, so a voided
invoice leaves revenue without touching any report query. New table only (ADR-002); the previous program
version ignores the table and simply never lists the invoice as open or closed.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS invoice_voids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL UNIQUE,
    kind TEXT NOT NULL,          -- 'void' (cancelled) or 'refund' (money handed back)
    amount REAL NOT NULL,        -- invoice total at the moment of voiding
    refund_method TEXT,          -- cash / card / ... for refunds
    reason TEXT NOT NULL,
    voided_by TEXT,
    voided_at TIMESTAMP DEFAULT (datetime('now', '+3 hours', '+30 minutes')),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id)
);"""
    )
