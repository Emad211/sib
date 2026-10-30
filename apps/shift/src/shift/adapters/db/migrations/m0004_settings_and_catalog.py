"""v4 — versioned settings and catalog, every change carrying its effective shift (R-08, R-09).

Nothing is updated or deleted here: a change appends a new version whose
`effective_from_shift` (`<work_date>#<rank>`) is the first shift that sees it, so a change
made while a shift runs can never reach that shift. The four settings rows seeded below are
the only values in the product — code holds the rules and the bounds, never the defaults.
"""

from sqlalchemy import Connection

from shift.adapters.db.sql_script import execute_script

SHIFT_KEY_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]#[1-3]"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS setting_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key          TEXT    NOT NULL,
    value                TEXT    NOT NULL,
    version              INTEGER NOT NULL CHECK (version >= 1),
    effective_from_shift TEXT    NOT NULL CHECK (effective_from_shift GLOB '{SHIFT_KEY_GLOB}'),
    changed_at           TEXT    NOT NULL,
    changed_by           TEXT    NOT NULL CHECK (length(changed_by) > 0),
    UNIQUE (setting_key, version)
);
CREATE INDEX IF NOT EXISTS idx_setting_versions_effective
    ON setting_versions (setting_key, effective_from_shift DESC, version DESC);

CREATE TABLE IF NOT EXISTS catalog_location_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id          TEXT    NOT NULL,
    name                 TEXT    NOT NULL CHECK (length(name) > 0),
    retired              INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1)),
    version              INTEGER NOT NULL CHECK (version >= 1),
    effective_from_shift TEXT    NOT NULL CHECK (effective_from_shift GLOB '{SHIFT_KEY_GLOB}'),
    changed_at           TEXT    NOT NULL,
    changed_by           TEXT    NOT NULL CHECK (length(changed_by) > 0),
    UNIQUE (location_id, version)
);
CREATE INDEX IF NOT EXISTS idx_catalog_location_versions_effective
    ON catalog_location_versions (location_id, effective_from_shift DESC, version DESC);

CREATE TABLE IF NOT EXISTS catalog_item_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id              TEXT    NOT NULL,
    name                 TEXT    NOT NULL CHECK (length(name) > 0),
    kind                 TEXT    NOT NULL CHECK (kind IN ('counted', 'yes_no', 'reading', 'photo')),
    location_id          TEXT    NOT NULL,
    unit                 TEXT    NOT NULL CHECK (length(unit) > 0),
    minimum              INTEGER NOT NULL DEFAULT 0 CHECK (minimum >= 0),
    acc_code             TEXT    CHECK (acc_code IS NULL OR length(acc_code) > 0),
    stock_factor         INTEGER NOT NULL DEFAULT 1 CHECK (stock_factor >= 1),
    retired              INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1)),
    version              INTEGER NOT NULL CHECK (version >= 1),
    effective_from_shift TEXT    NOT NULL CHECK (effective_from_shift GLOB '{SHIFT_KEY_GLOB}'),
    changed_at           TEXT    NOT NULL,
    changed_by           TEXT    NOT NULL CHECK (length(changed_by) > 0),
    UNIQUE (item_id, version)
);
CREATE INDEX IF NOT EXISTS idx_catalog_item_versions_effective
    ON catalog_item_versions (item_id, effective_from_shift DESC, version DESC);

CREATE TABLE IF NOT EXISTS catalog_staff_role_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id             TEXT    NOT NULL,
    role                 TEXT    NOT NULL
        CHECK (role IN ('reception', 'nurse', 'storekeeper', 'purchaser', 'accountant',
                        'shift_doctor', 'manager')),
    granted              INTEGER NOT NULL CHECK (granted IN (0, 1)),
    version              INTEGER NOT NULL CHECK (version >= 1),
    effective_from_shift TEXT    NOT NULL CHECK (effective_from_shift GLOB '{SHIFT_KEY_GLOB}'),
    changed_at           TEXT    NOT NULL,
    changed_by           TEXT    NOT NULL CHECK (length(changed_by) > 0),
    UNIQUE (staff_id, role, version)
);
CREATE INDEX IF NOT EXISTS idx_catalog_staff_role_versions_effective
    ON catalog_staff_role_versions (staff_id, effective_from_shift DESC, version DESC);
"""

DEFAULTS: tuple[tuple[str, str], ...] = (
    ("card_time", "07:30"),
    ("takeover_window_minutes", "30"),
    ("withdrawal_cap_toman", "300000"),
    ("session_ttl_hours", "12"),
)
BASELINE_SHIFT = "0001-01-01#1"  # `ShiftKey.baseline()`: applies to every shift ever


def up(connection: Connection) -> None:
    execute_script(connection, SCHEMA)
    connection.exec_driver_sql(
        "INSERT INTO setting_versions (setting_key, value, version, effective_from_shift, changed_at, changed_by) "
        "VALUES (?, ?, 1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'migration:m0004')",
        [(key, value, BASELINE_SHIFT) for key, value in DEFAULTS],
    )
