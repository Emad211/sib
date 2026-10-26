"""Build a realistic, fully synthetic accounting database (ACC-0.4).

No row comes from the production database. The generator is parameterised only
with *aggregate* ratios measured on the production copy on 2026-10-26
(see docs/accounting/A01_CURRENT_STATE_AUDIT.md). Names, prices, national IDs
and phone numbers are invented.

The schema is created by the application itself (schema.sql + runtime
migrations), and invoice totals are computed by the application's own
`InvoiceRepository.update_invoice_totals`, so the data is consistent with
whatever the code under test considers correct.

Usage:
    python tools/make_synthetic_db.py out.db [--days 60] [--per-day 40] [--seed 1405]
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT = os.path.dirname(_HERE)
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

import bcrypt  # noqa: E402

# --------------------------------------------------------------------------
# Aggregate parameters (production ratios, rounded) — NOT individual data
# --------------------------------------------------------------------------
INSURANCE_MIX = [
    ("آزاد", 0.67),
    ("تامین اجتماعی", 0.23),
    ("خدمات درمانی", 0.04),
    ("نیروهای مسلح", 0.03),
    ("بانک نمونه", 0.03),
]
SUPPLEMENTARY_RATE = 0.022  # invoices with supplementary insurance
SHIFT_MIX = [("morning", 0.29), ("evening", 0.34), ("night", 0.37)]
P_VISIT = 0.43  # invoices with a visit
P_INJECTION = 0.85  # invoices with >=1 nursing service
INJECTIONS_PER_INVOICE = 4.3  # mean, among invoices that have them
P_CONSUMABLE = 0.50
P_PROCEDURE = 0.034
P_PROCEDURE_BY_NURSE = 0.63
P_NO_NATIONAL_ID = 0.74
P_NO_PHONE = 0.67
PAYMENT_MIX = [("card", 0.991), ("cash", 0.003), (None, 0.006)]
P_ITEM_DELETED = 0.012  # logged deletions per invoice

# Shift windows (local clock hours) used to place timestamps.
SHIFT_HOURS = {"morning": (7, 14), "evening": (14, 20), "night": (20, 31)}  # 31 = 07:00 next day

# --------------------------------------------------------------------------
# Synthetic catalogue (invented prices)
# --------------------------------------------------------------------------
VISIT_TARIFFS = [
    # insurance_type, tariff_price, nursing_covers, is_supplementary, is_base_tariff
    ("آزاد", 250000, 0, 0, 1),
    ("تامین اجتماعی", 120000, 1, 0, 0),
    ("خدمات درمانی", 120000, 1, 0, 0),
    ("نیروهای مسلح", 100000, 1, 0, 0),
    ("بانک نمونه", 0, 0, 0, 0),
    ("تکمیلی نمونه", 0, 0, 1, 0),
]
NURSING_SERVICES = [
    ("تزریق داخل سرم", 60000),
    ("تزریق عضلانی", 40000),
    ("تزریق از راه ست سرم", 50000),
    ("وصل سرم نیم لیتری", 120000),
    ("وصل سرم یک لیتری", 160000),
    ("تست اکسیژن", 30000),
    ("تست قند", 30000),
    ("کنترل فشار", 20000),
    ("نوار قلب", 150000),
    ("تزریق وریدی", 50000),
    ("تزریق زیرجلدی", 40000),
    ("تزریق آنتی بیوتیک با تست", 90000),
    ("پانسمان ساده", 70000),
    ("شستشوی گوش", 100000),
    ("نبولایزر", 80000),
]
NURSING_WEIGHTS = [38, 15, 14, 7, 1, 7, 2, 1.5, 0.6, 0.8, 0.8, 1.5, 1, 0.5, 1]
CONSUMABLES = [
    ("روتختی", "supply", 10000),
    ("لیدوکائین", "drug", 15000),
    ("سرنگ ساده", "supply", 5000),
    ("سرنگ انسولین", "supply", 6000),
    ("آنژیوکت", "supply", 25000),
    ("آب مقطر", "drug", 8000),
    ("ست سرم و آنژیوکت", "supply", 45000),
    ("ب کمپلکس", "drug", 20000),
    ("آپوتل", "drug", 90000),
    ("ویتامین C", "drug", 18000),
    ("پنتوپرازول", "drug", 110000),
    ("هیدروکورتیزون", "drug", 70000),
    ("اندانسترون", "drug", 60000),
    ("سرم نرمال 500", "drug", 85000),
    ("بتامتازون", "drug", 30000),
    ("پیروکسیکام", "drug", 25000),
    ("کتورولاک", "drug", 30000),
    ("دگزامتازون", "drug", 15000),
    ("تیغ بیستوری", "supply", 10000),
    ("نخ بخیه 3/0", "supply", 60000),
]
CONSUMABLE_WEIGHTS = [32, 12, 8, 7, 6, 3, 3, 3, 2.2, 2, 1.7, 1.5, 1.3, 1, 1, 1, 0.8, 0.8, 0.5, 0.5]
PROCEDURES = [
    ("بخیه ساده", 300000),
    ("پانسمان سوختگی", 250000),
    ("کشیدن ناخن", 400000),
    ("خارج کردن جسم خارجی", 350000),
    ("شستشوی زخم", 150000),
]

FIRST_NAMES = [
    "علی",
    "مریم",
    "رضا",
    "زهرا",
    "حسین",
    "فاطمه",
    "مهدی",
    "سارا",
    "امیر",
    "نرگس",
    "محمد",
    "لیلا",
    "حمید",
    "مینا",
    "سعید",
    "الهام",
    "جواد",
    "شیما",
    "کاظم",
    "نازنین",
]
LAST_NAMES = [
    "آزمونی",
    "نمونه‌پور",
    "ساختگی",
    "تستی",
    "فرضی‌زاده",
    "مثالی",
    "آزمایشی",
    "داده‌پور",
    "نمونه‌وند",
    "ساختارزاده",
    "جعلی‌پور",
    "فرضیان",
]

# Test accounts. Passwords are for the synthetic DB only.
TEST_PASSWORD = "synthetic-pass"
TEST_USERS = [
    # username, role, full_name, is_active
    ("manager1", "manager", "مدیر آزمون", 1),
    ("reception1", "reception", "پذیرش یک", 1),
    ("reception2", "reception", "پذیرش دو", 1),
    ("reception_old", "reception", "پذیرش غیرفعال", 0),
    ("doctor1", "doctor", "دکتر نمونه", 1),
]
DOCTORS = ["دکتر الف", "دکتر ب", "دکتر ج", "دکتر د"]
NURSES = ["پرستار الف", "پرستار ب", "پرستار ج"]


@dataclass
class Stats:
    patients: int = 0
    invoices: int = 0
    open_invoices: int = 0
    items: int = 0


def valid_national_id(rng: random.Random) -> str:
    """A 10-digit Iranian national ID with a correct check digit (invented)."""
    while True:
        digits = [rng.randint(0, 9) for _ in range(9)]
        if len(set(digits)) == 1:
            continue
        s = sum(d * (10 - i) for i, d in enumerate(digits)) % 11
        check = s if s < 2 else 11 - s
        return "".join(map(str, digits)) + str(check)


def _pick(rng: random.Random, mix):
    r, acc = rng.random(), 0.0
    for value, p in mix:
        acc += p
        if r < acc:
            return value
    return mix[-1][0]


def _ts(d: date, shift: str, rng: random.Random) -> datetime:
    lo, hi = SHIFT_HOURS[shift]
    minutes = rng.randint(lo * 60, hi * 60 - 1)
    return datetime(d.year, d.month, d.day) + timedelta(minutes=minutes)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _init_schema(path: str):
    """Let the application create its own schema and run its own migrations."""
    import src.adapters.sqlite.core as core
    from src.app import create_app
    from src.config.settings import Config

    old_path, old_flag = Config.DATABASE_PATH, core._migrations_done
    Config.DATABASE_PATH = path
    core._migrations_done = False
    app = create_app({"TESTING": True, "DATABASE_PATH": path})
    return app, (old_path, old_flag)


def build(path: str, *, days: int = 60, per_day: int = 40, seed: int = 1405, end: date = date(2026, 9, 24)) -> Stats:
    if os.path.exists(path):
        os.remove(path)
    rng = random.Random(seed)
    stats = Stats()
    app, restore = _init_schema(path)
    import src.adapters.sqlite.core as core
    from src.adapters.sqlite.invoices_repo import InvoiceRepository
    from src.config.settings import Config

    with app.app_context():
        db = core.get_db()
        db.execute("""CREATE TABLE IF NOT EXISTS shift_staff (
            work_date TEXT NOT NULL, shift TEXT NOT NULL, doctor_id INTEGER, nurse_id INTEGER,
            PRIMARY KEY (work_date, shift))""")

        # ---- staff & users -------------------------------------------------
        doctor_ids, nurse_ids = [], []
        for n in DOCTORS:
            doctor_ids.append(
                db.execute("INSERT INTO medical_staff (full_name, staff_type) VALUES (?, 'doctor')", (n,)).lastrowid
            )
        for n in NURSES:
            nurse_ids.append(
                db.execute("INSERT INTO medical_staff (full_name, staff_type) VALUES (?, 'nurse')", (n,)).lastrowid
            )
        pw = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt(rounds=4))
        user_ids = {}
        for username, role, full_name, active in TEST_USERS:
            staff_id = doctor_ids[0] if role == "doctor" else None
            user_ids[username] = db.execute(
                "INSERT INTO users (username, password_hash, role, full_name, is_active, staff_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, pw, role, full_name, active, staff_id),
            ).lastrowid
        for sid in doctor_ids + nurse_ids:
            db.execute(
                """INSERT INTO payroll_settings (staff_id, base_morning, base_evening, base_night,
                visit_fee, injection_percent, procedure_percent, tax_percent, nursing_percent,
                nurse_procedure_percent) VALUES (?, 1500000, 1700000, 2000000, 50000, 10, 30, 10, 20, 25)""",
                (sid,),
            )

        # ---- catalogue -----------------------------------------------------
        for ins, price, covers, supp, base in VISIT_TARIFFS:
            db.execute(
                """INSERT INTO visit_tariffs (insurance_type, tariff_price, nursing_covers,
                is_supplementary, is_base_tariff) VALUES (?, ?, ?, ?, ?)""",
                (ins, price, covers, supp, base),
            )
        db.execute("""INSERT INTO visit_tariffs (insurance_type, tariff_price, is_active)
                      VALUES ('بیمهٔ منسوخ', 90000, 0)""")
        svc_ids = []
        for name, price in NURSING_SERVICES:
            svc_ids.append(
                db.execute(
                    "INSERT INTO nursing_services (service_name, unit_price) VALUES (?, ?)", (name, price)
                ).lastrowid
            )
        db.execute("INSERT INTO nursing_services (service_name, unit_price, is_active) VALUES ('خدمت منسوخ', 10000, 0)")
        # one exclusion: ECG is not covered by تامین اجتماعی
        db.execute(
            "INSERT INTO insurance_nursing_exclusions (insurance_type, nursing_service_id) VALUES (?, ?)",
            ("تامین اجتماعی", svc_ids[8]),
        )
        for name, cat, price in CONSUMABLES:
            db.execute(
                "INSERT INTO consumable_tariffs (name, category, default_price) VALUES (?, ?, ?)", (name, cat, price)
            )
        for name, price in PROCEDURES:
            db.execute("INSERT INTO procedure_tariffs (name, unit_price) VALUES (?, ?)", (name, price))
        for k, v in [
            ("clinic_name", "درمانگاه آزمون"),
            ("clinic_phone", "000"),
            ("clinic_address", "نشانی آزمون"),
            ("auto_backup", "1"),
        ]:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))

        # ---- patients ------------------------------------------------------
        n_patients = max(50, int(days * per_day / 1.11))
        used_nids = set()
        patient_rows = []
        for _i in range(n_patients):
            nid = None
            if rng.random() >= P_NO_NATIONAL_ID:
                nid = valid_national_id(rng)
                while nid in used_nids:
                    nid = valid_national_id(rng)
                used_nids.add(nid)
            phone = None if rng.random() < P_NO_PHONE else "09" + "".join(str(rng.randint(0, 9)) for _ in range(9))
            patient_rows.append(
                (
                    rng.choice(FIRST_NAMES),
                    rng.choice(LAST_NAMES),
                    nid,
                    phone,
                    _pick(rng, INSURANCE_MIX),
                    1 if nid is None else 0,
                    "reception1",
                )
            )
        db.executemany(
            """INSERT INTO patients (name, family_name, national_id, phone_number, insurance_type,
            is_foreign, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            patient_rows,
        )
        stats.patients = n_patients
        patient_ids = [r[0] for r in db.execute("SELECT id FROM patients ORDER BY id")]
        db.commit()

        # ---- invoices ------------------------------------------------------
        repo = InvoiceRepository()
        receptions = ["reception1", "reception2"]
        start = end - timedelta(days=days - 1)
        for day_idx in range(days):
            d = start + timedelta(days=day_idx)
            wd = d.isoformat()
            for shift, _ in SHIFT_MIX:
                db.execute(
                    "INSERT OR REPLACE INTO shift_staff VALUES (?, ?, ?, ?)",
                    (wd, shift, rng.choice(doctor_ids), rng.choice(nurse_ids)),
                )
            for _ in range(max(1, int(rng.gauss(per_day, per_day * 0.15)))):
                shift = _pick(rng, SHIFT_MIX)
                pid = rng.choice(patient_ids)
                ins = _pick(rng, INSURANCE_MIX)
                supp = "تکمیلی نمونه" if rng.random() < SUPPLEMENTARY_RATE else None
                rec = rng.choice(receptions)
                doc, nurse = rng.choice(doctor_ids), rng.choice(nurse_ids)
                opened = _ts(d, shift, rng)
                inv = db.execute(
                    """INSERT INTO invoices (patient_id, doctor_id, nurse_id, status, insurance_type,
                    supplementary_insurance, total_amount, work_date, shift, opened_at, opened_by, opened_by_name)
                    VALUES (?, ?, ?, 'open', ?, ?, 0, ?, ?, ?, ?, ?)""",
                    (pid, doc, nurse, ins, supp, wd, shift, _fmt(opened), rec, rec),
                ).lastrowid
                t = opened
                items = []  # (item_type, item_id)

                def tick():
                    nonlocal t
                    t += timedelta(minutes=rng.randint(1, 6))
                    return _fmt(t)

                if rng.random() < P_VISIT:
                    price = VISIT_TARIFFS[0][1]
                    vid = db.execute(
                        """INSERT INTO visits (patient_id, doctor_name, visit_date, shift, work_date,
                        insurance_type, supplementary_insurance, price, reception_user, invoice_id, doctor_id,
                        nurse_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            pid,
                            DOCTORS[doctor_ids.index(doc)],
                            tick(),
                            shift,
                            wd,
                            ins,
                            supp,
                            price,
                            rec,
                            inv,
                            doc,
                            nurse,
                            _fmt(t),
                        ),
                    ).lastrowid
                    items.append(("visit", vid))
                if rng.random() < P_INJECTION:
                    for _ in range(max(1, round(rng.expovariate(1 / INJECTIONS_PER_INVOICE)))):
                        k = rng.choices(range(len(NURSING_SERVICES)), weights=NURSING_WEIGHTS)[0]
                        name, price = NURSING_SERVICES[k]
                        iid = db.execute(
                            """INSERT INTO injections (patient_id, injection_type, service_id,
                            injection_date, shift, work_date, count, unit_price, total_price, reception_user,
                            invoice_id, doctor_id, nurse_id) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                            (pid, name, svc_ids[k], tick(), shift, wd, price, price, rec, inv, doc, nurse),
                        ).lastrowid
                        items.append(("injection", iid))
                if rng.random() < P_CONSUMABLE:
                    for _ in range(rng.choice([1, 1, 1, 2])):
                        k = rng.choices(range(len(CONSUMABLES)), weights=CONSUMABLE_WEIGHTS)[0]
                        name, cat, price = CONSUMABLES[k]
                        qty = rng.choices([1, 2, 3, 4, 5], weights=[93, 3.4, 1, 1, 0.8])[0]
                        provided = 1 if rng.random() < 0.001 else 0
                        cid = db.execute(
                            """INSERT INTO consumables_ledger (patient_id, item_name, category, quantity,
                            unit_price, total_cost, patient_provided, usage_date, shift, work_date, reception_user,
                            invoice_id, doctor_id, nurse_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                pid,
                                name,
                                cat,
                                qty,
                                price,
                                0 if provided else price * qty,
                                provided,
                                tick(),
                                shift,
                                wd,
                                rec,
                                inv,
                                doc,
                                nurse,
                            ),
                        ).lastrowid
                        items.append(("consumable", cid))
                if rng.random() < P_PROCEDURE:
                    name, price = rng.choice(PROCEDURES)
                    by_nurse = rng.random() < P_PROCEDURE_BY_NURSE
                    prid = db.execute(
                        """INSERT INTO procedures (patient_id, procedure_type, procedure_date, shift,
                        work_date, price, reception_user, invoice_id, performer_type, performer_id, doctor_id,
                        nurse_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            pid,
                            name,
                            tick(),
                            shift,
                            wd,
                            price,
                            rec,
                            inv,
                            "nurse" if by_nurse else "doctor",
                            nurse if by_nurse else doc,
                            doc,
                            nurse,
                        ),
                    ).lastrowid
                    items.append(("procedure", prid))

                is_last_day = day_idx == days - 1
                stays_open = is_last_day and rng.random() < 0.15
                for item_type, item_id in items:
                    ptype = _pick(rng, PAYMENT_MIX)
                    paid = 0 if stays_open and rng.random() < 0.5 else 1
                    db.execute(
                        """INSERT INTO invoice_item_payments (invoice_id, item_type, item_id, payment_type,
                        is_paid, updated_at) VALUES (?, ?, ?, ?, ?, ?)""",
                        (inv, item_type, item_id, ptype, paid, tick()),
                    )
                stats.items += len(items)
                db.execute(
                    """INSERT INTO activity_logs (user_id, username, action_type, action_category,
                    description, invoice_id, patient_id, created_at)
                    VALUES (?, ?, 'invoice_create', 'invoice', 'ایجاد فاکتور', ?, ?, ?)""",
                    (user_ids[rec], rec, inv, pid, _fmt(opened)),
                )
                if rng.random() < P_ITEM_DELETED:
                    db.execute(
                        """INSERT INTO activity_logs (user_id, username, action_type, action_category,
                        description, invoice_id, patient_id, created_at)
                        VALUES (?, ?, 'injection_delete', 'injection', 'حذف تزریق از فاکتور', ?, ?, ?)""",
                        (user_ids[rec], rec, inv, pid, tick()),
                    )
                db.commit()
                repo.update_invoice_totals(inv)
                if stays_open:
                    stats.open_invoices += 1
                else:
                    db.execute(
                        """UPDATE invoices SET status='closed', closed_at=?, closed_by=?, closed_by_name=?
                        WHERE id=?""",
                        (tick(), rec, rec, inv),
                    )
                    db.execute(
                        """INSERT INTO activity_logs (user_id, username, action_type, action_category,
                        description, invoice_id, patient_id, created_at)
                        VALUES (?, ?, 'invoice_close', 'invoice', 'بستن فاکتور', ?, ?, ?)""",
                        (user_ids[rec], rec, inv, pid, _fmt(t)),
                    )
                stats.invoices += 1
            db.commit()

        # Edge case: one stale open invoice from 10 days ago.
        stale_day = (end - timedelta(days=10)).isoformat()
        db.execute(
            """INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift,
            opened_at, opened_by, opened_by_name) VALUES (?, 'open', 'آزاد', 0, ?, 'night', ?, 'reception2',
            'reception2')""",
            (patient_ids[0], stale_day, stale_day + " 23:10:00"),
        )
        stats.open_invoices += 1
        stats.invoices += 1
        for username in ("reception1", "reception2"):
            db.execute(
                "INSERT INTO user_active_shift (user_id, active_shift, work_date, shift_started_at) "
                "VALUES (?, 'morning', ?, ?)",
                (user_ids[username], end.isoformat(), end.isoformat() + " 07:00:00"),
            )
        db.commit()
        core.close_connection(None)

    Config.DATABASE_PATH, core._migrations_done = restore
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--per-day", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1405)
    a = ap.parse_args(argv)
    s = build(a.out, days=a.days, per_day=a.per_day, seed=a.seed)
    print(
        f"synthetic DB written: {a.out} — {s.patients} patients, {s.invoices} invoices "
        f"({s.open_invoices} open), {s.items} items"
    )
    conn = sqlite3.connect(a.out)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


if __name__ == "__main__":
    main()
