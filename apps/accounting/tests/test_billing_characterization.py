"""
Characterization / golden-master tests for webapp billing logic.

These tests PIN the CURRENT behaviour of InvoiceRepository exactly as it
runs today.  They do NOT assert what the logic "should" do — they assert
what it DOES.  Any future refactor that breaks one of these tests means a
real behaviour change occurred and must be reviewed.

Isolation guarantee:
  - The `webapp_db` fixture (conftest.py) patches Config.DATABASE_PATH to a
    fresh temp SQLite file before any get_db() call.
  - The session-scoped `_guard_real_db` fixture asserts clinic_new.db is
    byte-for-byte unchanged after the whole run.
  - No real SMS is sent; no scheduler runs (TESTING=True).
"""

import pytest
from src.adapters.sqlite.invoices_repo import InvoiceRepository

# ---------------------------------------------------------------------------
# Helpers — seed minimal rows into the isolated DB
# ---------------------------------------------------------------------------

def _insert_patient(db, name="بیمار تست"):
    cur = db.execute(
        "INSERT INTO patients (name, family_name, national_id) VALUES (?, ?, ?)",
        (name, "آزمون", f"000{name[:5]}")
    )
    db.commit()
    return cur.lastrowid


def _insert_tariff(db, insurance_type, tariff_price, *,
                   nursing_covers=0, nursing_tariff=0.0,
                   is_active=1, is_supplementary=0, is_base_tariff=0):
    db.execute(
        """INSERT OR IGNORE INTO visit_tariffs
           (insurance_type, tariff_price, nursing_covers, nursing_tariff,
            is_active, is_supplementary, is_base_tariff)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (insurance_type, tariff_price, nursing_covers, nursing_tariff,
         is_active, is_supplementary, is_base_tariff)
    )
    db.commit()


def _open_invoice(db, patient_id, insurance_type="آزاد", supplementary=None):
    cur = db.execute(
        """INSERT INTO invoices
           (patient_id, insurance_type, supplementary_insurance, status, total_amount, work_date, shift)
           VALUES (?, ?, ?, 'open', 0, '2024-01-01', 'morning')""",
        (patient_id, insurance_type, supplementary)
    )
    db.commit()
    return cur.lastrowid


def _insert_nursing_service(db, service_name, unit_price=50000.0):
    cur = db.execute(
        "INSERT INTO nursing_services (service_name, unit_price) VALUES (?, ?)",
        (service_name, unit_price)
    )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# 1. ویزیت آزاد (بدون بیمه) → patient_share = تعرفه پایه
# ---------------------------------------------------------------------------

class TestFreeVisitPatientShare:
    """ویزیت بدون بیمه: patient_share باید برابر تعرفه پایه/آزاد باشد."""

    def test_free_visit_uses_base_tariff(self, webapp_db):
        db = webapp_db
        # تعرفه پایه/آزاد: is_base_tariff=1
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        pid = _insert_patient(db, "آزاد")
        inv_id = _open_invoice(db, pid, insurance_type="آزاد")
        # ثبت ویزیت بدون بیمه تکمیلی
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, supplementary_insurance,
                price, work_date)
               VALUES (?, ?, 'آزاد', NULL, 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        visits = [it for it in items if it['type'] == 'visit']
        assert len(visits) == 1, "باید یک ویزیت باشد"
        # Rule: visit without supplementary → patient_share = tariff of insurance_type
        # For 'آزاد' the tariff row has tariff_price=150000 AND is_base_tariff=1
        assert visits[0]['patient_share'] == 150000.0, (
            f"patient_share آزاد باید 150000 باشد، ولی {visits[0]['patient_share']} است"
        )

    def test_free_visit_no_insurance_share(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 200000.0, is_base_tariff=1)
        pid = _insert_patient(db, "آزاد2")
        inv_id = _open_invoice(db, pid, insurance_type="آزاد")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, price, work_date)
               VALUES (?, ?, 'آزاد', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        v = [it for it in items if it['type'] == 'visit'][0]
        # Rule: no supplementary → insurance_share = recorded_price - patient_share (both same → 0)
        assert v['insurance_share'] == 0.0, (
            "بدون بیمه: insurance_share باید ۰ باشد"
        )
        assert v['covered_by_insurance'] == 0, (
            "بدون پوشش: covered_by_insurance باید ۰ باشد"
        )


# ---------------------------------------------------------------------------
# 2. ویزیت بیمه‌ای → patient_share = تعرفه آن بیمه
# ---------------------------------------------------------------------------

class TestInsuredVisitPatientShare:
    """ویزیت بیمه‌ای: patient_share = tariff_price ردیف آن بیمه."""

    def test_insured_visit_uses_insurance_tariff(self, webapp_db):
        db = webapp_db
        # تعرفه پایه/آزاد
        _insert_tariff(db, "آزاد", 200000.0, is_base_tariff=1)
        # تعرفه تامین اجتماعی (بیمه اصلی، is_supplementary=0)
        _insert_tariff(db, "تامین اجتماعی", 85000.0, is_supplementary=0)
        pid = _insert_patient(db, "بیمه")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, supplementary_insurance,
                price, work_date)
               VALUES (?, ?, 'تامین اجتماعی', NULL, 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        visits = [it for it in items if it['type'] == 'visit']
        assert len(visits) == 1
        # Rule: insurance visit → patient_share = insurance tariff (85000)
        assert visits[0]['patient_share'] == 85000.0, (
            f"patient_share تامین اجتماعی باید 85000 باشد، ولی {visits[0]['patient_share']} است"
        )

    def test_insured_visit_insurance_share_is_difference(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 200000.0, is_base_tariff=1)
        _insert_tariff(db, "خدمات درمانی", 60000.0, is_supplementary=0)
        pid = _insert_patient(db, "خدمات")
        inv_id = _open_invoice(db, pid, insurance_type="خدمات درمانی")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, price, work_date)
               VALUES (?, ?, 'خدمات درمانی', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        v = [it for it in items if it['type'] == 'visit'][0]
        # Rule: insurance_share = recorded_price - patient_share when recorded > patient
        # recorded_price = base_visit_price = 200000; patient_share = 60000 → insurance_share = 140000
        assert v['recorded_price'] == 200000.0
        assert v['patient_share'] == 60000.0
        assert v['insurance_share'] == 140000.0, (
            f"insurance_share باید 140000 باشد، ولی {v['insurance_share']} است"
        )


# ---------------------------------------------------------------------------
# 3. ویزیت بیمه‌ای تکمیلی → patient_share = تعرفه supplementary
# ---------------------------------------------------------------------------

class TestSupplementaryInsuranceOverride:
    """بیمه تکمیلی: patient_share = tariff تکمیلی (override بیمه اصلی)."""

    def test_supplementary_overrides_primary_tariff(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 200000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0, is_supplementary=0)
        # تکمیلی: is_supplementary=1
        _insert_tariff(db, "بیمه تکمیلی آسیا", 20000.0, is_supplementary=1)
        pid = _insert_patient(db, "تکمیلی")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی",
                               supplementary="بیمه تکمیلی آسیا")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, supplementary_insurance,
                price, work_date)
               VALUES (?, ?, 'تامین اجتماعی', 'بیمه تکمیلی آسیا', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        v = [it for it in items if it['type'] == 'visit'][0]
        # Rule: supplementary present → patient_share = supplementary tariff (20000)
        assert v['patient_share'] == 20000.0, (
            f"با بیمه تکمیلی patient_share باید 20000 باشد، ولی {v['patient_share']} است"
        )

    def test_supplementary_zero_tariff_gives_zero_patient_share(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 200000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0, is_supplementary=0)
        # تکمیلی با ۰ تومان سهم بیمار
        _insert_tariff(db, "بیمه تکمیلی رایگان", 0.0, is_supplementary=1)
        pid = _insert_patient(db, "تکمیلی0")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی",
                               supplementary="بیمه تکمیلی رایگان")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, supplementary_insurance,
                price, work_date)
               VALUES (?, ?, 'تامین اجتماعی', 'بیمه تکمیلی رایگان', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        v = [it for it in items if it['type'] == 'visit'][0]
        assert v['patient_share'] == 0.0, (
            "تکمیلی با تعرفه ۰: patient_share باید ۰ باشد"
        )
        # covered only when patient_share=0 AND recorded_price>0
        assert v['covered_by_insurance'] == 1, (
            "وقتی patient_share=0 و recorded_price>0: covered_by_insurance=1"
        )


# ---------------------------------------------------------------------------
# 4. تزریق پوشش‌دار (nursing_covers=1، غیرمستثنا) → patient_share = 0
# ---------------------------------------------------------------------------

class TestCoveredInjectionPatientShare:
    """تزریق پوشش‌دار: patient_share=0 وقتی nursing_covers=1 و service_id مستثنا نیست."""

    def test_covered_injection_not_excluded_patient_share_zero(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        # بیمه با nursing_covers=1
        _insert_tariff(db, "تامین اجتماعی", 85000.0,
                       nursing_covers=1, nursing_tariff=0.0, is_supplementary=0)
        svc_id = _insert_nursing_service(db, "آمپول IV")
        pid = _insert_patient(db, "تزریق")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        # تزریق پوشش‌دار، service_id مستثنا نیست
        db.execute(
            """INSERT INTO injections
               (patient_id, invoice_id, injection_type, service_id,
                total_price, work_date)
               VALUES (?, ?, 'آمپول IV', ?, 80000.0, '2024-01-01')""",
            (pid, inv_id, svc_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        inj = [it for it in items if it['type'] == 'injection'][0]
        # Rule: nursing_covers=1 AND not excluded → patient_share = 0
        assert inj['patient_share'] == 0.0, (
            f"تزریق پوشش‌دار غیرمستثنا: patient_share باید ۰ باشد، ولی {inj['patient_share']} است"
        )
        assert inj['covered_by_insurance'] == 1, "باید covered=1 باشد"
        assert inj['insurance_share'] == 80000.0, "insurance_share باید = total_price باشد"

    def test_covered_injection_with_legacy_nursing_tariff_zero_actual_behaviour(self, webapp_db):
        """
        PIN رفتار واقعی (characterization):
        وقتی nursing_covers=0 و nursing_tariff=0 در DB ذخیره است:
          - کد bool(0) → False می‌کند؛ پس nursing_covers is None شرط False است.
          - Legacy path هرگز trigger نمی‌شود.
          - نتیجه: patient_share = total_price (پوشش داده نمی‌شود).

        [BUG PINNED] کامنت کد ادعا می‌کند nursing_tariff==0 پوشش legacy دارد،
        اما شرط واقعی: `nursing_covers is None and nursing_tariff==0`.
        چون nursing_covers در DB مقدار 0 (نه NULL) دارد، bool(0)=False است و
        nursing_covers هرگز None نیست — legacy path مرده است.
        """
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        # nursing_covers=0 (integer) در DB → bool(0)=False → not None → legacy dead
        _insert_tariff(db, "بیمه لگسی", 90000.0,
                       nursing_covers=0, nursing_tariff=0.0, is_supplementary=0)
        svc_id = _insert_nursing_service(db, "آمپول IM")
        pid = _insert_patient(db, "لگسی")
        inv_id = _open_invoice(db, pid, insurance_type="بیمه لگسی")
        db.execute(
            """INSERT INTO injections
               (patient_id, invoice_id, injection_type, service_id,
                total_price, work_date)
               VALUES (?, ?, 'آمپول IM', ?, 60000.0, '2024-01-01')""",
            (pid, inv_id, svc_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        inj = [it for it in items if it['type'] == 'injection'][0]
        # ACTUAL behaviour (not intended): legacy path dead → patient pays full
        assert inj['patient_share'] == 60000.0, (
            "BUG PINNED: nursing_covers=0 + nursing_tariff=0 → legacy path مرده، "
            f"patient_share = total_price = 60000، ولی {inj['patient_share']} است"
        )
        assert inj['covered_by_insurance'] == 0, (
            "BUG PINNED: covered=0 (legacy path فعال نشد)"
        )


# ---------------------------------------------------------------------------
# 5. تزریق مستثنا (در insurance_nursing_exclusions) → patient_share = total_price
# ---------------------------------------------------------------------------

class TestExcludedInjectionPatientShare:
    """تزریق مستثنا: حتی با nursing_covers=1، اگر service_id مستثنا باشد → patient_share=total_price."""

    def test_excluded_injection_patient_pays_full(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0,
                       nursing_covers=1, nursing_tariff=0.0, is_supplementary=0)
        svc_id = _insert_nursing_service(db, "واکسن مستثنا")
        # استثنا برای این سرویس در این بیمه
        db.execute(
            """INSERT INTO insurance_nursing_exclusions
               (insurance_type, nursing_service_id, note)
               VALUES ('تامین اجتماعی', ?, 'excluded test')""",
            (svc_id,)
        )
        db.commit()
        pid = _insert_patient(db, "مستثنا")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        db.execute(
            """INSERT INTO injections
               (patient_id, invoice_id, injection_type, service_id,
                total_price, work_date)
               VALUES (?, ?, 'واکسن مستثنا', ?, 120000.0, '2024-01-01')""",
            (pid, inv_id, svc_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        inj = [it for it in items if it['type'] == 'injection'][0]
        # Rule: excluded service → patient_share = total_price (no coverage)
        assert inj['patient_share'] == 120000.0, (
            f"تزریق مستثنا: patient_share باید 120000 باشد، ولی {inj['patient_share']} است"
        )
        assert inj['covered_by_insurance'] == 0, "مستثنا: covered_by_insurance باید ۰ باشد"
        assert inj['insurance_share'] == 0.0, "مستثنا: insurance_share باید ۰ باشد"

    def test_non_excluded_injection_covered_in_same_invoice(self, webapp_db):
        """ایزولاسیون: سرویس دیگری در همان فاکتور که مستثنا نیست → همچنان ۰."""
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0,
                       nursing_covers=1, nursing_tariff=0.0, is_supplementary=0)
        excluded_svc = _insert_nursing_service(db, "داروی مستثنا")
        covered_svc = _insert_nursing_service(db, "تزریق معمولی")
        db.execute(
            "INSERT INTO insurance_nursing_exclusions (insurance_type, nursing_service_id) VALUES ('تامین اجتماعی', ?)",
            (excluded_svc,)
        )
        db.commit()
        pid = _insert_patient(db, "مخلوط")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        db.execute(
            """INSERT INTO injections
               (patient_id, invoice_id, injection_type, service_id, total_price, work_date)
               VALUES (?, ?, 'داروی مستثنا', ?, 50000.0, '2024-01-01')""",
            (pid, inv_id, excluded_svc)
        )
        db.execute(
            """INSERT INTO injections
               (patient_id, invoice_id, injection_type, service_id, total_price, work_date)
               VALUES (?, ?, 'تزریق معمولی', ?, 30000.0, '2024-01-01')""",
            (pid, inv_id, covered_svc)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        inj_items = [it for it in items if it['type'] == 'injection']
        excluded = next(it for it in inj_items if it['recorded_price'] == 50000.0)
        covered = next(it for it in inj_items if it['recorded_price'] == 30000.0)
        assert excluded['patient_share'] == 50000.0
        assert covered['patient_share'] == 0.0


# ---------------------------------------------------------------------------
# 6. مصرفی → patient_share = total_cost همیشه
# ---------------------------------------------------------------------------

class TestConsumablePatientShare:
    """مصرفی: patient_share = total_cost بدون توجه به بیمه."""

    def test_consumable_patient_share_equals_total_cost(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0,
                       nursing_covers=1, nursing_tariff=0.0)
        pid = _insert_patient(db, "مصرفی")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        db.execute(
            """INSERT INTO consumables_ledger
               (patient_id, invoice_id, item_name, category, total_cost, work_date)
               VALUES (?, ?, 'گاز استریل', 'supply', 25000.0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()

        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        cons = [it for it in items if it['type'] == 'consumable'][0]
        # Rule: consumables always patient_share = total_cost (no insurance coverage)
        assert cons['patient_share'] == 25000.0, (
            f"مصرفی: patient_share باید 25000 باشد، ولی {cons['patient_share']} است"
        )
        assert cons['insurance_share'] == 0.0, "مصرفی: بیمه سهم ندارد"
        assert cons['covered_by_insurance'] == 0

    def test_consumable_always_patient_pays_even_when_insured(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0, nursing_covers=1)
        pid = _insert_patient(db, "مصرفی2")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        db.execute(
            """INSERT INTO consumables_ledger
               (patient_id, invoice_id, item_name, total_cost, work_date)
               VALUES (?, ?, 'دستکش', 8000.0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()
        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        cons = [it for it in items if it['type'] == 'consumable'][0]
        # Even with nursing_covers=1, consumable patient_share = total_cost
        assert cons['patient_share'] == 8000.0


# ---------------------------------------------------------------------------
# 7. پروسیجر پرستار + پوشش → patient_share = 0
# ---------------------------------------------------------------------------

class TestNurseProcedurePatientShare:
    """پروسیجر performer_type='nurse' + nursing_covers=1 → patient_share=0."""

    def test_nurse_procedure_covered(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0,
                       nursing_covers=1, nursing_tariff=0.0)
        pid = _insert_patient(db, "پانسمان")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        db.execute(
            """INSERT INTO procedures
               (patient_id, invoice_id, procedure_type, performer_type,
                price, work_date)
               VALUES (?, ?, 'پانسمان', 'nurse', 45000.0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()
        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        proc = [it for it in items if it['type'] == 'procedure'][0]
        # Rule: performer_type='nurse' AND nursing_covers → description ends with '(پرستار)'
        # → patient_share = 0
        assert proc['description'].endswith('(پرستار)'), (
            f"description باید به '(پرستار)' ختم شود: {proc['description']}"
        )
        assert proc['patient_share'] == 0.0, (
            f"پروسیجر پرستار پوشش‌دار: patient_share باید ۰ باشد، ولی {proc['patient_share']}"
        )
        assert proc['covered_by_insurance'] == 1

    def test_doctor_procedure_always_patient_pays(self, webapp_db):
        """پروسیجر پزشک: حتی با nursing_covers=1 بیمار می‌پردازد."""
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0,
                       nursing_covers=1, nursing_tariff=0.0)
        pid = _insert_patient(db, "دکتر")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")
        db.execute(
            """INSERT INTO procedures
               (patient_id, invoice_id, procedure_type, performer_type,
                price, work_date)
               VALUES (?, ?, 'بخیه', 'doctor', 200000.0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()
        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        proc = [it for it in items if it['type'] == 'procedure'][0]
        # Rule: performer_type='doctor' → description ends with '(پزشک)' → patient pays full
        assert proc['description'].endswith('(پزشک)')
        assert proc['patient_share'] == 200000.0, (
            "پروسیجر پزشک: patient_share = price"
        )

    def test_nurse_procedure_without_coverage_patient_pays(self, webapp_db):
        """پروسیجر پرستار ولی nursing_covers=0 → بیمار می‌پردازد."""
        db = webapp_db
        _insert_tariff(db, "آزاد", 150000.0, is_base_tariff=1)
        _insert_tariff(db, "بیمه بدون پوشش پرستاری", 90000.0,
                       nursing_covers=0, nursing_tariff=999.0)  # tariff != 0 → no legacy coverage
        pid = _insert_patient(db, "پرستار بدون پوشش")
        inv_id = _open_invoice(db, pid, insurance_type="بیمه بدون پوشش پرستاری")
        db.execute(
            """INSERT INTO procedures
               (patient_id, invoice_id, procedure_type, performer_type,
                price, work_date)
               VALUES (?, ?, 'پانسمان', 'nurse', 70000.0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()
        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        proc = [it for it in items if it['type'] == 'procedure'][0]
        # nursing_covers=0 AND nursing_tariff != 0 → not covered → patient_share = price
        assert proc['patient_share'] == 70000.0, (
            "بدون پوشش پرستاری: patient_share = price"
        )


# ---------------------------------------------------------------------------
# 8. فاکتور مرکب + update_invoice_totals + get_financials
# ---------------------------------------------------------------------------

class TestCompositeInvoiceTotalsAndFinancials:
    """فاکتور مرکب: total_amount، revenue، paid (card/cash)، remaining."""

    def _build_composite_invoice(self, db):
        """
        فاکتوری با:
          - ویزیت تامین اجتماعی: patient_share = 85000
          - تزریق پوشش‌دار (غیرمستثنا): patient_share = 0
          - پروسیجر پزشک: patient_share = 150000
          - مصرفی: patient_share = 30000 (شامل total_amount، خارج از revenue)
        total_amount = 85000 + 0 + 150000 + 30000 = 265000
        revenue     = 85000 + 0 + 150000 = 235000 (بدون مصرفی)
        """
        _insert_tariff(db, "آزاد", 200000.0, is_base_tariff=1)
        _insert_tariff(db, "تامین اجتماعی", 85000.0,
                       nursing_covers=1, nursing_tariff=0.0, is_supplementary=0)
        svc_id = _insert_nursing_service(db, "تزریق IV ترکیبی")
        pid = _insert_patient(db, "مرکب")
        inv_id = _open_invoice(db, pid, insurance_type="تامین اجتماعی")

        # ویزیت
        visit_cur = db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, price, work_date)
               VALUES (?, ?, 'تامین اجتماعی', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        visit_id = visit_cur.lastrowid

        # تزریق پوشش‌دار
        inj_cur = db.execute(
            """INSERT INTO injections
               (patient_id, invoice_id, injection_type, service_id,
                total_price, work_date)
               VALUES (?, ?, 'تزریق IV', ?, 70000.0, '2024-01-01')""",
            (pid, inv_id, svc_id)
        )
        inj_id = inj_cur.lastrowid

        # پروسیجر پزشک
        proc_cur = db.execute(
            """INSERT INTO procedures
               (patient_id, invoice_id, procedure_type, performer_type,
                price, work_date)
               VALUES (?, ?, 'بخیه', 'doctor', 150000.0, '2024-01-01')""",
            (pid, inv_id)
        )
        proc_id = proc_cur.lastrowid

        # مصرفی
        cons_cur = db.execute(
            """INSERT INTO consumables_ledger
               (patient_id, invoice_id, item_name, total_cost, work_date)
               VALUES (?, ?, 'گاز', 30000.0, '2024-01-01')""",
            (pid, inv_id)
        )
        cons_id = cons_cur.lastrowid
        db.commit()
        return inv_id, visit_id, inj_id, proc_id, cons_id, pid

    def test_update_invoice_totals_sums_patient_shares(self, webapp_db):
        db = webapp_db
        inv_id, *_ = self._build_composite_invoice(db)
        repo = InvoiceRepository()
        repo.update_invoice_totals(inv_id)

        row = db.execute("SELECT total_amount FROM invoices WHERE id = ?", (inv_id,)).fetchone()
        # visit(85000) + injection(0) + procedure(150000) + consumable(30000) = 265000
        assert row['total_amount'] == 265000.0, (
            f"total_amount باید 265000 باشد، ولی {row['total_amount']} است"
        )

    def test_get_financials_revenue_excludes_consumables(self, webapp_db):
        db = webapp_db
        inv_id, *_ = self._build_composite_invoice(db)
        repo = InvoiceRepository()
        repo.update_invoice_totals(inv_id)
        fin = repo.get_financials(inv_id)
        # Rule: revenue = visits + injections + procedures (NOT consumables)
        assert fin['revenue'] == 235000.0, (
            f"revenue باید 235000 باشد (بدون مصرفی)، ولی {fin['revenue']} است"
        )
        assert fin['consumables'] == 30000.0, "consumables باید 30000 باشد"
        assert fin['total'] == 265000.0, "total باید 265000 باشد"

    def test_get_financials_paid_card_and_cash_breakdown(self, webapp_db):
        db = webapp_db
        inv_id, visit_id, inj_id, proc_id, cons_id, pid = self._build_composite_invoice(db)
        repo = InvoiceRepository()
        repo.update_invoice_totals(inv_id)

        # ثبت پرداخت: ویزیت=card، پروسیجر=cash
        db.execute(
            """INSERT INTO invoice_item_payments
               (invoice_id, item_type, item_id, payment_type, is_paid)
               VALUES (?, 'visit', ?, 'card', 1)""",
            (inv_id, visit_id)
        )
        db.execute(
            """INSERT INTO invoice_item_payments
               (invoice_id, item_type, item_id, payment_type, is_paid)
               VALUES (?, 'procedure', ?, 'cash', 1)""",
            (inv_id, proc_id)
        )
        db.commit()

        fin = repo.get_financials(inv_id)
        # visit patient_share=85000 (card) + procedure patient_share=150000 (cash)
        assert fin['paid'] == 235000.0, (
            f"paid باید 235000 باشد، ولی {fin['paid']} است"
        )
        assert fin['paid_card'] == 85000.0, (
            f"paid_card باید 85000 باشد، ولی {fin['paid_card']} است"
        )
        assert fin['paid_cash'] == 150000.0, (
            f"paid_cash باید 150000 باشد، ولی {fin['paid_cash']} است"
        )
        # remaining = total_amount - paid = 265000 - 235000 = 30000
        assert fin['remaining'] == 30000.0, (
            f"remaining باید 30000 باشد، ولی {fin['remaining']} است"
        )

    def test_get_financials_injection_covered_zero_in_paid(self, webapp_db):
        """تزریق پوشش‌دار با is_paid=1: patient_share=0 پس paid تغییری نمی‌کند."""
        db = webapp_db
        inv_id, visit_id, inj_id, proc_id, cons_id, pid = self._build_composite_invoice(db)
        repo = InvoiceRepository()
        repo.update_invoice_totals(inv_id)

        # علامت‌گذاری تزریق به عنوان پرداخت‌شده (ولی patient_share=0)
        db.execute(
            """INSERT INTO invoice_item_payments
               (invoice_id, item_type, item_id, payment_type, is_paid)
               VALUES (?, 'injection', ?, 'card', 1)""",
            (inv_id, inj_id)
        )
        db.commit()
        fin = repo.get_financials(inv_id)
        # paid should be 0 because patient_share of injection is 0
        assert fin['paid'] == 0.0, (
            f"تزریق پوشش‌دار با is_paid=1: paid باید ۰ باشد، ولی {fin['paid']} است"
        )


# ---------------------------------------------------------------------------
# 9. close_invoice: open → closed + idempotency
# ---------------------------------------------------------------------------

class TestCloseInvoice:
    """close_invoice: باز → بسته؛ اجرای دوم روی بسته → rowcount=0."""

    def test_close_open_invoice_sets_status_closed(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 100000.0, is_base_tariff=1)
        pid = _insert_patient(db, "بستن")
        inv_id = _open_invoice(db, pid, insurance_type="آزاد")

        repo = InvoiceRepository()
        result = repo.close_invoice(inv_id, "testuser")
        # Rule: close open invoice → returns True
        assert result is True, "close_invoice روی فاکتور باز باید True برگرداند"
        row = db.execute("SELECT status, closed_by FROM invoices WHERE id = ?", (inv_id,)).fetchone()
        assert row['status'] == 'closed', "وضعیت باید 'closed' شود"
        assert row['closed_by'] == 'testuser', "closed_by ثبت شود"

    def test_close_open_invoice_updates_total_amount(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 100000.0, is_base_tariff=1)
        pid = _insert_patient(db, "بستن2")
        inv_id = _open_invoice(db, pid, insurance_type="آزاد")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, price, work_date)
               VALUES (?, ?, 'آزاد', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()
        repo = InvoiceRepository()
        repo.close_invoice(inv_id, "admin")
        row = db.execute("SELECT total_amount FROM invoices WHERE id = ?", (inv_id,)).fetchone()
        # visit patient_share = آزاد tariff = 100000
        assert row['total_amount'] == 100000.0, (
            f"close_invoice باید total_amount را نیز تنظیم کند، ولی {row['total_amount']} است"
        )

    def test_close_already_closed_invoice_returns_false(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 100000.0, is_base_tariff=1)
        pid = _insert_patient(db, "بسته‌شده")
        inv_id = _open_invoice(db, pid)
        repo = InvoiceRepository()
        repo.close_invoice(inv_id, "user1")  # first close
        result = repo.close_invoice(inv_id, "user2")  # second close → must be False
        # Rule: WHERE status='open' — already closed → rowcount=0 → False
        assert result is False, (
            "close_invoice روی فاکتور بسته باید False برگرداند (idempotency)"
        )

    def test_close_nonexistent_invoice_returns_false(self, webapp_db):
        repo = InvoiceRepository()
        result = repo.close_invoice(9999, "admin")
        assert result is False, "فاکتور ناموجود: باید False برگرداند"


# ---------------------------------------------------------------------------
# 10. get_invoice_items بدون tariff برای بیمه → fallback به base_tariff
# ---------------------------------------------------------------------------

class TestVisitTariffFallback:
    """اگر tariff ردیفِ بیمه وجود نداشته باشد → patient_share = base_tariff."""

    def test_visit_unknown_insurance_falls_back_to_base_tariff(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 180000.0, is_base_tariff=1)
        # هیچ ردیفی برای 'بیمه ناشناس' نیست
        pid = _insert_patient(db, "ناشناس")
        inv_id = _open_invoice(db, pid, insurance_type="بیمه ناشناس")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, price, work_date)
               VALUES (?, ?, 'بیمه ناشناس', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()
        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        v = [it for it in items if it['type'] == 'visit'][0]
        # No tariff row for 'بیمه ناشناس' → falls back to base_tariff (180000)
        # BUT code path: insurance type query returns nothing → patient_share stays = recorded_price = base_tariff
        assert v['patient_share'] == 180000.0, (
            f"بیمه ناشناس: patient_share باید به base_tariff (180000) fallback کند، ولی {v['patient_share']} است"
        )

    def test_visit_no_base_tariff_at_all_gives_zero_recorded(self, webapp_db):
        """وقتی هیچ base_tariff وجود ندارد: recorded_price=0، patient_share=0."""
        db = webapp_db
        # بدون هیچ تعرفه‌ای
        pid = _insert_patient(db, "صفر")
        inv_id = _open_invoice(db, pid, insurance_type="آزاد")
        db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, price, work_date)
               VALUES (?, ?, 'آزاد', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        db.commit()
        repo = InvoiceRepository()
        items = repo.get_invoice_items(inv_id)
        v = [it for it in items if it['type'] == 'visit'][0]
        # No tariff row at all → base_visit_price=0 → patient_share=0
        assert v['patient_share'] == 0.0, (
            "بدون هیچ تعرفه‌ای: patient_share باید ۰ باشد"
        )


# ---------------------------------------------------------------------------
# 11. get_financials: remaining هرگز منفی نمی‌شود
# ---------------------------------------------------------------------------

class TestRemainingNonNegative:
    """اگر paid > total_amount: remaining = 0 (نه منفی)."""

    def test_remaining_never_negative(self, webapp_db):
        db = webapp_db
        _insert_tariff(db, "آزاد", 50000.0, is_base_tariff=1)
        pid = _insert_patient(db, "مثبت")
        inv_id = _open_invoice(db, pid, insurance_type="آزاد")
        visit_cur = db.execute(
            """INSERT INTO visits
               (patient_id, invoice_id, insurance_type, price, work_date)
               VALUES (?, ?, 'آزاد', 0, '2024-01-01')""",
            (pid, inv_id)
        )
        visit_id = visit_cur.lastrowid
        db.commit()
        repo = InvoiceRepository()
        repo.update_invoice_totals(inv_id)
        # total_amount = 50000; حالا total_amount را دستی به ۱۰۰۰ کاهش می‌دهیم (شبیه‌سازی)
        db.execute("UPDATE invoices SET total_amount = 1000 WHERE id = ?", (inv_id,))
        db.execute(
            """INSERT INTO invoice_item_payments
               (invoice_id, item_type, item_id, payment_type, is_paid)
               VALUES (?, 'visit', ?, 'cash', 1)""",
            (inv_id, visit_id)
        )
        db.commit()
        fin = repo.get_financials(inv_id)
        # paid=50000 > invoice_total=1000 → remaining should be 0, not -49000
        assert fin['remaining'] >= 0, (
            f"remaining باید >= 0 باشد، ولی {fin['remaining']} است"
        )
