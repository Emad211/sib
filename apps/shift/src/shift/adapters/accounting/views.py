"""The `v_shift_*_v1` views the bridge may read: names, columns, keyset queries (S04).

Accounting owns these views (migrations 4 and 7); the shift app owns nothing here and reads no
table. A breaking change in accounting arrives as a `_v2` view, so the column lists below are the
frozen half of the contract — `tests/contract/test_views_shape.py` fails when they move.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection

from shift.common.clock import TEHRAN, require_aware


class ViewName(StrEnum):
    """Every view the bridge knows about."""

    OPEN_INVOICES = "v_shift_open_invoices_v1"
    INVOICE_LOOKUP = "v_shift_invoice_lookup_v1"
    CONSUMPTION = "v_shift_consumption_v1"
    PAYMENT_LINES = "v_shift_payment_lines_v1"
    CASH_RECEIPTS = "v_shift_cash_receipts_v1"
    VITALS_SERVICES = "v_shift_vitals_services_v1"
    PATIENT_REGISTRATIONS = "v_shift_patient_registrations_v1"
    VOIDS = "v_shift_voids_v1"
    PATIENT_REGISTRATIONS_V2 = "v_shift_patient_registrations_v2"
    CALL_SAMPLE = "v_shift_call_sample_v1"


COLUMNS: Mapping[ViewName, tuple[str, ...]] = {
    ViewName.OPEN_INVOICES: ("invoice_id", "opened_at", "opened_by", "shift", "work_date"),
    ViewName.INVOICE_LOOKUP: ("invoice_id", "patient_id", "status", "total", "closed_at"),
    ViewName.CONSUMPTION: ("ledger_id", "invoice_id", "closed_at", "acc_code", "quantity", "item_name"),
    ViewName.PAYMENT_LINES: ("invoice_id", "item_type", "item_id", "payment_type", "amount", "closed_at"),
    ViewName.CASH_RECEIPTS: ("invoice_id", "closed_at", "amount"),
    ViewName.VITALS_SERVICES: ("injection_id", "invoice_id", "nurse_id", "type", "closed_at"),
    ViewName.PATIENT_REGISTRATIONS: (
        "patient_id",
        "created_by",
        "created_at",
        "has_valid_nid",
        "has_valid_phone",
        "is_foreign",
    ),
    ViewName.VOIDS: (
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
    ViewName.PATIENT_REGISTRATIONS_V2: (
        "patient_id",
        "created_by",
        "created_at",
        "has_valid_nid",
        "has_valid_phone",
        "is_foreign",
        "first_for_phone",
    ),
    ViewName.CALL_SAMPLE: ("patient_id", "full_name", "phone_number", "created_at", "created_by"),
}

# Voids arrive in accounting migration 7; before it the bridge still runs, reporting that the
# handover's cash cannot be verified against refunds (design §4, R-06).
# The v2 registration view and the call sample arrive in accounting migration 8. An install that has
# not been upgraded keeps working: the bridge reads v1 and counts every registration as the first for
# its mobile (accounting has never told it about a duplicate).
OPTIONAL_VIEWS: frozenset[ViewName] = frozenset(
    {ViewName.VOIDS, ViewName.PATIENT_REGISTRATIONS_V2, ViewName.CALL_SAMPLE}
)
REQUIRED_VIEWS: frozenset[ViewName] = frozenset(ViewName) - OPTIONAL_VIEWS

ACCOUNTING_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class ViewContractError(RuntimeError):
    """A required view is missing; the bridge must not guess what accounting means."""


@dataclass(frozen=True, slots=True)
class ViewsReport:
    """Which views the accounting database actually has."""

    present: frozenset[ViewName]
    missing: frozenset[ViewName]

    @property
    def voids_available(self) -> bool:
        """True when refunds and cancellations can be read (accounting migration 7 or later)."""
        return ViewName.VOIDS in self.present

    @property
    def usable(self) -> bool:
        """True when every required view is present; a reshaped body fails when its feed is read."""
        return REQUIRED_VIEWS <= self.present


@dataclass(frozen=True, slots=True)
class PagedRead:
    """A keyset query over one view: `(key_at, key_id)` strictly increasing, never OFFSET."""

    view: ViewName
    key_at: str
    key_id: str
    sql: str


def _paged(view: ViewName, key_at: str, key_id: str) -> PagedRead:
    selected = ", ".join(COLUMNS[view])
    return PagedRead(
        view=view,
        key_at=key_at,
        key_id=key_id,
        sql=(
            f"SELECT {selected} FROM {view} "
            f"WHERE ({key_at} > :after_at OR ({key_at} = :after_at AND {key_id} > :after_id)) "
            f"AND {key_at} <= :until "
            f"ORDER BY {key_at}, {key_id} LIMIT :limit"
        ),
    )


PAGED_READS: Mapping[ViewName, PagedRead] = {
    ViewName.CASH_RECEIPTS: _paged(ViewName.CASH_RECEIPTS, "closed_at", "invoice_id"),
    ViewName.CONSUMPTION: _paged(ViewName.CONSUMPTION, "closed_at", "ledger_id"),
    ViewName.VITALS_SERVICES: _paged(ViewName.VITALS_SERVICES, "closed_at", "injection_id"),
    ViewName.PATIENT_REGISTRATIONS: _paged(ViewName.PATIENT_REGISTRATIONS, "created_at", "patient_id"),
    # The same keyset over the v2 columns: a `first_for_phone` row is the same registration, so the
    # watermark of the feed it replaces carries the same key.
    ViewName.PATIENT_REGISTRATIONS_V2: _paged(ViewName.PATIENT_REGISTRATIONS_V2, "created_at", "patient_id"),
    ViewName.VOIDS: _paged(ViewName.VOIDS, "voided_at", "void_id"),
}

OPEN_INVOICES_SQL = (
    f"SELECT {', '.join(COLUMNS[ViewName.OPEN_INVOICES])} FROM {ViewName.OPEN_INVOICES} ORDER BY invoice_id"
)

# The manager's control-call sample: name and mobile of a few patients, read at the moment the card
# page is rendered and never written anywhere (owner decision 5). Only the drawn ids are asked for.
CALL_SAMPLE_SQL = (
    f"SELECT {', '.join(COLUMNS[ViewName.CALL_SAMPLE])} FROM {ViewName.CALL_SAMPLE} "
    f"WHERE patient_id IN ({{placeholders}}) ORDER BY patient_id"
)


def call_sample_contacts(connection: Connection, patient_ids: Sequence[int]) -> dict[int, Mapping[str, Any]]:
    """Name and mobile of the drawn patients, read now and never stored (owner decision 5).

    The caller renders them into one manager page and forgets them: accounting's view is the only
    place a mobile lives, so the shift database, its events, its logs and the stored card hold none.
    """
    if not patient_ids:
        return {}
    placeholders = ", ".join("?" for _ in patient_ids)
    rows = connection.exec_driver_sql(CALL_SAMPLE_SQL.format(placeholders=placeholders), list(patient_ids)).mappings()
    return {int(row["patient_id"]): row for row in rows}


def introspect(connection: Connection) -> ViewsReport:
    """Report which contract views exist, reading `sqlite_master` only (never a view body)."""
    existing = {str(row[0]) for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'view'")}
    present = frozenset(view for view in ViewName if view.value in existing)
    return ViewsReport(present=present, missing=frozenset(set(ViewName) - present))


def require_views(report: ViewsReport) -> None:
    """Fail loudly when a required view is absent, naming the accounting migration that adds it."""
    missing = sorted(view.value for view in report.missing & REQUIRED_VIEWS)
    if missing:
        raise ViewContractError(
            f"accounting is missing the shift contract view(s) {', '.join(missing)}. "
            "Upgrade accounting to migration 4 or later (v_shift_*_v1) and re-poll."
        )


def parse_time(text: str) -> datetime:
    """Read accounting's Tehran local timestamp text as an aware instant."""
    return datetime.strptime(text, ACCOUNTING_TIME_FORMAT).replace(tzinfo=TEHRAN)


def format_time(at: datetime) -> str:
    """Render an instant the way accounting stores timestamps, so SQL text comparison is exact."""
    return require_aware(at).astimezone(TEHRAN).strftime(ACCOUNTING_TIME_FORMAT)
