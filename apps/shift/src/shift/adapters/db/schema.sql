-- Baseline tables for the projection engine (v1).
-- The event log is SQLite-specific and lives in sqlite/ddl.sql (step 4).

CREATE TABLE IF NOT EXISTS meta_projection (
    name      TEXT PRIMARY KEY,
    last_seq  INTEGER NOT NULL DEFAULT 0,
    checksum  TEXT,
    built_at  TEXT
);

CREATE TABLE IF NOT EXISTS proj_stock (
    item_id     TEXT NOT NULL,
    location_id TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    PRIMARY KEY (item_id, location_id)
);

CREATE TABLE IF NOT EXISTS proj_cash (
    shift_id TEXT PRIMARY KEY,
    expected INTEGER NOT NULL,
    counted  INTEGER NOT NULL,
    variance INTEGER NOT NULL
);

INSERT OR IGNORE INTO meta_projection (name, last_seq) VALUES ('proj_stock', 0), ('proj_cash', 0);
