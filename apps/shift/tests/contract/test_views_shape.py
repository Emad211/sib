"""The `v_shift_*_v1` contract: view names, column order and value types (S04 §2, R-05).

A change here is a change in accounting that the bridge would read wrong, so the expected names and
types are literals in this file rather than a re-derivation of `views.COLUMNS`.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from make_synthetic_acc_db import SyntheticAccounting, build

from shift.adapters.accounting import views
from shift.adapters.accounting.readonly import AccountingDatabase, open_accounting

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "v_shift_open_invoices_v1": ("invoice_id", "opened_at", "opened_by", "shift", "work_date"),
    "v_shift_invoice_lookup_v1": ("invoice_id", "patient_id", "status", "total", "closed_at"),
    "v_shift_consumption_v1": ("ledger_id", "invoice_id", "closed_at", "acc_code", "quantity", "item_name"),
    "v_shift_payment_lines_v1": ("invoice_id", "item_type", "item_id", "payment_type", "amount", "closed_at"),
    "v_shift_cash_receipts_v1": ("invoice_id", "closed_at", "amount"),
    "v_shift_vitals_services_v1": ("injection_id", "invoice_id", "nurse_id", "type", "closed_at"),
    "v_shift_patient_registrations_v1": (
        "patient_id",
        "created_by",
        "created_at",
        "has_valid_nid",
        "has_valid_phone",
        "is_foreign",
    ),
    "v_shift_voids_v1": (
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
    ),
    # Accounting migration 8 (owner decision 5): the duplicate-mobile flag and the call sample.
    "v_shift_patient_registrations_v2": (
        "patient_id",
        "created_by",
        "created_at",
        "has_valid_nid",
        "has_valid_phone",
        "is_foreign",
        "first_for_phone",
    ),
    "v_shift_call_sample_v1": ("patient_id", "full_name", "phone_number", "created_at", "created_by"),
}

# Declared types as SQLite reports them for a view; '' is a computed column with no affinity.
EXPECTED_DECLARED_TYPES: dict[str, tuple[str, ...]] = {
    "v_shift_open_invoices_v1": ("INTEGER", "TIMESTAMP", "TEXT", "TEXT", "TEXT"),
    "v_shift_invoice_lookup_v1": ("INTEGER", "INTEGER", "TEXT", "REAL", "TIMESTAMP"),
    "v_shift_consumption_v1": ("INTEGER", "INTEGER", "TIMESTAMP", "TEXT", "", "TEXT"),
    "v_shift_payment_lines_v1": ("INTEGER", "TEXT", "INTEGER", "TEXT", "REAL", "TIMESTAMP"),
    "v_shift_cash_receipts_v1": ("INTEGER", "TIMESTAMP", ""),
    "v_shift_vitals_services_v1": ("INTEGER", "INTEGER", "INTEGER", "TEXT", "TIMESTAMP"),
    "v_shift_patient_registrations_v1": ("INTEGER", "TEXT", "TIMESTAMP", "", "", ""),
    "v_shift_voids_v1": (
        "INTEGER",
        "INTEGER",
        "TEXT",
        "REAL",
        "TEXT",
        "TEXT",
        "TEXT",
        "TIMESTAMP",
        "TEXT",
        "TEXT",
    ),
    "v_shift_patient_registrations_v2": ("INTEGER", "TEXT", "TIMESTAMP", "", "", "", ""),
    "v_shift_call_sample_v1": ("INTEGER", "TEXT", "TEXT", "TIMESTAMP", "TEXT"),
}

# What the bridge parses out of each row: money is REAL Toman, times are local text, ids are integers.
EXPECTED_VALUE_TYPES: dict[str, tuple[str, ...]] = {
    "v_shift_cash_receipts_v1": ("integer", "text", "real"),
    "v_shift_consumption_v1": ("integer", "integer", "text", "text", "real", "text"),
    "v_shift_vitals_services_v1": ("integer", "integer", "integer", "text", "text"),
    "v_shift_patient_registrations_v1": ("integer", "text", "text", "integer", "integer", "integer"),
    "v_shift_voids_v1": ("integer", "integer", "text", "real", "text", "text", "text", "text", "text", "text"),
    "v_shift_patient_registrations_v2": (
        "integer",
        "text",
        "text",
        "integer",
        "integer",
        "integer",
        "integer",
    ),
    "v_shift_call_sample_v1": ("integer", "text", "text", "text", "text"),
}

FORBIDDEN_COLUMNS = {"national_id", "phone", "phone_number", "name", "family_name", "full_name", "address"}
# The call sample is the one view that carries a name and a mobile, and it is read by the manager's
# card page at render time only: the bridge never mirrors it, so it reaches no shift table (S04).
NAME_BEARING_VIEWS = {"v_shift_call_sample_v1"}


@pytest.fixture()
def scenario(tmp_path: Path) -> SyntheticAccounting:
    """A fresh synthetic accounting database for the shape checks."""
    return build(tmp_path / "accounting.db")


@pytest.fixture()
def accounting(scenario: SyntheticAccounting) -> Iterator[AccountingDatabase]:
    """The synthetic database opened the way the bridge opens it: read-only."""
    database = open_accounting(scenario.path)
    try:
        yield database
    finally:
        database.close()


def _run_sql(path: Path, script: str) -> None:
    """Change the synthetic accounting database the way an accounting upgrade or a DBA would."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()


def test_accounting_is_at_the_migration_that_owns_the_voids_view(scenario: SyntheticAccounting) -> None:
    assert scenario.schema_version >= 7


def test_every_view_has_exactly_the_contract_columns(accounting: AccountingDatabase) -> None:
    with accounting.connect() as connection:
        for name, expected in EXPECTED_COLUMNS.items():
            columns = tuple(row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({name})"))
            assert columns == expected, name


def test_the_code_contract_matches_the_pinned_names() -> None:
    assert {view.value: columns for view, columns in views.COLUMNS.items()} == EXPECTED_COLUMNS


def test_every_column_keeps_its_declared_type(accounting: AccountingDatabase) -> None:
    with accounting.connect() as connection:
        for name, expected in EXPECTED_DECLARED_TYPES.items():
            declared = tuple(row[2] for row in connection.exec_driver_sql(f"PRAGMA table_info({name})"))
            assert declared == expected, name


def test_values_really_have_the_types_the_bridge_parses(accounting: AccountingDatabase) -> None:
    with accounting.connect() as connection:
        for name, expected in EXPECTED_VALUE_TYPES.items():
            columns = views.COLUMNS[views.ViewName(name)]
            probe = ", ".join(f"typeof({column})" for column in columns)
            row = connection.exec_driver_sql(f"SELECT {probe} FROM {name} LIMIT 1").one()
            assert tuple(row) == expected, name


def test_no_mirrored_view_exposes_identity_values(accounting: AccountingDatabase) -> None:
    with accounting.connect() as connection:
        for name in EXPECTED_COLUMNS:
            if name in NAME_BEARING_VIEWS:
                continue
            columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({name})")}
            assert not columns & FORBIDDEN_COLUMNS, name


def test_introspection_sees_every_contract_view(accounting: AccountingDatabase) -> None:
    with accounting.connect() as connection:
        report = views.introspect(connection)

    assert report.present == frozenset(views.ViewName)
    assert report.missing == frozenset()
    assert report.voids_available is True
    assert report.usable is True


def test_a_missing_voids_view_degrades_only_the_voids_feed(scenario: SyntheticAccounting) -> None:
    _run_sql(scenario.path, "DROP VIEW v_shift_voids_v1")
    with open_accounting(scenario.path) as database, database.connect() as connection:
        report = views.introspect(connection)

    assert report.missing == frozenset({views.ViewName.VOIDS})
    assert report.voids_available is False
    assert report.usable is True
    views.require_views(report)  # refunds are optional; the bridge reports them as unverified


def test_a_missing_required_view_is_named_in_the_error(scenario: SyntheticAccounting) -> None:
    _run_sql(scenario.path, "DROP VIEW v_shift_cash_receipts_v1")
    with open_accounting(scenario.path) as database, database.connect() as connection:
        report = views.introspect(connection)

    assert report.usable is False
    assert report.missing == frozenset({views.ViewName.CASH_RECEIPTS})
    with pytest.raises(views.ViewContractError, match="v_shift_cash_receipts_v1"):
        views.require_views(report)


def test_the_contract_selects_columns_by_name_so_a_rename_cannot_pass_silently() -> None:
    """`_v2` is how accounting may break the shape; a rename inside `_v1` must break the read, not the money."""
    for page_read in views.PAGED_READS.values():
        for column in views.COLUMNS[page_read.view]:
            assert column in page_read.sql, page_read.view


def test_every_paged_read_is_a_keyset_query_on_pinned_columns() -> None:
    """Every mirrored view is read in pages; the lookup, the payment lines and the call sample are not."""
    assert set(views.PAGED_READS) | {views.ViewName.OPEN_INVOICES} == set(views.ViewName) - {
        views.ViewName.INVOICE_LOOKUP,
        views.ViewName.PAYMENT_LINES,
        # The call sample is asked for by patient id at render time, never mirrored (owner decision 5).
        views.ViewName.CALL_SAMPLE,
    }
    for view, page_read in views.PAGED_READS.items():
        assert page_read.view is view
        assert page_read.key_at in views.COLUMNS[view]
        assert page_read.key_id in views.COLUMNS[view]
        assert page_read.key_at != page_read.key_id
        assert f"FROM {view}" in page_read.sql
        assert "OFFSET" not in page_read.sql.upper()
        for parameter in (":after_at", ":after_id", ":until", ":limit"):
            assert parameter in page_read.sql


def test_the_open_invoice_snapshot_selects_only_contract_columns() -> None:
    for column in views.COLUMNS[views.ViewName.OPEN_INVOICES]:
        assert column in views.OPEN_INVOICES_SQL
    assert "OFFSET" not in views.OPEN_INVOICES_SQL.upper()
