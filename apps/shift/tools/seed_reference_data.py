"""Seed the clinic's reference catalog: the 69 counted items, the trolley checklist, the locations.

Run once after install, or in a development checkout:

    python apps/shift/tools/seed_reference_data.py --db shift.db

No patient data: names and units only, and every counted item waits for its accounting code (Q-17).

The clinic's own list (the appendix of the owner's logic document): 69 counted items on the
reception shelf and in the CPR room, and the 14-item trolley checklist. It is install-time
data, so it starts at `ShiftKey.baseline()` and is visible to every shift. The **minimum and
the accounting code are the manager's** (Q-17): every item starts with `minimum=0` and
`acc_code=None`, and the manager fills them on `/manager/items` before the first blind count.
Re-running is safe: an unchanged entity gets no new row, and a catalog already changed at
runtime is refused rather than backdated. The four policy defaults are not seeded here —
migration `0004` owns them.
"""

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import Connection  # noqa: E402

from shift.adapters.db import catalog_repo  # noqa: E402
from shift.adapters.db.core import Database  # noqa: E402
from shift.common.clock import SystemClock, utc_iso  # noqa: E402
from shift.domain.policy import ShiftKey  # noqa: E402
from shift.services.catalog import CatalogError, ItemKind  # noqa: E402

SEEDED_BY = "tool:seed_reference_data"
BASELINE_SHIFT = ShiftKey.baseline().text


class LocationSeed(NamedTuple):
    """A place stock is counted in (S03 §1)."""

    location_id: str
    name: str


class ItemSeed(NamedTuple):
    """One item of the clinic list; its minimum and accounting code wait for the manager."""

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
    ItemSeed("apotel_500_amp", "آپوتل", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("lidocaine_amp", "لیدوکائین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("diclofenac_75_supp", "شیاف دیکلوفناک", ItemKind.COUNTED, "shelf_reception", "شیاف", 0, None, 1),
    ItemSeed("pantoprazole_vial", "پنتوپرازول", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("neurobion_amp", "نوربیون", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("biperiden_amp", "بای‌پریدین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("haloperidol_amp", "هالوپریدول", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("ondansetron_amp", "اندانسترون", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("chlorpromazine_amp", "کلرپرومازین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("methylprednisolone_vial", "متیل‌پردنیزولون", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("cefazolin_vial", "سفازولین", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("ceftizoxime_vial", "سفتی‌زوکسیم", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("ceftazidime_vial", "سفتازیدیم", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("penicillin_633_vial", "پنی‌سیلین ۶.۳.۳", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("penicillin_800_vial", "پنی‌سیلین ۸۰۰", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("penicillin_1200_vial", "پنی‌سیلین ۱۲۰۰", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("betamethasone_la_amp", "بتامتازون LA", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("betamethasone_amp", "بتامتازون", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("dexamethasone_amp", "دگزامتازون", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("hydrocortisone_vial", "هیدروکورتیزون", ItemKind.COUNTED, "shelf_reception", "ویال", 0, None, 1),
    ItemSeed("triamcinolone_amp", "تریامسینولون", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("ketorolac_amp", "کتورولاک", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("piroxicam_amp", "پیروکسیکام", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("methocarbamol_amp", "متوکاربامول", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("metoclopramide_amp", "متوکلوپرامید", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("dicyclomine_amp", "دی‌سیکلومین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("hyoscine_amp", "هیوسین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("famotidine_amp", "فاموتیدین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("chlorpheniramine_amp", "کلرفنیرامین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("clemastine_amp", "کلماستین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("promethazine_amp", "پرومتازین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("trifluoperazine_amp", "تری‌فلوپرازین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("diazepam_amp", "دیازپام", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("furosemide_amp", "فوروزماید", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("aminophylline_amp", "آمینوفیلین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("gentamicin_amp", "جنتامایسین", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("b_complex_amp", "ب‌کمپلکس", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("b6_300_amp", "B6 300", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("b12_amp", "B12", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("vitamin_c_amp", "ویتامین C", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("vitamin_d_amp", "ویتامین D", ItemKind.COUNTED, "shelf_reception", "آمپول", 0, None, 1),
    ItemSeed("angiocath_pink", "آنژیوکت صورتی", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("angiocath_blue", "آنژیوکت آبی", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("angiocath_purple", "آنژیوکت بنفش", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("angiocath_yellow", "آنژیوکت زرد", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("iv_set", "ست سرم", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("insulin_syringe", "سرنگ انسولین", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("glucose_test_strip", "نوار تست قند", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("glucose_test_needle", "سوزن تست قند", ItemKind.COUNTED, "shelf_reception", "عدد", 0, None, 1),
    ItemSeed("dw_1000", "D/W 1000", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("dw_500", "D/W 500", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("ns_1000", "N/S 1000", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("ns_500", "N/S 500", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("third_two_thirds_1000", "⅓-⅔ 1000", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("third_two_thirds_500", "⅓-⅔ 500", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("ringer_1000", "رینگر 1000", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("ringer_500", "رینگر 500", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("ds_1000", "D/S 1000", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("ds_500", "D/S 500", ItemKind.COUNTED, "cpr_room", "بطری", 0, None, 1),
    ItemSeed("bistoury_blade", "تیغ بیستوری ۱۱ / ۲۰", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("suture_2_0", "نخ بخیه ۲/۰", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("suture_3_0", "نخ بخیه ۳/۰", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("suture_4_0", "نخ بخیه ۴/۰", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("suture_5_0", "نخ بخیه ۵/۰", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("surgifix", "سرجی‌فیکس", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("earring", "گوشواره", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("adhesive_tape", "چسب", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("razor", "ژیلت", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("oxygen_mask", "ماسک اکسیژن", ItemKind.COUNTED, "cpr_room", "عدد", 0, None, 1),
    ItemSeed("oxygen_cylinder_main_pressure", "فشار کپسول اکسیژن اصلی", ItemKind.READING, "trolley", "عدد", 0, None, 1),
    ItemSeed(
        "oxygen_cylinder_portable_pressure", "فشار کپسول اکسیژن پرتابل", ItemKind.READING, "trolley", "عدد", 0, None, 1
    ),
    ItemSeed("manometer", "مانومتر", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("ambu_adult", "آمبوبگ بزرگسال", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("ambu_child", "آمبوبگ اطفال", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("laryngoscope", "لارنگوسکوپ (لامپ و باتری)", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("endotracheal_tube", "لوله‌ی تراشه (سایزها)", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("suction", "ساکشن (روشن و کار می‌کند)", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("defibrillator", "DC شوک (شارژ و ژل)", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("monitor", "مانیتور", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("bp_glucometer", "فشارسنج و گلوکومتر", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("trolley_seal_intact", "پلمب ترالی سالم است", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("trolley_drug_expiry", "انقضای داروهای ترالی", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
    ItemSeed("room_tidiness", "نظم و نظافت اتاق", ItemKind.YES_NO, "trolley", "وضعیت", 0, None, 1),
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


def _install_item(connection: Connection, seed: ItemSeed, changed_at: str, position: int) -> None:
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
        sort_order=position,
    )


def seed(database: Database) -> tuple[int, int]:
    """Install the reference catalog at the baseline; returns the catalog size afterwards."""
    changed_at = utc_iso(SystemClock().now_utc())
    with database.write() as connection:
        for location in LOCATIONS:
            _install_location(connection, location, changed_at)
        for position, item in enumerate(ITEMS, start=1):
            # The list is in shelf order (the owner's appendix); the position keeps it that way in the app.
            _install_item(connection, item, changed_at, position * 10)
        locations = catalog_repo.list_locations_effective(connection, BASELINE_SHIFT)
        items = catalog_repo.list_items_effective(connection, BASELINE_SHIFT)
    return len(locations), len(items)


def main(argv: list[str] | None = None) -> int:
    """Migrate the database, then seed it; exits non-zero if the catalog cannot be written."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to the shift SQLite database")
    args = parser.parse_args(argv)

    database = Database(args.db)
    try:
        database.migrate()
        locations, items = seed(database)
    except CatalogError as error:
        print(f"reference catalog not seeded: {error}", file=sys.stderr)
        return 1
    finally:
        database.close()

    print(f"reference catalog ready: {locations} locations, {items} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
