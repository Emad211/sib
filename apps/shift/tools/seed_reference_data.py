"""Seed the synthetic reference catalog: locations and items only, never patient data.

Run once after install, or in a development checkout:

    python apps/shift/tools/seed_reference_data.py --db shift.db

Re-running is safe: an unchanged entity gets no new version. The four policy defaults are
not seeded here — migration `0004` owns them.
"""

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shift.adapters.db.core import Database  # noqa: E402
from shift.domain.policy import ShiftKey  # noqa: E402
from shift.services.catalog import CatalogService, ItemKind  # noqa: E402

SEEDED_BY = "tool:seed_reference_data"


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
    ItemSeed("fridge_temperature", "دمای یخچال دارو", ItemKind.READING, "main_store", "درجه", 0, None, 1),
    ItemSeed("store_lock_ok", "قفل انبار سالم است", ItemKind.YES_NO, "main_store", "وضعیت", 0, None, 1),
)


def seed(catalog: CatalogService, *, running_shift: ShiftKey | None = None) -> tuple[int, int]:
    """Write every reference location and item; returns the catalog size afterwards."""
    for location in LOCATIONS:
        catalog.save_location(location.location_id, name=location.name, actor_id=SEEDED_BY, running_shift=running_shift)
    for item in ITEMS:
        catalog.save_item(
            item.item_id,
            name=item.name,
            kind=item.kind,
            location_id=item.location_id,
            unit=item.unit,
            minimum=item.minimum,
            acc_code=item.acc_code,
            stock_factor=item.stock_factor,
            actor_id=SEEDED_BY,
            running_shift=running_shift,
        )
    return len(catalog.locations(running_shift)), len(catalog.items(running_shift))


def main(argv: list[str] | None = None) -> int:
    """Migrate the database, then seed it; exits non-zero if the catalog cannot be written."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to the shift SQLite database")
    args = parser.parse_args(argv)

    database = Database(args.db)
    try:
        database.migrate()
        locations, items = seed(CatalogService(database))
    finally:
        database.close()

    print(f"reference catalog ready: {locations} locations, {items} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
