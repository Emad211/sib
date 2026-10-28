"""Accounting read bridge: keyset polling of `v_shift_*` into mirrors and events (SHF-1.3).

One `poll` copies every accounting fact stamped at or before the shift's cutoff, page by page, in
`(timestamp, id)` order and never with OFFSET. Each page is committed together with its watermark,
and the cash page also appends `acc.cash_receipt` events keyed `acc:<view>:<invoice_id>`, so a crash,
a re-poll or a lost watermark cannot count the same row twice.

Only cash produces events today: it is the one accounting fact whose payload the event registry can
express on its own (a Toman amount and the shift it belongs to). Consumption needs the accounting
item code resolved to a shelf — the versioned catalog arrives with SHF-1.4 — and vitals, patient
registrations and voids are mirrors that the handover and reward screens read directly. A voided
invoice's cash is already gone from `v_shift_cash_receipts_v1` (accounting migration 7); refunding
cash the bridge had ingested before the void needs a compensating event, which waits on a
refund-aware projection. Mirroring the void is what this step can do without inventing vocabulary.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from shift.adapters.accounting import views
from shift.adapters.accounting.readonly import AccountingDatabase, AccountingUnavailable, open_accounting
from shift.adapters.accounting.views import PagedRead, ViewName
from shift.adapters.db import mirror_repo, watermark_repo
from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, utc_iso
from shift.common.ids import new_ulid
from shift.domain.cutoff import ShiftCutoff
from shift.domain.events import EventType
from shift.services.event_store import EventStore, NewEvent

log = logging.getLogger(__name__)

BRIDGE_ACTOR_ID = "accounting-bridge"
BRIDGE_DEVICE_ID = "accounting-bridge"
DEFAULT_PAGE_SIZE = 500
_MAX_ERROR_LENGTH = 500

MirrorRow = dict[str, Any]
MirrorBuilder = Callable[[Mapping[str, Any], str], MirrorRow]
MirrorInserter = Callable[[Connection, Sequence[Mapping[str, Any]]], int]


def _round_half_up(value: Any) -> int:
    """Accounting stores REAL; every count the shift app uses is a whole number, rounded once."""
    return int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))


def _cash_mirror(row: Mapping[str, Any], ingested_at: str) -> MirrorRow:
    return {
        "invoice_id": int(row["invoice_id"]),
        "closed_at": str(row["closed_at"]),
        "amount": _round_half_up(row["amount"]),
        "amount_raw": float(row["amount"]),
        "ingested_at": ingested_at,
    }


def _consumption_mirror(row: Mapping[str, Any], ingested_at: str) -> MirrorRow:
    return {
        "ledger_id": int(row["ledger_id"]),
        "invoice_id": int(row["invoice_id"]),
        "closed_at": str(row["closed_at"]),
        "acc_code": row["acc_code"],
        "quantity": _round_half_up(row["quantity"]),
        "quantity_raw": float(row["quantity"]),
        "item_name": str(row["item_name"]),
        "ingested_at": ingested_at,
    }


def _vital_mirror(row: Mapping[str, Any], ingested_at: str) -> MirrorRow:
    return {
        "injection_id": int(row["injection_id"]),
        "invoice_id": int(row["invoice_id"]),
        "nurse_id": row["nurse_id"],
        "kind": str(row["type"]),
        "closed_at": str(row["closed_at"]),
        "ingested_at": ingested_at,
    }


def _registration_mirror(row: Mapping[str, Any], ingested_at: str) -> MirrorRow:
    return {
        "patient_id": int(row["patient_id"]),
        "created_by": row["created_by"],
        "created_at": str(row["created_at"]),
        "has_valid_nid": int(row["has_valid_nid"]),
        "has_valid_phone": int(row["has_valid_phone"]),
        "is_foreign": int(row["is_foreign"]),
        "ingested_at": ingested_at,
    }


def _void_mirror(row: Mapping[str, Any], ingested_at: str) -> MirrorRow:
    return {
        "void_id": int(row["void_id"]),
        "invoice_id": int(row["invoice_id"]),
        "kind": str(row["kind"]),
        "amount": _round_half_up(row["amount"]),
        "amount_raw": float(row["amount"]),
        "refund_method": row["refund_method"],
        "reason": str(row["reason"]),
        "voided_by": row["voided_by"],
        "voided_at": str(row["voided_at"]),
        "work_date": row["work_date"],
        "shift": row["shift"],
        "ingested_at": ingested_at,
    }


@dataclass(frozen=True, slots=True)
class Feed:
    """One accounting view with everything the bridge does with its rows."""

    read: PagedRead
    mirror: MirrorBuilder
    insert: MirrorInserter
    event_type: EventType | None = None


FEEDS: Mapping[ViewName, Feed] = {
    ViewName.CASH_RECEIPTS: Feed(
        views.PAGED_READS[ViewName.CASH_RECEIPTS],
        _cash_mirror,
        mirror_repo.insert_cash,
        EventType.ACC_CASH_RECEIPT,
    ),
    ViewName.CONSUMPTION: Feed(
        views.PAGED_READS[ViewName.CONSUMPTION], _consumption_mirror, mirror_repo.insert_consumption
    ),
    ViewName.VITALS_SERVICES: Feed(
        views.PAGED_READS[ViewName.VITALS_SERVICES], _vital_mirror, mirror_repo.insert_vitals
    ),
    ViewName.PATIENT_REGISTRATIONS: Feed(
        views.PAGED_READS[ViewName.PATIENT_REGISTRATIONS], _registration_mirror, mirror_repo.insert_registrations
    ),
    ViewName.VOIDS: Feed(views.PAGED_READS[ViewName.VOIDS], _void_mirror, mirror_repo.insert_voids),
}


@dataclass(frozen=True, slots=True)
class PollReport:
    """What one poll did, or why it could not read accounting (R-06)."""

    degraded: bool
    voids_available: bool
    rows: Mapping[str, int]
    events: int
    pages: int
    open_invoices: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _FeedResult:
    rows: int
    events: int
    pages: int


class AccountingBridge:
    """Reads the accounting views a page at a time and records each fact exactly once."""

    def __init__(
        self,
        database: Database,
        store: EventStore,
        *,
        clock: Clock | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._database = database
        self._store = store
        self._clock = clock or SystemClock()
        self._page_size = page_size

    def poll(self, accounting_path: str | Path, shift: ShiftCutoff) -> PollReport:
        """Ingest every accounting fact up to `shift.cutoff_at`; never raises for accounting problems."""
        try:
            accounting = open_accounting(accounting_path)
        except AccountingUnavailable as exc:
            log.warning("accounting bridge degraded: %s", exc)
            return self._degraded(str(exc), voids_available=False)
        try:
            return self._poll(accounting, shift)
        finally:
            accounting.close()

    def _poll(self, accounting: AccountingDatabase, shift: ShiftCutoff) -> PollReport:
        with accounting.connect() as connection:
            contract = views.introspect(connection)
        try:
            views.require_views(contract)
        except views.ViewContractError as exc:
            log.error("accounting bridge degraded: %s", exc)
            return self._degraded(str(exc), voids_available=contract.voids_available)
        if not contract.voids_available:
            log.warning("accounting has no %s; refunds cannot be verified for this handover", ViewName.VOIDS)

        rows: dict[str, int] = {}
        events = pages = 0
        error: str | None = None
        for view, feed in FEEDS.items():
            if view not in contract.present:
                continue
            try:
                result = self._ingest_feed(accounting, feed, shift)
            except SQLAlchemyError as exc:
                self._record_failure(view, exc)
                error = error or f"{view.value}: {exc}"
                log.warning("accounting bridge could not read %s: %s", view.value, exc)
                continue
            rows[view.value] = result.rows
            events += result.events
            pages += result.pages

        open_invoices = 0
        try:
            open_invoices = self._refresh_open_invoices(accounting)
        except SQLAlchemyError as exc:
            error = error or f"{ViewName.OPEN_INVOICES.value}: {exc}"
            log.warning("accounting bridge could not read %s: %s", ViewName.OPEN_INVOICES.value, exc)
        return PollReport(
            degraded=error is not None,
            voids_available=contract.voids_available,
            rows=rows,
            events=events,
            pages=pages,
            open_invoices=open_invoices,
            error=error,
        )

    def _ingest_feed(self, accounting: AccountingDatabase, feed: Feed, shift: ShiftCutoff) -> _FeedResult:
        """Copy one view page by page; the watermark never moves past rows that were not committed."""
        with self._database.read() as connection:
            watermark = watermark_repo.get(connection, feed.read.view.value)
        after_at, after_id = watermark.last_key_at, watermark.last_key_id
        until = views.format_time(shift.cutoff_at)
        rows = events = pages = 0
        while True:
            page = self._read_page(accounting, feed.read, after_at, after_id, until)
            if not page:
                return _FeedResult(rows, events, pages)
            events += self._append_events(feed, page, shift)
            ingested_at = utc_iso(self._clock.now_utc())
            mirrored = [feed.mirror(row, ingested_at) for row in page]
            last = page[-1]
            last_at, last_id = str(last[feed.read.key_at]), int(last[feed.read.key_id])
            with self._database.write() as connection:
                rows += feed.insert(connection, mirrored)
                watermark_repo.save(connection, feed.read.view.value, last_at, last_id, ingested_at)
            pages += 1
            after_at, after_id = last_at, last_id
            if len(page) < self._page_size:
                return _FeedResult(rows, events, pages)

    def _read_page(
        self, accounting: AccountingDatabase, page_read: PagedRead, after_at: str, after_id: int, until: str
    ) -> list[Mapping[str, Any]]:
        with accounting.connect() as connection:
            result = connection.exec_driver_sql(
                page_read.sql,
                {"after_at": after_at, "after_id": after_id, "until": until, "limit": self._page_size},
            )
            return [dict(row) for row in result.mappings()]

    def _append_events(self, feed: Feed, page: Sequence[Mapping[str, Any]], shift: ShiftCutoff) -> int:
        """Append `acc.cash_receipt` for every row of a feed that has one; a retry returns the stored event."""
        if feed.event_type is None:
            return 0
        events: list[NewEvent] = []
        for row in page:
            closed_at = views.parse_time(str(row["closed_at"]))
            events.append(
                NewEvent(
                    client_event_id=new_ulid(closed_at),
                    type=feed.event_type.value,
                    payload={"amount": _round_half_up(row["amount"])},
                    actor_id=BRIDGE_ACTOR_ID,
                    device_id=BRIDGE_DEVICE_ID,
                    work_date=shift.work_date,
                    client_at=closed_at,
                    shift_id=shift.shift_id,
                    idempotency_key=f"acc:{feed.read.view.value}:{row['invoice_id']}",
                )
            )
        return sum(1 for result in self._store.append_many(events) if result.created)

    def _refresh_open_invoices(self, accounting: AccountingDatabase) -> int:
        """Replace the open-invoice snapshot; H-03 needs it at handover time."""
        with accounting.connect() as connection:
            page = [dict(row) for row in connection.exec_driver_sql(views.OPEN_INVOICES_SQL).mappings()]
        with self._database.write() as connection:
            return mirror_repo.replace_open_invoices(connection, page, utc_iso(self._clock.now_utc()))

    def _record_failure(self, view: ViewName, error: Exception) -> None:
        with self._database.write() as connection:
            watermark_repo.record_failure(
                connection, view.value, str(error)[:_MAX_ERROR_LENGTH], utc_iso(self._clock.now_utc())
            )

    @staticmethod
    def _degraded(error: str, *, voids_available: bool) -> PollReport:
        return PollReport(
            degraded=True,
            voids_available=voids_available,
            rows={},
            events=0,
            pages=0,
            open_invoices=0,
            error=error,
        )
