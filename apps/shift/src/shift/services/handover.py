"""The handover application service: one persisted flow, its blind counts and its events (H-01..H-13).

Routes arrive in slice 4; this module is the only writer of handover events, because ADR-016 requires
a handover to run against the live server and never from the offline outbox. A flow is rebuilt from its
row and its own events on every call — the step from the ordered `handover.step_advanced` events, the
count statuses from the count events, the signatures from `handover.signed` — so a second device or a
restarted server continues exactly where the first left off.

The reference of a count never leaves this module (R-02, ADR-011). It is frozen once at `start`, kept in
the audit trail (the only place a reference may rest) and compared server-side; `count` and `joint`
answer with a `CountStatus` that carries no digit. Freezing it at the flow's own start is H-11: a cash
receipt stamped after that instant belongs to the next shift and cannot change what this flow compares
against. A settled item count is the shelf's next reference because its count event sets the quantity;
a settled cash chain writes `handover.count_settled`, which rebases the box (ADR-017 item 9). A flow
cannot start at all without a readable accounting copy, because the open-invoice guard and every
reference come from it.

The owner's cash rule (logic document, "a patient who arrives during the handover") makes the settle
carry real: the incoming person admits on paper and takes no money, so every cash the box recorded
after the cutoff stays on top of the agreed figure instead of inside it. A receipt stamped *inside*
the flow's window breaks that rule, and it goes to the manager through the audit trail alone — no
response, header, public log line or client-visible event carries the number (R-02, ADR-011).

The app starts from the manager's initial baseline (`record_baseline`, SHF-7.1), not from a staff
handover: one event seeds the box, every shelf it names and who holds them. Nobody types the outgoing
person either — a flow derives it from the newest flow of its kind's incoming person, or from the
custody the baseline set, and refuses with a Persian message when neither exists.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Connection

from shift.adapters.db import events_repo, handover_repo, projections_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.adapters.db.handover_repo import FlowRow
from shift.common.clock import Clock, SystemClock, parse_iso, to_tehran
from shift.common.ids import new_ulid
from shift.domain.cash_box import CashBox
from shift.domain.cash_cutoff import CashMovement, signed_cash, windows
from shift.domain.events import EventType
from shift.domain.handover_baseline import BaselineRecorded
from shift.domain.handover_counts import CashCount, CountStatus, ItemCount
from shift.domain.handover_formulas import HandoverError
from shift.domain.handover_fsm import FLOW_STEPS, Handover, HandoverFlow, HandoverStep, Signer
from shift.domain.handover_payloads import CountTarget
from shift.domain.handover_steps import STEP_LOCATIONS
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering
from shift.services.accounting_bridge import AccountingBridge
from shift.services.audit import AuditEntry, AuditService
from shift.services.catalog import CatalogService, Item, ItemKind
from shift.services.event_store import EventStore, NewEvent
from shift.services.projections import ProjectionEngine

log = logging.getLogger(__name__)

REFERENCE_ACTION = "handover.reference"
# A receipt stamped inside a flow's window breaks the owner's rule; only the manager is told (R-02).
EXCEPTION_ACTION = "handover.cash_exception"
# The baseline is recorded by an operator command, not by a paired handset (SHF-7.1).
BASELINE_DEVICE_ID = "manager-cli"
# R-02: these refusals carry no number, and each only says what to do before a flow can start.
NO_ACCOUNTING = (
    "نسخهٔ حسابداری در دسترس نیست؛ بدون آن صورت‌حساب‌های باز و عدد مرجع قابل محاسبه نیستند و تحویل شیفت آغاز نمی‌شود."
)
NO_BASELINE = "مدیر باید ابتدا شمارش اولیه را ثبت کند."
_CASH = "cash"
_PENDING = frozenset({CountStatus.OUTGOING, CountStatus.JOINT})
_JOINT_EVENT = EventType.HANDOVER_JOINT_COUNTED.value
_COUNT_EVENTS = frozenset(
    {
        EventType.HANDOVER_CASH_COUNTED.value,
        EventType.HANDOVER_ITEM_COUNTED.value,
        EventType.HANDOVER_OUTGOING_COUNTED.value,
        _JOINT_EVENT,
    }
)
# Every kind whose effect takes money into or out of the reception box; the settle divides them by
# the side of its own cutoff they occurred on (H-11). The registry stays the authority for what an
# event does — `signed_money` reads the effect rather than a second hand-written table.
_MONEY_TYPES = (
    EventType.CASH_WITHDRAWN.value,
    EventType.CASH_DEPOSITED.value,
    EventType.ACC_CASH_RECEIPT.value,
    EventType.ACC_CASH_REVERSAL.value,
)
_CASH_SETTLED = EventType.HANDOVER_COUNT_SETTLED.value
_ADJUSTED = EventType.HANDOVER_CASH_REFERENCE_ADJUSTED.value
_LATE_CASH = EventType.HANDOVER_LATE_CASH_RECORDED.value


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is acting and from which paired device; R-04 stamps both on every event."""

    actor_id: str
    device_id: str


class NotASigner(HandoverError):
    """The caller is neither the incoming nor the outgoing person of this flow (H-04, R-04)."""


@dataclass(frozen=True, slots=True)
class HandoverState:
    """What a client may know about a flow: where it is, never a reference and never a count."""

    handover_id: str
    flow: HandoverFlow
    shift_id: str
    step: HandoverStep | None
    cash_status: CountStatus | None
    item_statuses: Mapping[str, CountStatus]
    signatures: frozenset[Signer]
    early_left: bool
    emergency_by: str | None
    # H-08 records the manager's phone confirmation with who and when; this is the when.
    emergency_at: datetime | None
    closed: bool


@dataclass(frozen=True, slots=True)
class _References:
    """Every chain's frozen reference for one flow, read once at its start (H-11, D4)."""

    cash: int | None
    items: Mapping[tuple[str, str], int]


@dataclass(frozen=True, slots=True)
class BaselineRecord:
    """What one baseline fixed: the flow, who holds it, when, and how many shelves it set."""

    flow: HandoverFlow
    custodian_id: str
    manager_id: str
    recorded_at: datetime
    opening_cash: int | None
    shelves: int


@dataclass(frozen=True, slots=True)
class _Carry:
    """What the box recorded during one flow, and the receipts inside its window that should not exist."""

    amount: int
    receipts: int
    outflow: int = 0


@dataclass(frozen=True, slots=True)
class _Flow:
    """One flow as its own log describes it: the domain state plus the chains rebuilt so far."""

    row: FlowRow
    handover: Handover
    references: _References
    cash: CashCount | None
    items: Mapping[tuple[str, str], ItemCount]
    emergency_at: datetime | None
    # Where in the log this flow starts, so "appended after the flow began" is a sequence question
    # and never a clock one (H-11), and how far its cash reference has already been adjusted.
    start_seq: int = 0
    adjustment: int = 0


class HandoverService:
    """Starts flows, records every count and closes them; the single writer of handover events."""

    def __init__(
        self,
        database: Database,
        store: EventStore,
        *,
        projections: ProjectionEngine,
        catalog: CatalogService,
        audit: AuditService,
        clock: Clock | None = None,
        bridge: AccountingBridge | None = None,
        accounting_path: str | None = None,
    ) -> None:
        self._database = database
        self._store = store
        self._projections = projections
        self._catalog = catalog
        self._audit = audit
        self._clock = clock or SystemClock()
        self._bridge = bridge
        self._accounting_path = accounting_path

    def record_baseline(
        self,
        *,
        flow: HandoverFlow,
        custodian_id: str,
        manager_id: str,
        opening_cash: int | None,
        items: Sequence[Mapping[str, Any]],
    ) -> BaselineRecord:
        """Record the manager's opening count: the box, every shelf it names and who holds them.

        A baseline is not a staff handover, and nothing is recorded against anyone by it: no reference
        is compared, no culprit is named, no discrepancy is written (SHF-7.1). It is refused before
        anything is written when this flow already has one, because the first baseline is what the
        app's blind counts compare against.

        The box, the shelves and the custody row are written from one act: the event is the truth, the
        projection is caught up from it and the custody row records who holds the flow now.
        """
        now = self._clock.now_utc()
        shift = covering(to_tehran(now))
        try:
            baseline = BaselineRecorded(
                flow=flow,
                custodian_id=custodian_id,
                manager_id=manager_id,
                opening_cash=opening_cash,
                items=tuple(items),
            )
        except ValidationError as exc:
            raise HandoverError(f"the baseline is not a valid opening count: {exc.errors()[0]['msg']}") from exc
        with self._database.read() as connection:
            if handover_repo.has_baseline(connection, flow):
                raise HandoverError(f"the {flow.value} flow already recorded its baseline")
        self._store.append(
            NewEvent(
                client_event_id=new_ulid(now),
                type=EventType.HANDOVER_BASELINE_RECORDED.value,
                payload=baseline.model_dump(mode="json"),
                actor_id=baseline.manager_id,
                device_id=BASELINE_DEVICE_ID,
                work_date=shift.work_date,
                client_at=now,
                shift_id=shift.text,
            )
        )
        # The first flow reads its reference from the projection, so the baseline must be applied
        # before custody is written and before any flow starts (ADR-010).
        self._projections.sync()
        with self._database.write() as connection:
            handover_repo.set_custodian(
                connection,
                flow=flow,
                custodian_id=baseline.custodian_id,
                updated_at=now,
                updated_by=baseline.manager_id,
            )
        return BaselineRecord(
            flow=baseline.flow,
            custodian_id=baseline.custodian_id,
            manager_id=baseline.manager_id,
            recorded_at=now,
            opening_cash=baseline.opening_cash,
            shelves=len(baseline.items),
        )

    def custodian(self, flow: HandoverFlow) -> str | None:
        """Who hands this flow over: the newest flow's incoming person, else the baseline's custodian.

        A first flow has no predecessor, so the manager's baseline names who holds the box and the
        shelves now. None means neither exists: the app has no opening count yet, so nothing may start.
        """
        with self._database.read() as connection:
            previous = handover_repo.newest_flow(connection, flow)
            if previous is not None:
                return previous.incoming_id
            return handover_repo.custodian(connection, flow)

    def start(
        self,
        flow: HandoverFlow,
        *,
        incoming_id: str,
        caller: Caller,
        outgoing_id: str | None = None,
    ) -> HandoverState:
        """Open one flow of the shift the clinic clock says is running (D1), with its own cutoff.

        The accounting copy must be readable first: H-03's open-invoice guard and every reference are
        computed from it, so a missing or unreachable copy refuses the start.

        Who hands over is derived, never typed: `outgoing_id` only carries what `custodian` already
        answered, so a caller that had to read it does not read it twice. Without either a previous
        flow or a baseline custodian there is nothing to count against, and the refusal tells staff to
        ask the manager, with no number in it (R-02).
        """
        self._read_accounting_now()
        started_at = self._clock.now_utc()
        shift = covering(to_tehran(started_at))
        outgoing = outgoing_id if outgoing_id is not None else self.custodian(flow)
        if outgoing is None:
            raise HandoverError(NO_BASELINE)
        with self._database.read() as connection:
            open_invoices = handover_repo.open_invoice_count(connection)
            taken = handover_repo.find_shift_flow(connection, shift.text, flow)
        if taken is not None:
            raise HandoverError(f"this shift already has a {flow} handover (H-12)")
        Handover.start(
            flow,
            shift_id=shift.text,
            work_date=shift.work_date,
            started_at=started_at,
            open_invoices=open_invoices,
            outgoing_id=outgoing,
            incoming_id=incoming_id,
        )
        references = self._freeze(flow, shift)
        handover_id = new_ulid(started_at)
        self._audit_references(handover_id, caller, references)
        with self._database.write() as connection:
            handover_repo.insert(
                connection,
                handover_id=handover_id,
                shift_id=shift.text,
                flow=flow,
                opened_at=started_at,
                work_date=shift.work_date,
                outgoing_id=outgoing,
                incoming_id=incoming_id,
                created_at=started_at,
            )
        row = self._row(handover_id)
        # The box was seeded by the manager's baseline, so a start event names no opening figure.
        self._append(row, caller, [(EventType.HANDOVER_STARTED.value, {"opening_cash": None})])
        return self.state(handover_id)

    def state(self, handover_id: str) -> HandoverState:
        """Rebuild the flow from its row and its own events; no in-memory state is trusted.

        The item statuses cover the step the flow stands on, so a client never has to guess which
        shelf or trolley is being counted, and a closed flow reports no step at all.
        """
        with self._database.read() as connection:
            flow = self._rebuild(connection, self._row_on(connection, handover_id))
        handover = flow.handover
        step = handover.step
        location = STEP_LOCATIONS[step] if step is not None else None
        statuses: dict[str, CountStatus] = {}
        if location is not None:
            for item in self._counted_items(ShiftKey.parse(flow.row.shift_id), location):
                chain = flow.items.get((item.item_id, location))
                statuses[item.item_id] = chain.status if chain is not None else CountStatus.FIRST
        return HandoverState(
            handover_id=flow.row.handover_id,
            flow=flow.row.flow,
            shift_id=flow.row.shift_id,
            step=step,
            cash_status=_cash_status(flow),
            item_statuses=statuses,
            signatures=handover.signatures,
            early_left=handover.early_left,
            emergency_by=handover.emergency_by,
            emergency_at=flow.emergency_at,
            closed=handover.closed,
        )

    def count(self, handover_id: str, target: CountTarget, counted: int, *, caller: Caller) -> CountStatus:
        """Take one blind count and answer with a status only; the reference stays here (R-02)."""
        flow, events = self._cash_reference_ready(self._live(handover_id), target)
        chain = self._current(flow, target)
        if chain.status is CountStatus.JOINT:
            raise HandoverError("the joint count is recorded through `joint`")
        if chain.settled:
            raise HandoverError("this target is already settled")
        event_type = _count_event(chain.status, target)
        new_chain, status = chain.count(counted)
        events.append((event_type, _count_payload(target, counted)))
        if status in _PENDING:
            events.append((EventType.HANDOVER_RECOUNT_REQUESTED.value, _target_payload(target)))
        carry = self._settlement_carry(flow, target, new_chain)
        events += _settlement(
            target,
            new_chain,
            basis="agreeing_blind" if target.scope == _CASH else "matched",
            carry=0 if carry is None else carry.amount,
        )
        self._append(flow.row, caller, events)
        self._report_carry(flow, caller, carry)
        return status

    def joint(self, handover_id: str, target: CountTarget, counted: int, *, caller: Caller) -> CountStatus:
        """The figure both people counted together; it settles the chain (H-05, H-13)."""
        flow, events = self._cash_reference_ready(self._live(handover_id), target)
        chain = self._current(flow, target)
        if chain.status is not CountStatus.JOINT:
            raise HandoverError("this target has no joint count to record")
        new_chain, status = chain.count(counted)
        events.append((_JOINT_EVENT, {**_target_payload(target), "counted": counted}))
        carry = self._settlement_carry(flow, target, new_chain)
        events += _settlement(target, new_chain, basis="joint", carry=0 if carry is None else carry.amount)
        self._append(flow.row, caller, events)
        self._report_carry(flow, caller, carry)
        return status

    def advance(self, handover_id: str, *, caller: Caller) -> HandoverState:
        """Leave the current step for the next one; the walk is ordered and never skips (H-04)."""
        flow = self._live(handover_id)
        advanced = flow.handover.advance()  # the domain refuses a closed flow, a skipped sign or H-08
        self._require_step_done(flow)
        step = advanced.step
        if step is None:
            raise HandoverError("the step sign closes the handover; it cannot be entered")
        self._append(flow.row, caller, [(EventType.HANDOVER_STEP_ADVANCED.value, {"step": step.value})])
        return self.state(handover_id)

    def answer_checklist(
        self,
        handover_id: str,
        item_id: str,
        *,
        ok: bool,
        caller: Caller,
        reading: float | None = None,
        photo_id: str | None = None,
    ) -> HandoverState:
        """Record one checklist answer on the checklists step (H-04); it changes no projection."""
        flow = self._require_step(handover_id, HandoverStep.CHECKLISTS)
        payload = {"item_id": item_id, "ok": ok, "reading": reading, "photo_id": photo_id}
        self._append(flow.row, caller, [(EventType.HANDOVER_CHECKLIST_ANSWERED.value, payload)])
        return self.state(handover_id)

    def hand_parcel(self, handover_id: str, parcel_id: str, packages: int, *, caller: Caller) -> HandoverState:
        """Record one parcel in transit changing hands on the parcels step (H-04)."""
        flow = self._require_step(handover_id, HandoverStep.PARCELS)
        payload = {"parcel_id": parcel_id, "packages": packages}
        self._append(flow.row, caller, [(EventType.HANDOVER_PARCEL_HANDED.value, payload)])
        return self.state(handover_id)

    def open_flows(self, shift_id: str) -> list[HandoverState]:
        """The flows of one shift still waiting for a signature, each rebuilt from its own log.

        The second person of a handover opens the screen on their own phone and joins the flow the
        first phone opened (S03 §3); a flow of another shift is never listed, and a closed one is
        gone. Nothing here carries a reference or a count, only the statuses a client needs.
        """
        with self._database.read() as connection:
            rows = [row for row in handover_repo.open_flows(connection) if row.shift_id == shift_id]
        return [self.state(row.handover_id) for row in rows]

    def sign(self, handover_id: str, *, caller: Caller) -> HandoverState:
        """Record the caller's own signature at the dual-sign step; the last one closes the flow.

        H-04's two signatures are each recorded by the person who signed, so the signer is derived
        from the session rather than typed: a phone can only sign its own side, and the event's actor
        is the person whose signature it is (R-04). Anyone else is refused (H-09 keeps the block to
        its two ends). The domain still refuses an absent outgoing person on an emergency flow (H-08)
        and a second signature from the same side.
        """
        flow = self._live(handover_id)
        signer = _signer_of(flow.row, caller)
        signed = flow.handover.sign(signer)
        self._append(flow.row, caller, [(EventType.HANDOVER_SIGNED.value, {"signer": signer.value})])
        if signed.closed:
            self._mark_signed(flow.row, caller)
        return self.state(handover_id)

    def report_early_leave(self, handover_id: str, *, caller: Caller) -> HandoverState:
        """Note that the outgoing person left before handing over (H-08); nothing happens by itself."""
        flow = self._live(handover_id)
        flow.handover.report_early_leave()
        payload = {"staff_id": flow.row.outgoing_id}
        self._append(flow.row, caller, [(EventType.HANDOVER_EARLY_LEAVE_REPORTED.value, payload)])
        with self._database.write() as connection:
            handover_repo.mark_early_left(connection, handover_id)
        return self.state(handover_id)

    def confirm_emergency_start(self, handover_id: str, manager_id: str, *, caller: Caller) -> HandoverState:
        """Record the manager's phone confirmation that the incoming person finishes alone (H-08)."""
        flow = self._live(handover_id)
        confirmed = flow.handover.confirm_emergency_start(manager_id)
        payload = {"manager_id": manager_id}
        self._append(flow.row, caller, [(EventType.HANDOVER_EMERGENCY_START_CONFIRMED.value, payload)])
        with self._database.write() as connection:
            handover_repo.mark_emergency(connection, handover_id, manager_id)
        if confirmed.closed:
            self._mark_signed(flow.row, caller)
        return self.state(handover_id)

    def _read_accounting_now(self) -> None:
        """Read accounting at the flow's own cutoff (S02 §2); without it the flow cannot start (H-03).

        The open-invoice guard and the reference numbers only exist in the accounting copy, so a
        missing or unreachable copy refuses the start instead of guessing: a silent allow would
        produce false discrepancies. The refusal names no number (R-02).
        """
        if self._bridge is None or self._accounting_path is None:
            raise HandoverError(NO_ACCOUNTING)
        report = self._bridge.poll(self._accounting_path)
        if report.degraded:
            log.warning("the handover start read failed: %s", report.error)
            raise HandoverError(NO_ACCOUNTING)
        self._projections.sync()

    def _freeze(self, flow: HandoverFlow, shift: ShiftKey) -> _References:
        """Freeze every chain's reference at the flow's start; H-11 forbids reading it later."""
        with self._database.read() as connection:
            stock = {
                (str(row["item_id"]), str(row["location_id"])): int(row["qty"])
                for row in projections_repo.stock_rows(connection)
            }
            box = next(
                (
                    row
                    for row in projections_repo.cash_rows(connection)
                    if row["cash_box_id"] == CashBox.RECEPTION.value
                ),
                None,
            )
        return _References(cash=self._cash_reference(flow, box), items=self._item_references(flow, shift, stock))

    @staticmethod
    def _cash_reference(flow: HandoverFlow, box: Mapping[str, Any] | None) -> int | None:
        """The box reference a flow freezes, or None for the nursing flow that holds no cash (H-12).

        The manager's baseline seeds the box, so a reception flow that finds none has nothing to
        compare against: guessing here would become a false discrepancy later.
        """
        if flow is not HandoverFlow.RECEPTION:
            return None
        if box is None:
            raise HandoverError("the cash box has no figure; the manager's baseline has to seed it")
        return int(box["expected"])

    def _settlement_carry(self, flow: _Flow, target: CountTarget, chain: ItemCount | CashCount) -> _Carry | None:
        """The cash a settling chain must keep on top of its figure, or None when nothing settles.

        The carry is the signed net of the money that **occurred after** the flow's cutoff — a
        receipt puts money on top of the agreed figure, a withdrawal or a deposit takes money off it
        — because that is the next shift's money: the incoming person takes nothing during the
        handover (H-11). What occurred **before** the cutoff is already inside the counted figure,
        so it is never subtracted a second time here; it moved the reference instead.

        A receipt stamped inside the flow's own window cannot happen in the owner's sequence and is
        summed for the manager, and a withdrawal inside it is reported the same way: the cash really
        moved, so it is accepted and flagged rather than refused.
        """
        if not chain.settled:
            return None
        if target.scope != _CASH or flow.references.cash is None:
            return _Carry(amount=0, receipts=0)
        movements = self._money_movements(flow.start_seq)
        split = windows(movements, flow.row.opened_at)
        with self._database.read() as connection:
            receipts = handover_repo.cash_receipt_total(
                connection, after=flow.row.opened_at, until=self._clock.now_utc()
            )
        return _Carry(amount=split.carry, receipts=receipts, outflow=split.outflow)

    def _cash_reference_ready(
        self, flow: _Flow, target: CountTarget
    ) -> tuple[_Flow, list[tuple[str, Mapping[str, Any]]]]:
        """Move the flow's cash reference to where its own log says it now is (H-11).

        A cash event that occurred before this flow's cutoff but arrived while it was being counted
        belongs inside the counted figure: the frozen reference gains its delta before the
        comparison, and the flow writes that delta into its own log so a rebuild replays exactly the
        comparison the two people saw. Only then can `expected = counted + carry` be honest.
        """
        if target.scope != _CASH or flow.references.cash is None:
            return flow, []
        delta = self._pending_adjustment(flow)
        if not delta:
            return flow, []
        reference = flow.cash.reference if flow.cash is not None else flow.references.cash
        adjusted = replace(flow.cash or CashCount(reference, flow.handover.culprit_id), reference=reference + delta)
        return replace(flow, cash=adjusted, adjustment=flow.adjustment + delta), [(_ADJUSTED, {"delta": delta})]

    def _pending_adjustment(self, flow: _Flow) -> int:
        """How far the reference still has to move for the pre-cutoff money that arrived late."""
        split = windows(self._money_movements(flow.start_seq), flow.row.opened_at)
        return split.before_cutoff - flow.adjustment

    def _money_movements(self, after_seq: int) -> list[CashMovement]:
        """Every money event appended after a flow started, with when it occurred and what it moved."""
        with self._database.read() as connection:
            records = events_repo.money_movements_after(connection, after_seq, _MONEY_TYPES)
        movements: list[CashMovement] = []
        for record in records:
            amount = signed_cash(record.type, record.payload, record.shift_id)
            if amount:
                movements.append(CashMovement(seq=record.seq, occurred_at=parse_iso(record.client_at), amount=amount))
        return movements

    def _report_carry(self, flow: _Flow, caller: Caller, carry: _Carry | None) -> None:
        """Tell the manager, through the audit trail only, that a receipt landed inside a flow.

        The audit trail is the one sink allowed to carry a number (R-02, ADR-011): no response, no
        response header, no `logs/shift.log` line and no client-visible event ever carries it. A
        withdrawal inside the window is flagged the same way: the cash really moved, so the record
        is accepted and the manager is told instead of the operation being refused.
        """
        if carry is None:
            return
        detail: dict[str, Any] = {"flow": flow.row.flow.value, "amount": carry.receipts}
        if carry.outflow:
            detail["outflow"] = carry.outflow
        if not carry.receipts and not carry.outflow:
            return
        self._audit.record(
            AuditEntry(
                actor_id=caller.actor_id,
                action=EXCEPTION_ACTION,
                subject_id=flow.row.handover_id,
                detail=detail,
            )
        )

    def _item_references(
        self, flow: HandoverFlow, shift: ShiftKey, stock: Mapping[tuple[str, str], int]
    ) -> dict[tuple[str, str], int]:
        """Every countable item of the flow's steps, at the quantity the projection holds now."""
        items: dict[tuple[str, str], int] = {}
        for step in FLOW_STEPS[flow]:
            location = STEP_LOCATIONS[step]
            if location is None:
                continue
            for item in self._counted_items(shift, location):
                key = (item.item_id, location)
                items[key] = stock.get(key, 0)
        return items

    def _counted_items(self, shift: ShiftKey, location: str) -> list[Item]:
        """The countable items of one step's location, as the catalog sees them at the flow's shift."""
        return [item for item in self._catalog.items(shift, location_id=location) if item.kind is ItemKind.COUNTED]

    def _live(self, handover_id: str) -> _Flow:
        """The flow rebuilt from its log; a closed flow accepts no more work."""
        with self._database.read() as connection:
            flow = self._rebuild(connection, self._row_on(connection, handover_id))
        if flow.handover.closed:
            raise HandoverError("the handover is already closed")
        return flow

    def _require_step(self, handover_id: str, entry: HandoverStep) -> _Flow:
        """One kind of note belongs to one step of the walk; recording it elsewhere is a client error."""
        flow = self._live(handover_id)
        if flow.handover.step is not entry:
            raise HandoverError(f"{entry.value} work belongs to the {entry.value} step")
        return flow

    def _require_step_done(self, flow: _Flow) -> None:
        """Leave the cash step once the box is settled, an item step once every item has settled (H-05).

        A step whose location holds no counted item in the catalog still advances: an empty shelf is
        a real state, and nobody may be stuck on a step with nothing to count.
        """
        step = flow.handover.step
        if step is HandoverStep.CASH:
            if flow.cash is None or not flow.cash.settled:
                raise HandoverError("the cash box has to be counted and settled before the shelf")
            return
        location = STEP_LOCATIONS[step] if step is not None else None
        if location is None:
            return
        for item in self._counted_items(ShiftKey.parse(flow.row.shift_id), location):
            key = (item.item_id, location)
            chain = flow.items.get(key)
            if key in flow.references.items and (chain is None or not chain.settled):
                raise HandoverError(f"item {item.item_id!r} is not counted and settled on this step")

    def _current(self, flow: _Flow, target: CountTarget) -> ItemCount | CashCount:
        """The chain a count belongs to, on the step that owns its target."""
        step = flow.handover.step
        if target.scope == _CASH:
            if step is not HandoverStep.CASH or flow.references.cash is None:
                raise HandoverError("the cash box is counted on the cash step of a reception handover")
            chain = flow.cash
            return chain if chain is not None else CashCount(flow.references.cash, flow.handover.culprit_id)
        key = (str(target.item_id), str(target.location_id))
        if step is None or STEP_LOCATIONS[step] != target.location_id:
            raise HandoverError("this item is not counted on the current step")
        if key not in flow.references.items:
            raise HandoverError("this item was not part of the handover when it started")
        chain = flow.items.get(key)
        return chain if chain is not None else ItemCount(flow.references.items[key], flow.handover.culprit_id)

    def _row(self, handover_id: str) -> FlowRow:
        with self._database.read() as connection:
            return self._row_on(connection, handover_id)

    @staticmethod
    def _row_on(connection: Connection, handover_id: str) -> FlowRow:
        row = handover_repo.get(connection, handover_id)
        if row is None:
            raise HandoverError(f"unknown handover: {handover_id!r}")
        return row

    def _rebuild(self, connection: Connection, row: FlowRow) -> _Flow:
        """Replay the flow's own events into the domain state and the count chains."""
        references = self._references_of(row.handover_id)
        handover = Handover.start(
            row.flow,
            shift_id=row.shift_id,
            work_date=row.work_date,
            started_at=row.opened_at,
            open_invoices=0,
            outgoing_id=row.outgoing_id,
            incoming_id=row.incoming_id,
        )
        items: dict[tuple[str, str], ItemCount] = {}
        cash: CashCount | None = None
        emergency_at: datetime | None = None
        start_seq = 0
        adjustment = 0
        for event in events_repo.iter_handover_events(connection, row.handover_id):
            if start_seq == 0 and event.type == EventType.HANDOVER_STARTED.value:
                # The freeze point: money appended after this row is what the settle has to place.
                start_seq = event.seq
            if event.type == _ADJUSTED:
                adjustment += int(event.payload["delta"])
            handover, cash, items = _replay(event, handover, cash, items, references)
            if event.type == EventType.HANDOVER_EMERGENCY_START_CONFIRMED.value:
                # H-08 records who confirmed the emergency start and when; the when is its server stamp.
                emergency_at = parse_iso(event.server_at)
        return _Flow(row, handover, references, cash, items, emergency_at, start_seq, adjustment)

    def _references_of(self, handover_id: str) -> _References:
        """Read the references frozen at `start` back from the audit trail (R-02 allows them nowhere else)."""
        entries = self._audit.history(action=f"{REFERENCE_ACTION}.{handover_id}", limit=1)
        if not entries:
            raise HandoverError("this handover never froze a reference; it cannot be counted")
        detail = entries[0].detail
        items = {
            (str(entry["item_id"]), str(entry["location_id"])): int(entry["reference"])
            for entry in detail.get("items", [])
        }
        return _References(cash=detail.get("cash"), items=items)

    def _audit_references(self, handover_id: str, caller: Caller, references: _References) -> None:
        """Write the frozen references where a reference may rest: the audit trail (R-02, ADR-011)."""
        self._audit.record(
            AuditEntry(
                actor_id=caller.actor_id,
                action=f"{REFERENCE_ACTION}.{handover_id}",
                subject_id=handover_id,
                detail={
                    "cash": references.cash,
                    "items": [
                        {"item_id": item_id, "location_id": location_id, "reference": reference}
                        for (item_id, location_id), reference in sorted(references.items.items())
                    ],
                },
            )
        )

    def _mark_signed(self, row: FlowRow, caller: Caller) -> None:
        """Close the flow and hand its custody to the incoming person (H-04, SHF-7.1)."""
        now = self._clock.now_utc()
        with self._database.write() as connection:
            handover_repo.mark_signed(connection, row.handover_id, now)
            handover_repo.set_custodian(
                connection,
                flow=row.flow,
                custodian_id=row.incoming_id,
                updated_at=now,
                updated_by=caller.actor_id,
            )

    def _append(self, row: FlowRow, caller: Caller, events: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        """Append one handover batch; every event names its flow and the shift that flow closes (R-04)."""
        moment = self._clock.now_utc()
        self._store.append_many(
            [
                NewEvent(
                    client_event_id=new_ulid(moment),
                    type=event_type,
                    payload=payload,
                    actor_id=caller.actor_id,
                    device_id=caller.device_id,
                    work_date=row.work_date,
                    client_at=moment,
                    shift_id=row.shift_id,
                    handover_id=row.handover_id,
                )
                for event_type, payload in events
            ]
        )
        # The projection catches up immediately: the next count's step and the next flow's reference
        # are read from it, and a rebuild has to equal what it shows (ADR-010).
        self._projections.sync()


def _signer_of(row: FlowRow, caller: Caller) -> Signer:
    """Which side of this flow the caller is; anyone else may not sign it at all (S03 §3)."""
    if caller.actor_id == row.incoming_id:
        return Signer.INCOMING
    if caller.actor_id == row.outgoing_id:
        return Signer.OUTGOING
    raise NotASigner("the caller is not a signer of this handover")


def _cash_status(flow: _Flow) -> CountStatus | None:
    """The box's chain status, or None for a flow that hands over no cash (H-12)."""
    if flow.cash is not None:
        return flow.cash.status
    return CountStatus.FIRST if flow.row.flow is HandoverFlow.RECEPTION else None


def _count_event(status: CountStatus, target: CountTarget) -> str:
    """The count event a chain phase asks for: the incoming blind counts, the outgoing's third."""
    if target.scope == _CASH:
        return EventType.HANDOVER_CASH_COUNTED.value
    if status is CountStatus.OUTGOING:
        return EventType.HANDOVER_OUTGOING_COUNTED.value
    return EventType.HANDOVER_ITEM_COUNTED.value


def _count_payload(target: CountTarget, counted: int) -> dict[str, Any]:
    """The payload of one count event; a cash count never names an item."""
    if target.scope == _CASH:
        return {"counted": counted}
    return {"item_id": target.item_id, "location_id": target.location_id, "counted": counted}


def _target_payload(target: CountTarget) -> dict[str, Any]:
    return {"scope": target.scope, "item_id": target.item_id, "location_id": target.location_id}


def _settlement(
    target: CountTarget, chain: ItemCount | CashCount, *, basis: str, carry: int = 0
) -> list[tuple[str, Mapping[str, Any]]]:
    """What a settled chain writes: the one count event that moves a projection, then any real gap.

    A shelf takes its figure here and nowhere else, and a cash chain rebases the box here, so no
    provisional blind count can become the next flow's reference (H-05, H-13).
    """
    if not chain.settled:
        return []
    events: list[tuple[str, Mapping[str, Any]]] = []
    if target.scope == _CASH:
        settled = {"scope": _CASH, "counted": chain.agreed, "basis": basis, "carry": carry}
    else:
        settled = {**_target_payload(target), "counted": chain.agreed, "basis": basis}
    events.append((EventType.HANDOVER_COUNT_SETTLED.value, settled))
    if chain.discrepancy is not None:
        gap = {"difference": chain.discrepancy.difference, "culprit_id": chain.discrepancy.culprit_id}
        events.append((EventType.DISCREPANCY_RECORDED.value, {**_target_payload(target), **gap}))
    return events


def _replay(
    event: EventRecord,
    handover: Handover,
    cash: CashCount | None,
    items: dict[tuple[str, str], ItemCount],
    references: _References,
) -> tuple[Handover, CashCount | None, dict[tuple[str, str], ItemCount]]:
    """Apply one recorded event to the rebuilt flow; counts compare exactly as they did live."""
    payload = event.payload
    if event.type == EventType.HANDOVER_STEP_ADVANCED.value:
        return _enter_step(handover, payload["step"]), cash, items
    if event.type == EventType.HANDOVER_EARLY_LEAVE_REPORTED.value:
        return handover.report_early_leave(), cash, items
    if event.type == EventType.HANDOVER_EMERGENCY_START_CONFIRMED.value:
        return handover.confirm_emergency_start(payload["manager_id"]), cash, items
    if event.type == EventType.HANDOVER_SIGNED.value:
        return handover.sign(Signer(payload["signer"])), cash, items
    if event.type == EventType.HANDOVER_CASH_REFERENCE_ADJUSTED.value:
        if references.cash is None:
            raise HandoverError("a cash reference moved in a flow that hands over no cash")
        chain = cash if cash is not None else CashCount(references.cash, handover.culprit_id)
        return handover, replace(chain, reference=chain.reference + int(payload["delta"])), items
    if event.type not in _COUNT_EVENTS:
        return handover, cash, items  # notes, recount requests, settlements and discrepancies
    key, counted = _counted_target(event)
    if key is None:
        if references.cash is None:
            raise HandoverError("a cash count in a flow that hands over no cash")
        money = cash if cash is not None else CashCount(references.cash, handover.culprit_id)
        return handover, money.count(counted)[0], items
    if key not in references.items:
        raise HandoverError("the log counts an item this handover never froze a reference for")
    chain = items.get(key) or ItemCount(references.items[key], handover.culprit_id)
    items[key] = chain.count(counted)[0]
    return handover, cash, items


def _counted_target(event: EventRecord) -> tuple[tuple[str, str] | None, int]:
    """Which chain one count event belongs to, and the figure it recorded."""
    payload = event.payload
    if event.type == EventType.HANDOVER_CASH_COUNTED.value:
        return None, int(payload["counted"])
    if event.type == _JOINT_EVENT and payload["scope"] == _CASH:
        return None, int(payload["counted"])
    return (str(payload["item_id"]), str(payload["location_id"])), int(payload["counted"])


def _enter_step(handover: Handover, entered: str) -> Handover:
    """Move to the next step the log names; a skip, a repeat or an unknown step breaks the log."""
    try:
        target = HandoverStep(entered)
    except ValueError as exc:
        raise HandoverError(f"unknown step in the handover log: {entered!r}") from exc
    steps = FLOW_STEPS[handover.flow]
    following = steps[handover.position + 1] if handover.position + 1 < len(steps) else None
    if handover.closed or handover.step is None or following != target:
        raise HandoverError("the handover log skips or repeats a step")
    return handover.advance()
