"""The synthetic reference catalog: twelve invented items for tests and development.

These are not the clinic's items — those are the install seed in `tools/seed_reference_data.py`
(SHF-7.1) — but a small, deliberate catalog where every value is invented (ADR-006). A few tests use
these ids as load-bearing names; change them together with those tests, never silently.
"""

import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sqlalchemy import Connection  # noqa: E402

from shift.adapters.db import catalog_repo  # noqa: E402
from shift.adapters.db.core import Database  # noqa: E402
from shift.common.clock import SystemClock, utc_iso  # noqa: E402
from shift.domain.policy import ShiftKey  # noqa: E402
from shift.services.catalog import CatalogError, ItemKind  # noqa: E402

SEEDED_BY = "tests:synthetic_catalog"
BASELINE_SHIFT = ShiftKey.baseline().text


class LocationSeed(NamedTuple):
    """A place stock is counted in (S03 §1)."""

    location_id: str
    name: str


class ItemSeed(NamedTuple):
    """A countable or checklist item; the names are placeholders for the manager's real list."""

    item_id: str
    name: str
    kind: ItemKind
    location_id: str
    unit: str
    minimum: int
    acc_code: str | None
    stock_factor: int
    responsible_role: str | None = None


LOCATIONS: tuple[LocationSeed, ...] = (
    LocationSeed("shelf_reception", "قفسهٔ پذیرش"),
    LocationSeed("cpr_room", "اتاق CPR"),
    LocationSeed("trolley", "ترالی"),
    LocationSeed("doctor_room", "اتاق پزشک"),
    LocationSeed("public_area", "فضای عمومی"),
    LocationSeed("main_store", "انبار اصلی"),
)

ITEMS: tuple[ItemSeed, ...] = (
    ItemSeed("para_500_amp", "پاراستامول ۵۰۰ میلی‌گرم", ItemKind.COUNTED, "shelf_reception", "آمپول", 10, "10001", 1),
    ItemSeed("diclofenac_75_amp", "دیکلوفناک ۷۵", ItemKind.COUNTED, "shelf_reception", "آمپول", 6, "10002", 1),
    ItemSeed("saline_500_bag", "نرمال سالین ۵۰۰ سی‌سی", ItemKind.COUNTED, "cpr_room", "کیسه", 4, "10003", 1),
    ItemSeed("oxygen_mask", "ماسک اکسیژن", ItemKind.COUNTED, "cpr_room", "عدد", 2, "10004", 1),
    ItemSeed("glove_latex_m", "دستکش لاتکس متوسط", ItemKind.COUNTED, "trolley", "عدد", 500, "10005", 100),
    ItemSeed("alcohol_pad", "پد الکلی", ItemKind.COUNTED, "trolley", "عدد", 150, "10006", 50),
    ItemSeed("gauze_roll", "رول گاز", ItemKind.COUNTED, "doctor_room", "رول", 4, "10007", 1),
    ItemSeed("speculum", "اسپکولوم", ItemKind.COUNTED, "doctor_room", "عدد", 2, "10008", 1),
    ItemSeed("paper_towel", "دستمال کاغذی", ItemKind.COUNTED, "public_area", "بسته", 2, "10009", 1),
    ItemSeed("sharps_box", "باکس اجسام تیز", ItemKind.COUNTED, "public_area", "عدد", 1, "10010", 1),
    ItemSeed("fridge_temperature", "دمای یخچال دارو", ItemKind.READING, "main_store", "درجه", 0, None, 1, "reception"),
    ItemSeed("store_lock_ok", "قفل انبار سالم است", ItemKind.YES_NO, "main_store", "وضعیت", 0, None, 1, "reception"),
    # A3: the trolley checklist belongs to the nursing flow (owner decision 8), so these two are the
    # nursing lines the handover asks; the storekeeper's lines come up in the reception handover (ADR-035).
    ItemSeed("trolley_seal_ok", "پلمب ترالی سالم است", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1, "nurse"),
    ItemSeed(
        "oxygen_pressure_trolley", "فشار کپسول اکسیژن ترالی", ItemKind.READING, "trolley", "عدد", 0, None, 1, "nurse"
    ),
)


def _install_location(connection: Connection, seed: LocationSeed, changed_at: str) -> None:
    latest = catalog_repo.find_location_latest(connection, seed.location_id)
    if latest is not None:
        if (latest.name, latest.retired) != (seed.name, False):
            raise CatalogError(f"location {seed.location_id!r} changed at runtime; reference seeding is install-only")
        return
    catalog_repo.insert_location_version(
        connection,
        location_id=seed.location_id,
        name=seed.name,
        retired=False,
        version=1,
        effective_from_shift=BASELINE_SHIFT,
        changed_at=changed_at,
        changed_by=SEEDED_BY,
    )


def _install_item(connection: Connection, seed: ItemSeed, changed_at: str) -> None:
    requested = (
        seed.name,
        seed.kind.value,
        seed.location_id,
        seed.unit,
        seed.minimum,
        seed.acc_code,
        seed.stock_factor,
        False,
    )
    latest = catalog_repo.find_item_latest(connection, seed.item_id)
    if latest is not None:
        stored = (
            latest.name,
            latest.kind,
            latest.location_id,
            latest.unit,
            latest.minimum,
            latest.acc_code,
            latest.stock_factor,
            latest.retired,
        )
        if stored != requested:
            raise CatalogError(f"item {seed.item_id!r} changed at runtime; reference seeding is install-only")
        return
    if seed.acc_code is not None:
        owner = catalog_repo.find_item_with_acc_code(connection, seed.acc_code, exclude_item_id=seed.item_id)
        if owner is not None:
            raise CatalogError(f"acc_code {seed.acc_code!r} already belongs to item {owner!r}")
    place = catalog_repo.find_location_effective(connection, seed.location_id, BASELINE_SHIFT)
    if place is None or place.retired:
        raise CatalogError(f"location {seed.location_id!r} is missing from the reference catalog")
    catalog_repo.insert_item_version(
        connection,
        item_id=seed.item_id,
        name=seed.name,
        kind=seed.kind.value,
        location_id=seed.location_id,
        unit=seed.unit,
        minimum=seed.minimum,
        acc_code=seed.acc_code,
        stock_factor=seed.stock_factor,
        retired=False,
        version=1,
        effective_from_shift=BASELINE_SHIFT,
        changed_at=changed_at,
        changed_by=SEEDED_BY,
        responsible_role=seed.responsible_role,
    )


def seed(database: Database) -> tuple[int, int]:
    """Install the reference catalog at the baseline; returns the catalog size afterwards."""
    changed_at = utc_iso(SystemClock().now_utc())
    with database.write() as connection:
        for location in LOCATIONS:
            _install_location(connection, location, changed_at)
        for item in ITEMS:
            _install_item(connection, item, changed_at)
        locations = catalog_repo.list_locations_effective(connection, BASELINE_SHIFT)
        items = catalog_repo.list_items_effective(connection, BASELINE_SHIFT)
    return len(locations), len(items)
