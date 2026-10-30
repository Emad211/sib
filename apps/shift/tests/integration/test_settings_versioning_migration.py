"""Migration 0004 creates the version tables, 0008 seeds the schedule, and neither reseeds."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from shift.adapters.db import migrations
from shift.adapters.db.core import Database
from shift.adapters.db.migrations import m0004_settings_and_catalog, m0007_catalog_fixes, m0008_schedule_settings
from shift.domain.policy import Role, SettingKey, ShiftKey
from shift.services.catalog import ItemKind

TABLES = (
    "setting_versions",
    "catalog_location_versions",
    "catalog_item_versions",
    "catalog_staff_role_versions",
)
INDEXES = (
    "idx_setting_versions_effective",
    "idx_catalog_location_versions_effective",
    "idx_catalog_item_versions_effective",
    "idx_catalog_staff_role_versions_effective",
    "idx_catalog_item_versions_acc_code",
)
ITEM_DEFAULTS = {
    "item_id": "para_500_amp",
    "name": "پاراستامول",
    "kind": "counted",
    "location_id": "shelf_reception",
    "unit": "آمپول",
    "minimum": 0,
    "acc_code": "10001",
    "stock_factor": 1,
    "retired": 0,
    "version": 1,
    "effective_from_shift": "2026-10-28#2",
    "changed_at": "2026-10-28T00:00:00.000Z",
    "changed_by": "test",
}


def _names(database: Database, kind: str) -> set[str]:
    with database.read() as connection:
        rows = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
        return {str(row[0]) for row in rows}


def _pending_after(version: int) -> list[str]:
    """The one registry is the spec: every migration numbered after `version` must apply."""
    return [f"{number:04d}_{name}" for number, name, _ in migrations.MIGRATIONS if number > version]


def _database_at_version(path: Path, version: int) -> Database:
    """Build a genuine old database by applying only the migrations up to `version`."""
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for number, _name, up in migrations.MIGRATIONS:
            if number > version:
                break
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            up(connection)
            connection.exec_driver_sql(f"PRAGMA user_version = {number}")
            connection.exec_driver_sql("COMMIT")
    engine.dispose()
    return Database(path)


def _setting_version(database: Database, **overrides: Any) -> None:
    values = {
        "setting_key": "card_time",
        "value": "08:00",
        "version": 2,
        "effective_from_shift": "2026-10-28#2",
        "changed_at": "2026-10-28T00:00:00.000Z",
        "changed_by": "test",
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    with database.write() as connection:
        connection.exec_driver_sql(f"INSERT INTO setting_versions ({columns}) VALUES ({placeholders})", values)


def _item_version(database: Database, **overrides: Any) -> None:
    values = dict(ITEM_DEFAULTS)
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    with database.write() as connection:
        connection.exec_driver_sql(f"INSERT INTO catalog_item_versions ({columns}) VALUES ({placeholders})", values)


def _role_version(database: Database, **overrides: Any) -> None:
    values = {
        "staff_id": "staff-1",
        "role": "nurse",
        "granted": 1,
        "version": 1,
        "effective_from_shift": "2026-10-28#2",
        "changed_at": "2026-10-28T00:00:00.000Z",
        "changed_by": "test",
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    with database.write() as connection:
        connection.exec_driver_sql(
            f"INSERT INTO catalog_staff_role_versions ({columns}) VALUES ({placeholders})", values
        )


def test_migration_creates_the_version_tables_and_lookup_indexes(database: Database) -> None:
    assert set(TABLES) <= _names(database, "table")
    assert set(INDEXES) <= _names(database, "index")


def test_the_migrations_seed_the_setting_defaults(database: Database) -> None:
    with database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT setting_key, value, version, effective_from_shift, changed_by FROM setting_versions "
            "ORDER BY setting_key"
        ).mappings()

    assert [dict(row) for row in rows] == [
        {
            "setting_key": "card_time",
            "value": "07:30",
            "version": 1,
            "effective_from_shift": "0001-01-01#1",
            "changed_by": "migration:m0004",
        },
        {
            # Retired by ADR-018 (the card's Friday gap is clinic policy, not a setting): the row
            # stays as history and `SettingsService.snapshot` ignores it.
            "setting_key": "daily_card_skip_weekday",
            "value": "4",
            "version": 1,
            "effective_from_shift": "0001-01-01#1",
            "changed_by": "migration:m0008",
        },
        {
            "setting_key": "session_ttl_hours",
            "value": "12",
            "version": 1,
            "effective_from_shift": "0001-01-01#1",
            "changed_by": "migration:m0004",
        },
        {
            "setting_key": "takeover_window_minutes",
            "value": "30",
            "version": 1,
            "effective_from_shift": "0001-01-01#1",
            "changed_by": "migration:m0004",
        },
        {
            "setting_key": "weekly_count_time",
            "value": "07:30",
            "version": 1,
            "effective_from_shift": "0001-01-01#1",
            "changed_by": "migration:m0008",
        },
        {
            "setting_key": "weekly_count_weekday",
            "value": "0",
            "version": 1,
            "effective_from_shift": "0001-01-01#1",
            "changed_by": "migration:m0008",
        },
        {
            "setting_key": "withdrawal_cap_toman",
            "value": "300000",
            "version": 1,
            "effective_from_shift": "0001-01-01#1",
            "changed_by": "migration:m0004",
        },
    ]


def test_the_migrations_seed_every_known_setting_key(database: Database) -> None:
    seeded = {key for key, _ in m0004_settings_and_catalog.DEFAULTS}
    seeded |= {key for key, _ in m0008_schedule_settings.DEFAULTS}

    assert {key.value for key in SettingKey} <= seeded, "every policy key must have a seeded row"
    # ADR-018 retired `daily_card_skip_weekday`; its seeded row stays as history (R-03).
    assert "daily_card_skip_weekday" in seeded
    assert m0004_settings_and_catalog.BASELINE_SHIFT == ShiftKey.baseline().text
    assert m0008_schedule_settings.BASELINE_SHIFT == ShiftKey.baseline().text


def test_migrating_again_does_not_reseed(database: Database) -> None:
    database.migrate()

    with database.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM setting_versions").scalar_one() == 7


def test_upgrading_a_version_three_database_applies_every_pending_migration(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    database = _database_at_version(path, 3)
    with database.write() as connection:
        connection.exec_driver_sql("INSERT INTO meta_projection (name, last_seq) VALUES ('kept', 7)")
    database.close()

    upgraded = Database(path)
    try:
        report = upgraded.migrate(backup_dir=tmp_path / "backups")

        assert report.applied == _pending_after(3)
        assert report.to_version == migrations.LATEST
        assert report.backup is not None
        assert set(TABLES) <= _names(upgraded, "table")
        assert set(INDEXES) <= _names(upgraded, "index")
        with upgraded.read() as connection:
            assert (
                connection.exec_driver_sql("SELECT last_seq FROM meta_projection WHERE name = 'kept'").scalar_one() == 7
            )
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM setting_versions").scalar_one() == 7
    finally:
        upgraded.close()


def test_upgrading_a_version_four_database_applies_every_later_migration(tmp_path: Path) -> None:
    path = tmp_path / "v4.db"
    database = _database_at_version(path, 4)
    _item_version(database, item_id="kept_item", acc_code="90001")
    database.close()

    upgraded = Database(path)
    try:
        report = upgraded.migrate(backup_dir=tmp_path / "backups")

        assert report.applied == _pending_after(4)
        assert report.to_version == migrations.LATEST
        assert "idx_catalog_item_versions_acc_code" in _names(upgraded, "index")
        with upgraded.read() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM catalog_item_versions WHERE item_id = 'kept_item'"
                ).scalar_one()
                == 1
            )
    finally:
        upgraded.close()


def test_the_catalog_fix_index_backs_the_acc_code_lookup(database: Database) -> None:
    with database.read() as connection:
        plan = [
            str(row[-1])
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT item_id FROM catalog_item_versions "
                "WHERE acc_code = ? AND item_id != ? LIMIT 1",
                ("10001", "other_item"),
            )
        ]

    assert any("idx_catalog_item_versions_acc_code" in line for line in plan)


def test_the_catalog_fix_migration_is_idempotent(database: Database) -> None:
    with database.write() as connection:
        m0007_catalog_fixes.up(connection)
        m0007_catalog_fixes.up(connection)


def test_a_shift_key_of_another_shape_is_rejected(database: Database) -> None:
    with pytest.raises(IntegrityError):
        _setting_version(database, effective_from_shift="2026-10-28")
    with pytest.raises(IntegrityError):
        _setting_version(database, effective_from_shift="2026-10-28#4")


def test_the_schema_enforces_the_role_vocabulary(database: Database) -> None:
    for role in Role:
        _role_version(database, role=role.value)

    with pytest.raises(IntegrityError):
        _role_version(database, staff_id="staff-2", role="owner")


def test_the_schema_enforces_the_item_kind_vocabulary(database: Database) -> None:
    for position, kind in enumerate(ItemKind):
        _item_version(database, item_id=f"item_{position}", acc_code=f"9{position}", kind=kind.value)

    with pytest.raises(IntegrityError):
        _item_version(database, item_id="scales", kind="weight")


@pytest.mark.parametrize(("column", "value"), [("stock_factor", 0), ("minimum", -1), ("retired", 2)])
def test_the_schema_enforces_item_bounds(database: Database, column: str, value: int) -> None:
    with pytest.raises(IntegrityError):
        _item_version(database, **{column: value})


def test_a_version_number_must_be_positive(database: Database) -> None:
    with pytest.raises(IntegrityError):
        _setting_version(database, version=0)


def test_the_same_version_number_cannot_be_written_twice(database: Database) -> None:
    _setting_version(database, version=2)

    with pytest.raises(IntegrityError):
        _setting_version(database, version=2, value="09:00")


def test_a_version_must_name_its_author(database: Database) -> None:
    with pytest.raises(IntegrityError):
        _setting_version(database, changed_by="")
