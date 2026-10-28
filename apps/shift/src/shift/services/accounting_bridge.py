"""Accounting read bridge: keyset polling of `v_shift_*` into mirrors and events (SHF-1.3).

One `poll` copies every accounting fact stamped at or before the shift's cutoff, page by page, in
`(timestamp, id)` order and never with OFFSET. Each page is committed together with its watermark,
and the cash page also appends `acc.cash_receipt` events keyed by `(invoice_id, closed_at, amount)`,
so a crash, a re-poll or a lost watermark cannot count the same close twice.

Accounting can take a cash fact back. An invoice that is reopened and closed again arrives with a
new `closed_at`: the bridge keeps the old version in `acc_cash` (`superseded_at`) and appends two
events in one batch — an `acc.cash_reversal` that subtracts the superseded amount and references its
receipt, then a new `acc.cash_receipt` that references the same receipt. A void that arrives through
`v_shift_voids_v1` reverses the cash still active for that invoice. Every correction is idempotent
(its event key names the fact it corrects), and both the correction events and the projections stay
ordinary event-log facts, so `rebuild()` reproduces the live projection exactly.

A re-close can carry a stamp older than the cash watermark (the invoice was already read once), so
the cash feed re-reads a bounded trailing window instead of trusting the watermark alone. Mirror
state is what decides whether a row is new, which keeps that re-read free of double counting.

Only cash produces events today: it is the one accounting fact whose payload the event registry can
express on its own (a Toman amount and the shift it belongs to). Consumption needs the accounting
item code resolved to a shelf — the versioned catalog arrives with SHF-1.4 — and vitals, patient
registrations and voids' own rows are mirrors that the handover and reward screens read directly.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from shift.adapters.accounting import views
from shift.adapters.accounting.readonly import AccountingDatabase, AccountingUnavailable, open_accounting
from shift.adapters.accounting.views import PagedRead, ViewName
from shift.adapters.db import events_repo, mirror_repo, watermark_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, utc_iso
from shift.common.ids import new_ulid
from shift.domain.cutoff import ShiftCutoff
from shift.domain.events import EventType
from shift.services.event_store import EventStore, NewEvent

log = logging.getLogger(__name__)

BRIDGE_ACTOR_ID = "accounting-bridge"
BRIDGE_DEVICE_ID = "accounting-bridge"
DEFAULT_PAGE_SIZE = 500
# Accounting can rewrite an already-read close (reopen then re-close) with an earlier stamp; the cash
# feed re-reads this trailing window on every poll because mirror state makes the re-read idempotent.
CASH_LOOKBACK = timedelta(hours=24)
_MAX_ERROR_LENGTH = 500

MirrorRow = dict[str, Any]
MirrorBuilder = Callable[[Mapping[str, Any], str], MirrorRow]
MirrorInserter = Callable[[Connection, Sequence[Mapping[str, Any]]], int]


def _cash_key(view_name: str, invoice_id: int, closed_at: str, amount: int) -> str:
    """Identity of one close: the same invoice may be closed again at a new stamp or amount."""
    return f"acc:{view_name}:{invoice_id}@{closed_at}@{amount}"


def _legacy_cash_key(view_name: str, invoice_id: int) -> str:
    """Keys written before corrections existed; a superseded receipt may still carry one."""
    return f"acc:{view_name}:{invoice_id}"


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
    corrections: bool = False


FEEDS: Mapping[ViewName, Feed] = {
    ViewName.CASH_RECEIPTS: Feed(
        views.PAGED_READS[ViewName.CASH_RECEIPTS],
        _cash_mirror,
        mirror_repo.insert_cash,
        EventType.ACC_CASH_RECEIPT,
        corrections=True,
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


@dataclass(frozen=True, slots=True)
class _PagePlan:
    """What one accounting page becomes: events to append, then mirror work for one transaction."""

    events: tuple[NewEvent, ...] = ()
    insert: tuple[MirrorRow, ...] = ()
    update: tuple[MirrorRow, ...] = ()
    supersede: tuple[tuple[int, str, str], ...] = ()


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

        if contract.voids_available:
            try:
                events += self._compensate_voids(shift)
            except SQLAlchemyError as exc:
                error = error or f"{ViewName.VOIDS.value}: {exc}"
                log.warning("accounting bridge could not compensate voids: %s", exc)

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
        if feed.corrections:
            # A re-close may re-use a stamp the watermark already passed; re-read a bounded window.
            floor = views.format_time(shift.cutoff_at - CASH_LOOKBACK)
            if floor < after_at:
                after_at, after_id = floor, watermark_repo.EMPTY_WATERMARK_ID
        until = views.format_time(shift.cutoff_at)
        rows = events = pages = 0
        while True:
            page = self._read_page(accounting, feed.read, after_at, after_id, until)
            if not page:
                return _FeedResult(rows, events, pages)
            ingested_at = utc_iso(self._clock.now_utc())
            plan = self._plan_page(feed, page, shift, ingested_at)
            events += self._append_events(plan.events)
            last = page[-1]
            last_at, last_id = str(last[feed.read.key_at]), int(last[feed.read.key_id])
            with self._database.write() as connection:
                rows += self._apply_plan(connection, feed, plan)
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

    def _plan_page(
        self, feed: Feed, page: Sequence[Mapping[str, Any]], shift: ShiftCutoff, ingested_at: str
    ) -> _PagePlan:
        """Turn a page into events and mirror work; the cash feed also subtracts what it supersedes."""
        if feed.corrections:
            return self._plan_cash_page(feed, page, shift, ingested_at)
        return _PagePlan(insert=tuple(feed.mirror(row, ingested_at) for row in page))

    @staticmethod
    def _apply_plan(connection: Connection, feed: Feed, plan: _PagePlan) -> int:
        """Persist one page's mirror work in the page transaction (the events are already appended)."""
        for row in plan.update:
            mirror_repo.update_cash_amount(
                connection,
                int(row["invoice_id"]),
                str(row["closed_at"]),
                int(row["amount"]),
                float(row["amount_raw"]),
                str(row["ingested_at"]),
            )
        for invoice_id, closed_at, superseded_at in plan.supersede:
            mirror_repo.supersede_cash(connection, invoice_id, closed_at, superseded_at)
        return feed.insert(connection, plan.insert) + len(plan.update)

    def _plan_cash_page(
        self, feed: Feed, page: Sequence[Mapping[str, Any]], shift: ShiftCutoff, ingested_at: str
    ) -> _PagePlan:
        """Plan receipts and corrections: a re-close reverses the version it replaced (R-03, S04 §2.1)."""
        view_name = feed.read.view.value
        invoice_ids = sorted({int(row["invoice_id"]) for row in page})
        events: list[NewEvent] = []
        insert: list[MirrorRow] = []
        update: list[MirrorRow] = []
        supersede: list[tuple[int, str, str]] = []
        with self._database.read() as connection:
            history = mirror_repo.cash_versions_for(connection, invoice_ids)
            for row in page:
                invoice_id = int(row["invoice_id"])
                closed_at = str(row["closed_at"])
                mirrored = feed.mirror(row, ingested_at)
                known = history.setdefault(invoice_id, [])
                exact = next((version for version in known if version["closed_at"] == closed_at), None)
                if exact is not None and int(exact["amount"]) == int(mirrored["amount"]):
                    continue  # this close is already ingested, correction included
                active = next((version for version in reversed(known) if version["superseded_at"] is None), None)
                superseded = exact if exact is not None else active
                receipt = None
                if superseded is not None:
                    receipt = self._find_receipt_event(connection, view_name, superseded)
                    if receipt is None:
                        log.error(
                            "cannot correct invoice %s closed at %s: its receipt event is missing",
                            invoice_id,
                            superseded["closed_at"],
                        )
                        continue
                    at = views.parse_time(closed_at)
                    events.append(self._cash_reversal_event(receipt, superseded, at=at, reason="reclosed"))
                events.append(self._cash_receipt_event(mirrored, shift, receipt, view_name))
                if exact is not None:
                    update.append(mirrored)
                    exact.update(mirrored)
                else:
                    if active is not None:
                        supersede.append((invoice_id, str(active["closed_at"]), ingested_at))
                        active["superseded_at"] = ingested_at
                    insert.append(mirrored)
                    known.append({**mirrored, "superseded_at": None})
        return _PagePlan(tuple(events), tuple(insert), tuple(update), tuple(supersede))

    @staticmethod
    def _find_receipt_event(connection: Connection, view_name: str, version: Mapping[str, Any]) -> EventRecord | None:
        """The receipt a correction supersedes; rows written before corrections used the legacy key."""
        invoice_id = int(version["invoice_id"])
        keys = (
            _cash_key(view_name, invoice_id, str(version["closed_at"]), int(version["amount"])),
            _legacy_cash_key(view_name, invoice_id),
        )
        for key in keys:
            event = events_repo.find_by_idempotency_key(connection, key)
            if event is not None:
                return event
        return None

    def _cash_receipt_event(
        self, mirrored: Mapping[str, Any], shift: ShiftCutoff, superseded: EventRecord | None, view_name: str
    ) -> NewEvent:
        """A new close; when it replaces one, its `ref_event_id` names the receipt it supersedes."""
        closed_at_text = str(mirrored["closed_at"])
        closed_at = views.parse_time(closed_at_text)
        return NewEvent(
            client_event_id=new_ulid(closed_at),
            type=EventType.ACC_CASH_RECEIPT.value,
            payload={"amount": int(mirrored["amount"]), "amount_raw": float(mirrored["amount_raw"])},
            actor_id=BRIDGE_ACTOR_ID,
            device_id=BRIDGE_DEVICE_ID,
            work_date=shift.work_date,
            client_at=closed_at,
            shift_id=shift.shift_id,
            ref_event_id=superseded.event_id if superseded is not None else None,
            idempotency_key=_cash_key(view_name, int(mirrored["invoice_id"]), closed_at_text, int(mirrored["amount"])),
        )

    @staticmethod
    def _cash_reversal_event(
        receipt: EventRecord,
        version: Mapping[str, Any],
        *,
        at: datetime,
        reason: str,
        shift_id: str | None = None,
        work_date: date | None = None,
    ) -> NewEvent:
        """The compensating half of a correction: same amount, subtract effect, reference to its receipt."""
        return NewEvent(
            client_event_id=new_ulid(at),
            type=EventType.ACC_CASH_REVERSAL.value,
            payload={
                "amount": int(version["amount"]),
                "amount_raw": float(version["amount_raw"]),
                "reason": reason,
            },
            actor_id=BRIDGE_ACTOR_ID,
            device_id=BRIDGE_DEVICE_ID,
            work_date=work_date or date.fromisoformat(receipt.work_date),
            client_at=at,
            shift_id=shift_id or receipt.shift_id,
            ref_event_id=receipt.event_id,
            idempotency_key=f"acc:cash-reversal:{receipt.event_id}",
        )

    def _compensate_voids(self, shift: ShiftCutoff) -> int:
        """Reverse the cash still active for every voided invoice, once per void (S04 §2.1)."""
        events: list[NewEvent] = []
        compensated: list[int] = []
        with self._database.read() as connection:
            for void in mirror_repo.uncompensated_voids(connection):
                void_id = int(void["void_id"])
                if void["cash_closed_at"] is None:
                    compensated.append(void_id)  # no cash was ever ingested for this invoice
                    continue
                version = {
                    "invoice_id": int(void["invoice_id"]),
                    "closed_at": str(void["cash_closed_at"]),
                    "amount": int(void["cash_amount"]),
                    "amount_raw": float(void["cash_amount_raw"]),
                }
                receipt = self._find_receipt_event(connection, ViewName.CASH_RECEIPTS.value, version)
                if receipt is None:
                    log.error(
                        "void %s cannot be compensated: invoice %s has no receipt event",
                        void_id,
                        version["invoice_id"],
                    )
                    continue
                events.append(
                    self._cash_reversal_event(
                        receipt,
                        version,
                        at=views.parse_time(str(void["voided_at"])),
                        reason="refunded" if str(void["kind"]) == "refund" else "voided",
                        shift_id=shift.shift_id,
                        work_date=shift.work_date,
                    )
                )
                compensated.append(void_id)
        created = self._append_events(events)
        if compensated:
            compensated_at = utc_iso(self._clock.now_utc())
            with self._database.write() as connection:
                for void_id in compensated:
                    mirror_repo.mark_void_compensated(connection, void_id, compensated_at)
        return created

    def _append_events(self, events: Sequence[NewEvent]) -> int:
        """Append a planned batch; a retry returns the stored event, so nothing is counted twice."""
        if not events:
            return 0
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
