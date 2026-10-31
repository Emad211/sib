"""v20 — checklist attributes on catalog items and the optional thresholds (A3, A4, E1).

Three additive columns on `catalog_item_versions`: `responsible_role` (NULL means the role of the item's
place), `frequency` (every shift, daily or weekly) and `sort_order` (shelf order; the install list was
inserted in shelf order, so each item keeps the position of its first row, in steps of ten so a manager can
slide a new item between two). The old rows are only read to fill the new column; nothing else changes.

Five `setting_versions` rows with an empty value: the sugar, systolic, diastolic and oxygen-pressure
thresholds and the cash float. An empty value means «not set», and a rule that reads it then stays quiet
(owner decision 11, 2026-10-31).
"""

from sqlalchemy import Connection

BASELINE_SHIFT = "0001-01-01#1"  # `ShiftKey.baseline()`: applies to every shift ever
ROLES = "'reception', 'nurse', 'storekeeper', 'purchaser', 'accountant', 'shift_doctor', 'manager'"

_STATEMENTS = (
    f"ALTER TABLE catalog_item_versions ADD COLUMN responsible_role TEXT "
    f"CHECK (responsible_role IS NULL OR responsible_role IN ({ROLES}))",
    "ALTER TABLE catalog_item_versions ADD COLUMN frequency TEXT NOT NULL DEFAULT 'every_shift' "
    "CHECK (frequency IN ('every_shift', 'daily', 'weekly'))",
    "ALTER TABLE catalog_item_versions ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0)",
    "UPDATE catalog_item_versions SET sort_order = 10 * ("
    "SELECT MIN(first.id) FROM catalog_item_versions AS first WHERE first.item_id = catalog_item_versions.item_id)",
)
THRESHOLDS = (
    "sugar_threshold",
    "systolic_threshold",
    "diastolic_threshold",
    "oxygen_pressure_threshold",
    "cash_float_toman",
)


def up(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(
        "INSERT INTO setting_versions (setting_key, value, version, effective_from_shift, changed_at, changed_by) "
        "VALUES (?, '', 1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'migration:m0020')",
        [(key, BASELINE_SHIFT) for key in THRESHOLDS],
    )
