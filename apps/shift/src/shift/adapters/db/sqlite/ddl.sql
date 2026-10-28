-- SQLite-specific event log (step 4). `server_at` is the authoritative UTC instant
-- from gap S3 (the design's at_utc); `at_tehran` is its fixed-offset rendering.
CREATE TABLE IF NOT EXISTS events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,   -- client_event_id: offline-minted ULID (S3)
    client_event_id TEXT GENERATED ALWAYS AS (event_id) VIRTUAL,
    type            TEXT NOT NULL,
    schema_v        INTEGER NOT NULL DEFAULT 1 CHECK (schema_v >= 1),
    payload         TEXT NOT NULL,
    actor_id        TEXT NOT NULL CHECK (length(actor_id) > 0),
    device_id       TEXT NOT NULL CHECK (length(device_id) > 0),
    shift_id        TEXT,
    work_date       TEXT NOT NULL CHECK (work_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    client_at       TEXT NOT NULL,
    server_at       TEXT NOT NULL,
    at_utc          TEXT GENERATED ALWAYS AS (server_at) VIRTUAL,  -- the design's at_utc
    at_tehran       TEXT NOT NULL,
    ref_event_id    TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_shift_seq ON events (shift_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type_seq ON events (type, seq);
