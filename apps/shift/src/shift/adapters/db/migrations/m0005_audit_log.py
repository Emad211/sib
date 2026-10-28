"""v5 — `audit_log`: the audit trail, kept in its own table and out of the public event log."""

from sqlalchemy import Connection

from shift.adapters.db.sql_script import execute_script

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at_utc     TEXT NOT NULL,
    at_tehran  TEXT NOT NULL,
    actor_id   TEXT NOT NULL CHECK (length(actor_id) > 0),
    action     TEXT NOT NULL CHECK (length(action) > 0),
    outcome    TEXT NOT NULL CHECK (outcome IN ('ok', 'denied', 'failed')),
    subject_id TEXT,
    detail     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_log_at_utc ON audit_log (at_utc);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor_at ON audit_log (actor_id, at_utc);
CREATE INDEX IF NOT EXISTS idx_audit_log_action_at ON audit_log (action, at_utc);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


def up(connection: Connection) -> None:
    execute_script(connection, _DDL)
