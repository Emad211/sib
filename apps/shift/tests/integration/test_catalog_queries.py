"""Version lookups seek the per-entity index and need no temporary sort (SHOULD-3, NICE-2)."""

import pytest

from shift.adapters.db import catalog_repo, settings_repo
from shift.adapters.db.core import Database


def _plan(database: Database, sql: str, parameters: tuple[str, ...]) -> list[str]:
    with database.read() as connection:
        return [str(row[-1]) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, parameters)]


@pytest.mark.parametrize(
    ("table", "columns", "keys"),
    [
        ("catalog_item_versions", catalog_repo._ITEM_COLUMNS, ("item_id",)),
        ("catalog_location_versions", catalog_repo._LOCATION_COLUMNS, ("location_id",)),
    ],
)
def test_effective_reads_seek_the_entity_index_without_a_temp_sort(
    database: Database, table: str, columns: tuple[str, ...], keys: tuple[str, ...]
) -> None:
    plan = _plan(database, catalog_repo._effective_sql(table, columns, keys), ("2026-10-28#2",))

    assert any("USING INDEX" in line or "USING COVERING INDEX" in line for line in plan)
    assert not any("TEMP B-TREE" in line for line in plan)


def test_the_settings_snapshot_has_no_window_function_or_temp_sort(database: Database) -> None:
    plan = _plan(database, settings_repo._EFFECTIVE_SQL, ("2026-10-28#2",))

    assert "ROW_NUMBER" not in settings_repo._EFFECTIVE_SQL
    assert any("idx_setting_versions_effective" in line for line in plan)
    assert not any("TEMP B-TREE" in line for line in plan)
