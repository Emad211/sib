"""v2 — auth and devices: staff, roles, sessions, enrolled devices, one-time tokens.

Additive only (ADR-002): v1 databases keep their rows and gain the tables below. Every
timestamp is ISO-8601 UTC text (`...Z`), so `expires_at > :now` is a plain string compare.
A session is a 32-byte random token whose SHA-256 is stored here; the raw token only
ever lives in the HttpOnly cookie. `pin_hash` is an argon2id digest, never a PIN.
"""

from sqlalchemy import Connection

_DDL = (
    # `mobile` is the login identity, so it is unique for the life of the row.
    """CREATE TABLE staff (
    staff_id        TEXT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    mobile          TEXT NOT NULL UNIQUE,
    pin_hash        TEXT NOT NULL,
    pin_updated_at  TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until    TEXT,
    created_at      TEXT NOT NULL
)""",
    # A person may hold several roles (R-08); the PK prevents a duplicate grant.
    """CREATE TABLE staff_role (
    staff_id   TEXT NOT NULL REFERENCES staff (staff_id),
    role       TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (staff_id, role)
)""",
    "CREATE INDEX idx_staff_role_role ON staff_role (role)",
    """CREATE TABLE devices (
    device_id      TEXT PRIMARY KEY,
    staff_id       TEXT NOT NULL REFERENCES staff (staff_id),
    label          TEXT NOT NULL,
    pubkey         TEXT NOT NULL,
    enrolled_at    TEXT NOT NULL,
    revoked_at     TEXT,
    revoked_reason TEXT
)""",
    "CREATE INDEX idx_devices_staff ON devices (staff_id)",
    # The token row is minted before the device exists, so it carries the pre-bound
    # device_id: a token can only ever create that one device identity (single use).
    """CREATE TABLE enrollment_tokens (
    token_hash  TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL UNIQUE,
    staff_id    TEXT NOT NULL REFERENCES staff (staff_id),
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT
)""",
    "CREATE INDEX idx_enrollment_tokens_expires ON enrollment_tokens (expires_at)",
    """CREATE TABLE sessions (
    token_hash     TEXT PRIMARY KEY,
    csrf_token     TEXT NOT NULL,
    staff_id       TEXT NOT NULL REFERENCES staff (staff_id),
    device_id      TEXT NOT NULL REFERENCES devices (device_id),
    issued_at      TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    revoked_at     TEXT,
    revoked_reason TEXT
)""",
    # Partial indexes: revoking sessions (shift close, device loss) scans live rows only.
    "CREATE INDEX idx_sessions_staff ON sessions (staff_id) WHERE revoked_at IS NULL",
    "CREATE INDEX idx_sessions_device ON sessions (device_id) WHERE revoked_at IS NULL",
    "CREATE INDEX idx_sessions_expires ON sessions (expires_at)",
)


def up(connection: Connection) -> None:
    for statement in _DDL:
        connection.exec_driver_sql(statement)
