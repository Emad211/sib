"""The contract between accounting and the shift app (ACC-2.11, docs/shift/S04): the `v_shift_*_v1`
views, read through a read-only connection. Column names and meanings are part of the contract."""

import sqlite3

import pytest

from src.adapters.sqlite.core import ensure_database_ready
from src.adapters.sqlite.migrations import m0004_shift_contract as m4
from src.adapters.sqlite.migrations import m0007_shift_contract_voids as m7
from src.services.backup import ro_uri

EXPECTED_COLUMNS = {
    "v_shift_open_invoices_v1": ["invoice_id", "opened_at", "opened_by", "shift", "work_date"],
    "v_shift_invoice_lookup_v1": ["invoice_id", "patient_id", "status", "total", "closed_at"],
    "v_shift_consumption_v1": ["ledger_id", "invoice_id", "closed_at", "acc_code", "quantity", "item_name"],
    "v_shift_payment_lines_v1": ["invoice_id", "item_type", "item_id", "payment_type", "amount", "closed_at"],
    "v_shift_cash_receipts_v1": ["invoice_id", "closed_at", "amount"],
    "v_shift_vitals_services_v1": ["injection_id", "invoice_id", "nurse_id", "type", "closed_at"],
    "v_shift_patient_registrations_v1": [
        "patient_id",
        "created_by",
        "created_at",
        "has_valid_nid",
        "has_valid_phone",
        "is_foreign",
    ],
    "v_shift_voids_v1": [
        "void_id",
        "invoice_id",
        "kind",
        "amount",
        "refund_method",
        "reason",
        "voided_by",
        "voided_at",
        "work_date",
        "shift",
    ],
}


@pytest.fixture()
def path(tmp_path):
    p = str(tmp_path / "contract.db")
    ensure_database_ready(p)
    conn = sqlite3.connect(p)
    conn.execute(
        "INSERT INTO patients (name, family_name, national_id, phone_number, created_by, created_at, is_foreign) VALUES ('الف', 'ب', '2170415981', '09123456789', 'u1', '2026-09-24 08:00:00', 0)"
    )
    conn.commit()
    conn.close()
    return p


def write(path, *statements):
    conn = sqlite3.connect(path)
    for sql, args in statements:
        conn.execute(sql, args)
    conn.commit()
    conn.close()


def read(path, sql, *args):
    """How the shift app connects: read-only URI plus query_only."""
    conn = sqlite3.connect(ro_uri(path), uri=True)
    conn.execute("PRAGMA query_only = 1")
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def invoice(status="closed", closed_at="2026-09-24 09:00:00", shift="morning"):
    return (
        "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, opened_by, closed_at)"
        " VALUES (1, ?, 'آزاد', 100, '2026-09-24', ?, '2026-09-24 08:00:00', 'u1', ?)",
        (status, shift, closed_at if status == "closed" else None),
    )


# --------------------------------------------------------------------------- shape


def test_every_view_has_exactly_the_contract_columns(path):
    for name, cols in EXPECTED_COLUMNS.items():
        got = [r[1] for r in sqlite3.connect(path).execute(f"PRAGMA table_info({name})")]
        assert got == cols, name
    assert set(m4.VIEWS_V1) | set(m7.VOIDS_VIEWS) == set(EXPECTED_COLUMNS)


def test_the_latest_migration_owns_the_closed_only_money_views(path):
    """The fix lives in v7: v4 stays immutable, so the view bodies on disk must be v7's."""
    assert set(m7.CLOSED_ONLY_VIEWS) == {"v_shift_cash_receipts_v1", "v_shift_payment_lines_v1"}
    for name, sql in m7.CLOSED_ONLY_VIEWS.items():
        on_disk = sqlite3.connect(path).execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]
        assert on_disk.strip() == sql.strip(), name


def test_no_view_exposes_personal_data(path):
    forbidden = {"national_id", "phone", "phone_number", "full_name", "name", "family_name", "address", "birthdate"}
    for name in EXPECTED_COLUMNS:
        cols = {r[1] for r in sqlite3.connect(path).execute(f"PRAGMA table_info({name})")}
        assert not cols & forbidden, name


def test_views_are_readable_read_only_and_cannot_be_written_through(path):
    assert read(path, "SELECT COUNT(*) FROM v_shift_invoice_lookup_v1") == [(0,)]
    conn = sqlite3.connect(ro_uri(path), uri=True)
    conn.execute("PRAGMA query_only = 1")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM v_shift_invoice_lookup_v1")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO consumables_ledger (patient_id, item_name) VALUES (1, 'x')")
    conn.close()


def test_the_migrations_can_run_twice(path):
    conn = sqlite3.connect(path)
    for up in (m4.up, m4.up, m7.up, m7.up):
        up(conn)
    conn.commit()
    assert [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE 'v_shift_%' ORDER BY name"
        )
    ] == sorted(EXPECTED_COLUMNS)
    conn.close()


def test_closed_at_is_indexed_for_watermark_reads(path):
    plan = " ".join(
        str(r)
        for r in sqlite3.connect(path).execute(
            "EXPLAIN QUERY PLAN SELECT * FROM invoices WHERE closed_at > '2026-09-24'"
        )
    )
    assert "idx_invoices_closed_at" in plan


def test_voids_and_registrations_are_indexed_for_watermark_reads(path):
    for table, column, index in (
        ("invoice_voids", "voided_at", "idx_invoice_voids_voided_at"),
        ("patients", "created_at", "idx_patients_created_at"),
    ):
        plan = " ".join(
            str(r)
            for r in sqlite3.connect(path).execute(
                f"EXPLAIN QUERY PLAN SELECT * FROM {table} WHERE {column} > '2026-09-24'"
            )
        )
        assert index in plan, table


# --------------------------------------------------------------------------- invoices


def test_open_invoices_and_lookup(path):
    write(path, invoice("open"), invoice("closed"))
    assert read(path, "SELECT invoice_id, opened_by, shift, work_date FROM v_shift_open_invoices_v1") == [
        (1, "u1", "morning", "2026-09-24")
    ]
    assert read(
        path,
        "SELECT invoice_id, patient_id, status, total, closed_at FROM v_shift_invoice_lookup_v1 ORDER BY invoice_id",
    ) == [
        (1, 1, "open", 100.0, None),
        (2, 1, "closed", 100.0, "2026-09-24 09:00:00"),
    ]


# --------------------------------------------------------------------------- consumption


def ledger(invoice_id, name, qty, provided=0):
    return (
        "INSERT INTO consumables_ledger (patient_id, item_name, quantity, total_cost, patient_provided, invoice_id) VALUES (1, ?, ?, 0, ?, ?)",
        (name, qty, provided, invoice_id),
    )


def test_consumption_maps_codes_applies_the_factor_and_skips_what_must_be_skipped(path):
    write(
        path,
        invoice("closed"),  # 1
        invoice("open"),  # 2
        (
            "INSERT INTO consumable_tariffs (name, default_price, category, acc_code, stock_factor) VALUES ('سرنگ', 1, 'supply', 'SYR-5', 1)",
            (),
        ),
        (
            "INSERT INTO consumable_tariffs (name, default_price, category, acc_code, stock_factor) VALUES ('سرم نیم‌لیتر', 1, 'drug', 'SER-500', 0.5)",
            (),
        ),
        ("INSERT INTO consumable_tariffs (name, default_price, category) VALUES ('گاز', 1, 'supply')", ()),
        ledger(1, "سرنگ", 3),
        ledger(1, "سرم نیم‌لیتر", 4),
        ledger(1, "گاز", 2),  # known tariff, no code yet
        ledger(1, "ناشناخته", 1),  # no tariff at all
        ledger(1, "سرنگ", 9, provided=1),  # brought by the patient: not clinic stock
        ledger(2, "سرنگ", 5),  # invoice not closed yet
    )
    rows = read(path, "SELECT invoice_id, acc_code, quantity, item_name FROM v_shift_consumption_v1 ORDER BY ledger_id")
    assert rows == [
        (1, "SYR-5", 3.0, "سرنگ"),
        (1, "SER-500", 2.0, "سرم نیم‌لیتر"),
        (1, None, 2.0, "گاز"),
        (1, None, 1.0, "ناشناخته"),
    ]


def test_consumption_has_one_row_per_ledger_line_and_tariff_names_cannot_duplicate(path):
    write(
        path,
        invoice("closed"),
        ledger(1, "سرنگ", 1),
        (
            "INSERT INTO consumable_tariffs (name, default_price, category, acc_code) VALUES ('سرنگ', 1, 'supply', 'A')",
            (),
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):  # the UNIQUE name is what keeps the join one-to-one
        write(
            path,
            (
                "INSERT INTO consumable_tariffs (name, default_price, category, acc_code) VALUES ('سرنگ', 1, 'supply', 'B')",
                (),
            ),
        )
    assert read(path, "SELECT COUNT(*), MIN(acc_code) FROM v_shift_consumption_v1") == [(1, "A")]


# --------------------------------------------------------------------------- money


def line(invoice_id, item_id, ptype, amount, closed_at="2026-09-24 09:00:00"):
    return (
        "INSERT INTO invoice_payment_lines (invoice_id, item_type, item_id, payment_type, patient_amount, closed_at) VALUES (?, 'injection', ?, ?, ?, ?)",
        (invoice_id, item_id, ptype, amount, closed_at),
    )


def test_cash_receipts_sum_cash_only_per_invoice(path):
    write(
        path,
        invoice("closed"),
        invoice("closed"),
        line(1, 1, "cash", 100000),
        line(1, 2, "cash", 50000),
        line(1, 3, "card", 70000),
        line(2, 4, "card", 10000),
        line(2, 5, None, 0),
        line(2, 6, "cash", 0),
    )
    assert read(path, "SELECT invoice_id, amount FROM v_shift_cash_receipts_v1 ORDER BY invoice_id") == [(1, 150000.0)]
    assert read(path, "SELECT COUNT(*) FROM v_shift_payment_lines_v1") == [(6,)]
    assert read(path, "SELECT SUM(amount) FROM v_shift_payment_lines_v1 WHERE payment_type = 'card'") == [(80000.0,)]


def test_lines_of_a_non_closed_invoice_are_never_counted(path):
    """The money bug: lines that outlive their invoice's 'closed' status must count for nothing."""
    write(path, invoice("closed"), line(1, 1, "cash", 100000), line(1, 2, "card", 50000))
    write(path, ("UPDATE invoices SET status = 'open', closed_at = NULL WHERE id = ?", (1,)))
    assert read(path, "SELECT COUNT(*) FROM invoice_payment_lines") == [(2,)]  # the lines are still there
    assert read(path, "SELECT COUNT(*) FROM v_shift_cash_receipts_v1") == [(0,)]
    assert read(path, "SELECT COUNT(*) FROM v_shift_payment_lines_v1") == [(0,)]


# --------------------------------------------------------------------------- voids


def void(invoice_id, kind="refund", amount=100, method="cash", reason="اشتباه در ثبت"):
    """What the manager's void route writes: the reason, the money, and status='voided'."""
    return (
        (
            "INSERT INTO invoice_voids (invoice_id, kind, amount, refund_method, reason, voided_by, voided_at)"
            " VALUES (?, ?, ?, ?, ?, 'mgr', '2026-09-24 10:00:00')",
            (invoice_id, kind, amount, method, reason),
        ),
        ("UPDATE invoices SET status = 'voided' WHERE id = ?", (invoice_id,)),
    )


def test_a_void_takes_the_cash_out_of_the_shift_views(path):
    write(path, invoice("closed"), invoice("closed"), line(1, 1, "cash", 100000), line(2, 2, "cash", 40000))
    assert read(path, "SELECT invoice_id, amount FROM v_shift_cash_receipts_v1 ORDER BY invoice_id") == [
        (1, 100000.0),
        (2, 40000.0),
    ]
    write(path, *void(1))
    # The refunded invoice keeps its frozen lines (v6); only its status takes it out of the views.
    assert read(path, "SELECT COUNT(*) FROM invoice_payment_lines WHERE invoice_id = 1") == [(1,)]
    assert read(path, "SELECT invoice_id, amount FROM v_shift_cash_receipts_v1") == [(2, 40000.0)]
    assert read(path, "SELECT invoice_id FROM v_shift_payment_lines_v1") == [(2,)]


def test_a_reopened_invoice_loses_its_lines_and_drops_out_of_the_views(path):
    write(path, invoice("closed"), line(1, 1, "cash", 100000), line(1, 2, "card", 50000))
    write(
        path,
        ("DELETE FROM invoice_payment_lines WHERE invoice_id = ?", (1,)),
        ("UPDATE invoices SET status = 'open', closed_at = NULL WHERE id = ?", (1,)),
    )
    assert read(path, "SELECT COUNT(*) FROM invoice_payment_lines") == [(0,)]
    assert read(path, "SELECT COUNT(*) FROM v_shift_cash_receipts_v1") == [(0,)]
    assert read(path, "SELECT COUNT(*) FROM v_shift_payment_lines_v1") == [(0,)]


def test_every_void_is_listed_with_its_money_reason_and_the_invoice_shift(path):
    write(
        path,
        invoice("closed", shift="night"),
        invoice("closed"),
        *void(1),
        *void(2, kind="void", method=None, reason="ثبت تکراری"),
    )
    assert read(
        path,
        "SELECT void_id, invoice_id, kind, amount, refund_method, reason, voided_by, voided_at, work_date, shift "
        "FROM v_shift_voids_v1 ORDER BY void_id",
    ) == [
        (1, 1, "refund", 100.0, "cash", "اشتباه در ثبت", "mgr", "2026-09-24 10:00:00", "2026-09-24", "night"),
        (2, 2, "void", 100.0, None, "ثبت تکراری", "mgr", "2026-09-24 10:00:00", "2026-09-24", "morning"),
    ]


# --------------------------------------------------------------------------- vitals and registrations


def test_vitals_services_are_the_two_named_ones_on_closed_invoices(path):
    inj = (
        "INSERT INTO injections (patient_id, injection_type, nurse_id, total_price, invoice_id) VALUES (1, ?, ?, 1, ?)"
    )
    write(
        path,
        invoice("closed"),
        invoice("open"),
        (inj, ("تست قند", 7, 1)),
        (inj, ("کنترل فشار", 8, 1)),
        (inj, ("سرم", 7, 1)),
        (inj, ("تست قند", 7, 2)),
    )
    assert read(path, "SELECT nurse_id, type FROM v_shift_vitals_services_v1 ORDER BY injection_id") == [
        (7, "تست قند"),
        (8, "کنترل فشار"),
    ]


def test_patient_registrations_report_validity_not_values(path):
    write(
        path,
        (
            "INSERT INTO patients (name, family_name, national_id, phone_number, created_by, is_foreign) VALUES ('ج', 'د', '2170415982', '9123456789', 'u2', 0)",
            (),
        ),
        (
            "INSERT INTO patients (name, family_name, national_id, phone_number, created_by, is_foreign) VALUES ('ه', 'و', NULL, NULL, 'u2', 1)",
            (),
        ),
    )
    assert read(
        path,
        "SELECT patient_id, created_by, has_valid_nid, has_valid_phone, is_foreign FROM v_shift_patient_registrations_v1 ORDER BY patient_id",
    ) == [
        (1, "u1", 1, 1, 0),
        (2, "u2", 0, 0, 0),
        (3, "u2", 0, 0, 1),
    ]


def test_old_visit_tariffs_columns_are_added_by_the_migration_not_by_a_request(tmp_path):
    """A very old backup without nursing_covers / is_base_tariff is fixed when it is restored."""
    p = str(tmp_path / "old.db")
    ensure_database_ready(p)
    conn = sqlite3.connect(p)
    conn.execute("ALTER TABLE visit_tariffs DROP COLUMN nursing_covers")
    conn.execute("ALTER TABLE visit_tariffs DROP COLUMN is_base_tariff")
    conn.commit()
    m4.up(conn)
    conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(visit_tariffs)")}
    conn.close()
    assert {"nursing_covers", "is_base_tariff"} <= cols
