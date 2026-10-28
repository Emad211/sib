"""The reference seeder writes synthetic catalog data only, and re-running it is a no-op."""

from datetime import date
from pathlib import Path

from clocks import FixedClock, inside_shift
from seed_reference_data import ITEMS, LOCATIONS, main

from shift.adapters.db.core import Database
from shift.domain.policy import SettingKey, ShiftKey
from shift.services.catalog import CatalogService, ItemKind
from shift.services.settings import SettingsService

DAY = date(2026, 10, 28)


def _seed(tmp_path: Path) -> Database:
    path = tmp_path / "shift.db"
    assert main(["--db", str(path)]) == 0
    return Database(path)


def _version_count(database: Database) -> tuple[int, int]:
    with database.read() as connection:
        locations = connection.exec_driver_sql("SELECT COUNT(*) FROM catalog_location_versions").scalar_one()
        items = connection.exec_driver_sql("SELECT COUNT(*) FROM catalog_item_versions").scalar_one()
    return int(locations), int(items)


def test_seeding_creates_the_reference_catalog(tmp_path: Path) -> None:
    database = _seed(tmp_path)
    try:
        catalog = CatalogService(database)

        assert len(catalog.locations()) == len(LOCATIONS)
        assert len(catalog.items()) == len(ITEMS)
        assert {location.name for location in catalog.locations()} >= {"قفسهٔ پذیرش", "انبار اصلی"}
    finally:
        database.close()


def test_every_seeded_item_is_countable_and_unique(tmp_path: Path) -> None:
    database = _seed(tmp_path)
    try:
        items = CatalogService(database).items()

        counted = [item for item in items if item.kind is ItemKind.COUNTED]
        assert all(item.acc_code for item in counted)
        assert len({item.acc_code for item in counted}) == len(counted)
        assert {item.location_id for item in items} <= {location.location_id for location in LOCATIONS}
    finally:
        database.close()


def test_seeding_twice_adds_no_version(tmp_path: Path) -> None:
    database = _seed(tmp_path)
    try:
        before = _version_count(database)

        assert main(["--db", str(tmp_path / "shift.db")]) == 0

        assert _version_count(database) == before == (len(LOCATIONS), len(ITEMS))
    finally:
        database.close()


def test_seeding_applies_from_the_baseline_and_leaves_settings_alone(tmp_path: Path) -> None:
    database = _seed(tmp_path)
    try:
        catalog = CatalogService(database)
        settings = SettingsService(database)

        assert {item.effective_from_shift for item in catalog.items()} == {ShiftKey.baseline()}
        assert {len(settings.history(key)) for key in SettingKey} == {1}
        assert {settings.value(key).changed_by for key in SettingKey} == {"migration:m0004"}
    finally:
        database.close()


def test_seeding_refuses_to_backdate_a_runtime_change(tmp_path: Path) -> None:
    path = tmp_path / "shift.db"
    assert main(["--db", str(path)]) == 0
    database = Database(path)
    try:
        catalog = CatalogService(database, clock=FixedClock(inside_shift(DAY, 1)))
        catalog.save_location(
            "shelf_reception", name="renamed", actor_id="staff-manager", running_shift=ShiftKey(DAY, 1)
        )
    finally:
        database.close()

    assert main(["--db", str(path)]) == 1


def test_every_seed_row_round_trips_into_the_catalog(tmp_path: Path) -> None:
    database = _seed(tmp_path)
    try:
        items = {item.item_id: item for item in CatalogService(database).items()}

        for seed in ITEMS:
            stored = items[seed.item_id]
            assert (stored.name, stored.kind, stored.location_id, stored.unit) == (
                seed.name,
                seed.kind,
                seed.location_id,
                seed.unit,
            )
            assert (stored.minimum, stored.acc_code, stored.stock_factor) == (
                seed.minimum,
                seed.acc_code,
                seed.stock_factor,
            )
    finally:
        database.close()
