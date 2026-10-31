"""The manager's daily card: the document it holds and the read model behind it (S01 §6, SHF-6.1).

A card is a *derived document*, not a record of the clinic: every section is read from the event log,
the handover flows and the audit trail, and regenerating a day rewrites its one row while the log
itself stays append-only (R-03). `build` only reads, `regenerate` stores the document and audits it,
and `generate_due` is what the scheduler calls — so a restart, or a server that was off at 07:30,
still produces every card it missed and leaves no silent gap (S01 §6, ADR-018).

What "that day's work" means here: a flow belongs to the covered day of its own `opened_at` (Tehran
local), while events and audit rows belong to the card's window instants — `CardWindow.start`
inclusive, `CardWindow.end` exclusive. An open flow is listed whatever day it was opened on, because
a handover nobody signed is exactly what the card exists to show.

Nothing here reads a reference number. The card is a manager's page, and R-02/ADR-011 keep the frozen
references in the audit trail alone: `handover.reference.<id>` is never queried and never serialised,
and the only figures a section carries are a discrepancy's gap, a flow's cash exception total, or an
amount a shift event itself recorded — never a blind-count reference.

The two shift-event sections (SHF-3) are laid over the same log: `shift_events` is every registered
shift event of the window, newest first, and `open_events` is whatever is still waiting — an open
fault or an undecided approval — whatever day it was opened on, oldest first. A decision event is
never a row of its own; it only decides the status of the event it references.

The two money sections of SHF-5 read the same log one more time (S03 §6, schema 5): `rewards` is what
the day's rewards became — confirmed, or taken back with the month its deduction lands in — plus every
record the manager still has to decide, whatever shift recorded it, and `campaign_discounts` is the
«گزارش کمپین» of the window, one row per campaign that gave a discount in it. Neither section accrues
anything: a reward record exists only because the reward service already wrote it, so building a card
stays read-only.

The five purchasing sections of SHF-4 (schema 6) are folded from the same log: open orders and flagged
parcels for the manager, parcels in transit, the main store's weekly count and the items under their
minimum.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import (
    audit_repo,
    campaign_repo,
    card_repo,
    catalog_repo,
    events_repo,
    handover_repo,
    projections_repo,
    reward_repo,
    staff_repo,
)
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, parse_iso, tehran_iso, to_tehran, to_utc, utc_iso
from shift.common.hashing import canonical_json
from shift.domain.daily_card import CARD_SCHEMA_VERSION, CardWindow, due_card_days, window_for
from shift.domain.events import EventType
from shift.domain.occurred_time import HELD_MESSAGES
from shift.domain.policy import SettingKey
from shift.domain.purchase import UNDECIDED, order_states
from shift.domain.purchase_deliveries import delivery_states
from shift.domain.rewards import WORK_TYPE_LABELS, deduction_month
from shift.domain.shift_clock import covering
from shift.domain.shift_events import (
    APPROVED,
    DECISION_TYPES,
    FieldKind,
    KindSpec,
    is_decision,
    kind_for,
    summary_for,
)
from shift.domain.stock_checks import weekly_status, weekly_window
from shift.domain.store_payloads import MAIN_STORE
from shift.services.audit import AuditEntry, AuditService
from shift.services.daily_card_extras import (
    CallSampleLine,
    EventCountLine,
    OverdueFaultLine,
    call_sample,
    event_counts,
    flag_event_ids,
    overdue_faults,
)
from shift.services.handover import EXCEPTION_ACTION
from shift.services.rewards import CONFIRMED, PENDING, REVOKED
from shift.services.settings import SettingsService
from shift.services.shift_events import waits_for_decision

# The background runtime produces the cards, exactly as it produces the snapshots; runtime.RUNTIME_ACTOR
# is the same name, and the card service does not import the asyncio runtime for one string.
ACTOR = "shift-runtime"
UNNAMED = "نامشخص"
GENERATED_ACTION = "daily_card.generated"
# A settled gap's direction: the event log keeps the sign, the card keeps the meaning beside the size.
SHORTAGE = "shortage"
SURPLUS = "surplus"
_EMERGENCY = EventType.HANDOVER_EMERGENCY_START_CONFIRMED.value
_DISCREPANCY = EventType.DISCREPANCY_RECORDED.value
_FAULT_REPORTED = EventType.EQUIPMENT_FAULT_REPORTED.value
_FAULT_ASSIGNED = EventType.EQUIPMENT_FAULT_ASSIGNED.value
_FAULT_FIXED = EventType.EQUIPMENT_FAULT_FIXED.value
_SECONDS_PER_HOUR = 3600
# F3/F4, F1 and F5: the money and offline records the manager has to see first.
_WITHDRAWN = EventType.CASH_WITHDRAWN.value
_LATE_CASH = EventType.HANDOVER_LATE_CASH_RECORDED.value
_HELD = EventType.EVENT_HELD.value
_RELEASED = EventType.EVENT_RELEASED.value
_HOLD_REJECTED = EventType.EVENT_HOLD_REJECTED.value
# SHF-5: the reward records the day paid or took back, and the «ت» discounts of its campaigns.
_EARNED_TYPES = (reward_repo.REWARD_EARNED, reward_repo.REWARD_CONFIRMED)
_DISCOUNT = EventType.CAMPAIGN_DISCOUNT_APPLIED.value

# The status a shift-event row carries on the card; `open_events` keeps the three that still wait.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_REPORTED = "reported"
STATUS_ASSIGNED = "assigned"
STATUS_FIXED = "fixed"
STATUS_RECORDED = "recorded"
OPEN_STATUSES = frozenset({STATUS_PENDING, STATUS_REPORTED, STATUS_ASSIGNED})

# Every section a card of this code writes, in the document's own order — which is the order the page
# reads them in. A stored card that predates a section simply does not carry it, and the page then does
# not render it (schema 2 has five, schema 3 has nine, schema 4 has ten, schema 5 has twelve).
SECTION_NAMES = (
    "emergency_starts",
    "over_cap_withdrawals",
    "cash_exceptions",
    "discrepancies",
    "flows",
    "open_flows",
    "shift_events",
    "open_events",
    "rewards",
    "campaign_discounts",
    "late_cash",
    "held_events",
    # SHF-4: what the purchasing chain waits to decide and what the warehouse still owes (M-19..M-21).
    "purchase_orders",
    "flagged_deliveries",
    "parcels_in_transit",
    "weekly_count",
    "below_minimum",
    # C1: the counts per code, the overdue repairs and the control-call sample (owner document).
    "event_counts",
    "overdue_faults",
    "call_sample",
)
LEGACY_SCHEMA_VERSIONS = frozenset({2, 3, 4, 5, 6})


@dataclass(frozen=True, slots=True)
class Person:
    """A staff id and the name the roster holds now; a person who left keeps the id and no name."""

    id: str
    name: str | None

    def to_payload(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Person":
        name = payload.get("name")
        return cls(str(payload["id"]), None if name is None else str(name))


@dataclass(frozen=True, slots=True)
class EmergencyStart:
    """H-08: who started the handover alone, which manager confirmed it by phone, and when."""

    handover_id: str
    flow: str
    at_tehran: str
    recorded_by: Person
    manager: Person

    def to_payload(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "flow": self.flow,
            "at_tehran": self.at_tehran,
            "recorded_by": self.recorded_by.to_payload(),
            "manager": self.manager.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "EmergencyStart":
        return cls(
            handover_id=str(payload["handover_id"]),
            flow=str(payload["flow"]),
            at_tehran=str(payload["at_tehran"]),
            recorded_by=Person.from_payload(payload["recorded_by"]),
            manager=Person.from_payload(payload["manager"]),
        )


@dataclass(frozen=True, slots=True)
class CashException:
    """A receipt stamped inside a flow's own window; `amount` is the receipts total, never a reference."""

    handover_id: str
    flow: str
    at_tehran: str
    amount: int
    # Money that left the box inside the window, as a positive figure: a withdrawal during a
    # handover is accepted and flagged exactly like a receipt, and the card must show it (F1).
    outflow: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "flow": self.flow,
            "at_tehran": self.at_tehran,
            "amount": self.amount,
            "outflow": self.outflow,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CashException":
        return cls(
            handover_id=str(payload["handover_id"]),
            flow=str(payload["flow"]),
            at_tehran=str(payload["at_tehran"]),
            amount=int(payload["amount"]),
            outflow=int(payload.get("outflow", 0)),
        )


@dataclass(frozen=True, slots=True)
class DiscrepancyLine:
    """A settled count's gap from its reference; `culprit` None is the unnamed emergency path (H-08)."""

    handover_id: str
    flow: str
    at_tehran: str
    difference: int
    direction: str
    item_id: str | None
    culprit: Person | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "flow": self.flow,
            "at_tehran": self.at_tehran,
            "difference": self.difference,
            "direction": self.direction,
            "item_id": self.item_id,
            "culprit": None if self.culprit is None else self.culprit.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DiscrepancyLine":
        item_id = payload.get("item_id")
        culprit = payload.get("culprit")
        return cls(
            handover_id=str(payload["handover_id"]),
            flow=str(payload["flow"]),
            at_tehran=str(payload["at_tehran"]),
            difference=int(payload["difference"]),
            direction=str(payload["direction"]),
            item_id=None if item_id is None else str(item_id),
            culprit=None if culprit is None else Person.from_payload(culprit),
        )


@dataclass(frozen=True, slots=True)
class FlowLine:
    """One handover flow of the covered day, with the two people the log names and its two flags."""

    handover_id: str
    flow: str
    shift: str
    opened_at_tehran: str
    closed: bool
    emergency: bool
    early_left: bool
    outgoing: Person
    incoming: Person

    def to_payload(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "flow": self.flow,
            "shift": self.shift,
            "opened_at_tehran": self.opened_at_tehran,
            "closed": self.closed,
            "emergency": self.emergency,
            "early_left": self.early_left,
            "outgoing": self.outgoing.to_payload(),
            "incoming": self.incoming.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "FlowLine":
        return cls(
            handover_id=str(payload["handover_id"]),
            flow=str(payload["flow"]),
            shift=str(payload["shift"]),
            opened_at_tehran=str(payload["opened_at_tehran"]),
            closed=bool(payload["closed"]),
            emergency=bool(payload["emergency"]),
            early_left=bool(payload["early_left"]),
            outgoing=Person.from_payload(payload["outgoing"]),
            incoming=Person.from_payload(payload["incoming"]),
        )


@dataclass(frozen=True, slots=True)
class OpenFlow:
    """A flow still waiting for its last signature, however long ago it was opened (H-12)."""

    handover_id: str
    flow: str
    shift: str
    opened_at_tehran: str
    age_hours: int
    outgoing: Person
    incoming: Person

    def to_payload(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "flow": self.flow,
            "shift": self.shift,
            "opened_at_tehran": self.opened_at_tehran,
            "age_hours": self.age_hours,
            "outgoing": self.outgoing.to_payload(),
            "incoming": self.incoming.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OpenFlow":
        return cls(
            handover_id=str(payload["handover_id"]),
            flow=str(payload["flow"]),
            shift=str(payload["shift"]),
            opened_at_tehran=str(payload["opened_at_tehran"]),
            age_hours=int(payload["age_hours"]),
            outgoing=Person.from_payload(payload["outgoing"]),
            incoming=Person.from_payload(payload["incoming"]),
        )


@dataclass(frozen=True, slots=True)
class ShiftEventLine:
    """One recorded shift event: its kind, who recorded it, when, and the status the card reads now.

    `summary` is the kind's own summarizer with the ids replaced by the names the roster and the
    catalog hold now, so the page renders a person and an item instead of an opaque identifier.
    """

    event_type: str
    code: str
    label_fa: str
    at_tehran: str
    actor: Person
    summary: dict[str, Any]
    status: str
    ref_event_id: str | None
    # The record's own id, so a decision button on the card can point at it (C1).
    event_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "code": self.code,
            "label_fa": self.label_fa,
            "at_tehran": self.at_tehran,
            "actor": self.actor.to_payload(),
            "summary": self.summary,
            "status": self.status,
            "ref_event_id": self.ref_event_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ShiftEventLine":
        ref_event_id = payload.get("ref_event_id")
        return cls(
            event_type=str(payload["event_type"]),
            code=str(payload["code"]),
            label_fa=str(payload["label_fa"]),
            at_tehran=str(payload["at_tehran"]),
            actor=Person.from_payload(payload["actor"]),
            summary=dict(payload["summary"]),
            status=str(payload["status"]),
            ref_event_id=None if ref_event_id is None else str(ref_event_id),
            event_id=None if payload.get("event_id") is None else str(payload["event_id"]),
        )


@dataclass(frozen=True, slots=True)
class OpenEvent:
    """A shift event the clinic is still waiting on, and how long it has waited (H-07, SHF-3).

    The row is the same one `shift_events` carries; `age_hours` is measured to the build moment, so a
    fault reported three days ago arrives at the manager as a number, not as a date to subtract.
    """

    row: ShiftEventLine
    age_hours: int

    def to_payload(self) -> dict[str, Any]:
        return {**self.row.to_payload(), "age_hours": self.age_hours}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OpenEvent":
        return cls(ShiftEventLine.from_payload(payload), int(payload["age_hours"]))


@dataclass(frozen=True, slots=True)
class WithdrawalLine:
    """A withdrawal above the cap: the owner wants every one of them impossible to miss.

    The wizard asks for the manager's name only above the cap and refuses a manager name at or below
    it, so a stored `manager_id` is the record's own proof that the cap was passed when it happened.
    """

    event_id: str
    at_tehran: str
    amount: int
    reason: str
    recipient: Person
    manager: Person
    # Whether the manager has decided it: a button is drawn only while it is pending (C1, ADR-019).
    status: str = "pending"

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "event_id": self.event_id,
            "at_tehran": self.at_tehran,
            "amount": self.amount,
            "reason": self.reason,
            "recipient": self.recipient.to_payload(),
            "manager": self.manager.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WithdrawalLine":
        return cls(
            event_id=str(payload["event_id"]),
            at_tehran=str(payload["at_tehran"]),
            amount=int(payload["amount"]),
            reason=str(payload["reason"]),
            recipient=Person.from_payload(payload["recipient"]),
            manager=Person.from_payload(payload["manager"]),
            status=str(payload.get("status", "pending")),
        )


@dataclass(frozen=True, slots=True)
class LateCashLine:
    """A cash record that happened before a flow's cutoff but arrived after it settled (H-11)."""

    event_id: str
    handover_id: str | None
    amount: int
    occurred_at_tehran: str
    received_at_tehran: str
    actor: Person
    explains_discrepancy: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "handover_id": self.handover_id,
            "amount": self.amount,
            "occurred_at_tehran": self.occurred_at_tehran,
            "received_at_tehran": self.received_at_tehran,
            "actor": self.actor.to_payload(),
            "explains_discrepancy": self.explains_discrepancy,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "LateCashLine":
        return cls(
            event_id=str(payload["event_id"]),
            handover_id=None if payload.get("handover_id") is None else str(payload["handover_id"]),
            amount=int(payload["amount"]),
            occurred_at_tehran=str(payload["occurred_at_tehran"]),
            received_at_tehran=str(payload["received_at_tehran"]),
            actor=Person.from_payload(payload["actor"]),
            explains_discrepancy=bool(payload["explains_discrepancy"]),
        )


@dataclass(frozen=True, slots=True)
class HeldLine:
    """A record the server kept instead of applying: the manager releases it or keeps it out (F5)."""

    held_event_id: str
    event_id: str
    label: str
    reason: str
    message: str
    occurred_at_tehran: str
    received_at_tehran: str
    actor: Person

    def to_payload(self) -> dict[str, Any]:
        return {
            "held_event_id": self.held_event_id,
            "event_id": self.event_id,
            "label": self.label,
            "reason": self.reason,
            "message": self.message,
            "occurred_at_tehran": self.occurred_at_tehran,
            "received_at_tehran": self.received_at_tehran,
            "actor": self.actor.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "HeldLine":
        return cls(
            held_event_id=str(payload["held_event_id"]),
            event_id=str(payload["event_id"]),
            label=str(payload["label"]),
            reason=str(payload["reason"]),
            message=str(payload["message"]),
            occurred_at_tehran=str(payload["occurred_at_tehran"]),
            received_at_tehran=str(payload["received_at_tehran"]),
            actor=Person.from_payload(payload["actor"]),
        )


@dataclass(frozen=True, slots=True)
class RewardLine:
    """One reward the day paid, took back, or left waiting for the manager (S01 §5, SHF-5).

    `status` is one of the reward service's own three (`pending`, `confirmed`, `revoked`); a revoked
    row carries the deduction as a **negative** amount, exactly as the payroll line does, and the month
    that deduction lands in — the month after the revocation, never the one already paid. A rejected
    record is not a row: it pays nothing, and the refusal is its shift-event row's own status.
    """

    event_id: str
    status: str
    earner: Person
    work_type: str
    label_fa: str
    amount: int
    at_tehran: str
    deduction_month: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "status": self.status,
            "earner": self.earner.to_payload(),
            "work_type": self.work_type,
            "label_fa": self.label_fa,
            "amount": self.amount,
            "at_tehran": self.at_tehran,
            "deduction_month": self.deduction_month,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RewardLine":
        deduction = payload.get("deduction_month")
        return cls(
            event_id=str(payload["event_id"]),
            status=str(payload["status"]),
            earner=Person.from_payload(payload["earner"]),
            work_type=str(payload["work_type"]),
            label_fa=str(payload["label_fa"]),
            amount=int(payload["amount"]),
            at_tehran=str(payload["at_tehran"]),
            deduction_month=None if deduction is None else str(deduction),
        )


@dataclass(frozen=True, slots=True)
class PurchaseOrderLine:
    """An order the storekeeper sent and the manager has not decided yet (P-02, M-19).

    The row carries the lines' names and quantities and never a price: the manager decides what to buy,
    the accountant prices what arrived. It stays on the card whatever shift drafted it.
    """

    order_id: str
    at_tehran: str
    age_hours: int
    actor: Person
    line_count: int
    lines: tuple[str, ...]
    note: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "at_tehran": self.at_tehran,
            "age_hours": self.age_hours,
            "actor": self.actor.to_payload(),
            "line_count": self.line_count,
            "lines": list(self.lines),
            "note": self.note,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PurchaseOrderLine":
        note = payload.get("note")
        return cls(
            order_id=str(payload["order_id"]),
            at_tehran=str(payload["at_tehran"]),
            age_hours=int(payload["age_hours"]),
            actor=Person.from_payload(payload["actor"]),
            line_count=int(payload["line_count"]),
            lines=tuple(str(line) for line in payload.get("lines", ())),
            note=None if note is None else str(note),
        )


@dataclass(frozen=True, slots=True)
class CampaignDiscountLine:
    """One campaign that gave a discount in the window: its name, service, count, amount and total.

    The figures are the campaign report's own — the count of «ت» events in the window times the
    campaign's amount — so the card and the report page never disagree. A campaign the calendar no
    longer holds still counts (it happened) and keeps its id as its name, with no amount and no total:
    the card never invents a price for it.
    """

    campaign_id: str
    name: str
    service: str
    count: int
    amount: int | None
    total: int | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "service": self.service,
            "count": self.count,
            "amount": self.amount,
            "total": self.total,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CampaignDiscountLine":
        amount = payload.get("amount")
        total = payload.get("total")
        return cls(
            campaign_id=str(payload["campaign_id"]),
            name=str(payload["name"]),
            service=str(payload["service"]),
            count=int(payload["count"]),
            amount=None if amount is None else int(amount),
            total=None if total is None else int(total),
        )


@dataclass(frozen=True, slots=True)
class FlaggedDeliveryLine:
    """A parcel received with a problem label whose payment still waits for the manager (P-06, M-19)."""

    delivery_id: str
    invoice_reference: str
    reason_fa: str
    at_tehran: str
    age_hours: int
    actor: Person
    # The flag record the manager's decision points at (C1).
    flag_event_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "flag_event_id": self.flag_event_id,
            "delivery_id": self.delivery_id,
            "invoice_reference": self.invoice_reference,
            "reason_fa": self.reason_fa,
            "at_tehran": self.at_tehran,
            "age_hours": self.age_hours,
            "actor": self.actor.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "FlaggedDeliveryLine":
        return cls(
            delivery_id=str(payload["delivery_id"]),
            invoice_reference=str(payload["invoice_reference"]),
            reason_fa=str(payload["reason_fa"]),
            at_tehran=str(payload["at_tehran"]),
            age_hours=int(payload["age_hours"]),
            actor=Person.from_payload(payload["actor"]),
            flag_event_id=None if payload.get("flag_event_id") is None else str(payload["flag_event_id"]),
        )


@dataclass(frozen=True, slots=True)
class ParcelLine:
    """A received parcel the storekeeper has not taken custody of yet, with its age (P-12, M-20)."""

    delivery_id: str
    invoice_reference: str
    packages: int | None
    at_tehran: str
    age_hours: int
    actor: Person

    def to_payload(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "invoice_reference": self.invoice_reference,
            "packages": self.packages,
            "at_tehran": self.at_tehran,
            "age_hours": self.age_hours,
            "actor": self.actor.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ParcelLine":
        packages = payload.get("packages")
        return cls(
            delivery_id=str(payload["delivery_id"]),
            invoice_reference=str(payload["invoice_reference"]),
            packages=None if packages is None else int(packages),
            at_tehran=str(payload["at_tehran"]),
            age_hours=int(payload["age_hours"]),
            actor=Person.from_payload(payload["actor"]),
        )


@dataclass(frozen=True, slots=True)
class WeeklyCountLine:
    """The main store's count for this week: done, still due, or past its deadline (P-11, M-20)."""

    state: str
    window_from: str
    window_to: str
    last_count_on: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "window_from": self.window_from,
            "window_to": self.window_to,
            "last_count_on": self.last_count_on,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WeeklyCountLine":
        last = payload.get("last_count_on")
        return cls(
            state=str(payload["state"]),
            window_from=str(payload["window_from"]),
            window_to=str(payload["window_to"]),
            last_count_on=None if last is None else str(last),
        )


@dataclass(frozen=True, slots=True)
class BelowMinimumLine:
    """A counted item under its catalog minimum, with the figure it stands at now (P-01, M-21)."""

    item_id: str
    name: str
    location: str
    minimum: int
    quantity: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "location": self.location,
            "minimum": self.minimum,
            "quantity": self.quantity,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "BelowMinimumLine":
        return cls(
            item_id=str(payload["item_id"]),
            name=str(payload["name"]),
            location=str(payload["location"]),
            minimum=int(payload["minimum"]),
            quantity=int(payload["quantity"]),
        )


@dataclass(frozen=True, slots=True)
class Card:
    """One day's manager card: the window it covers, when it was built, and its seventeen sections.

    `present_sections` is not part of the document: it remembers which sections the stored card
    carried, so a schema-2 card — written before the shift-event sections existed — does not grow two
    empty blocks when the page renders it.
    """

    card_date: date
    covered_from: date
    covered_to: date
    generated_at: datetime
    emergency_starts: tuple[EmergencyStart, ...] = ()
    cash_exceptions: tuple[CashException, ...] = ()
    discrepancies: tuple[DiscrepancyLine, ...] = ()
    flows: tuple[FlowLine, ...] = ()
    open_flows: tuple[OpenFlow, ...] = ()
    shift_events: tuple[ShiftEventLine, ...] = ()
    open_events: tuple[OpenEvent, ...] = ()
    over_cap_withdrawals: tuple[WithdrawalLine, ...] = ()
    late_cash: tuple[LateCashLine, ...] = ()
    held_events: tuple[HeldLine, ...] = ()
    rewards: tuple[RewardLine, ...] = ()
    campaign_discounts: tuple[CampaignDiscountLine, ...] = ()
    purchase_orders: tuple[PurchaseOrderLine, ...] = ()
    flagged_deliveries: tuple[FlaggedDeliveryLine, ...] = ()
    parcels_in_transit: tuple[ParcelLine, ...] = ()
    weekly_count: tuple[WeeklyCountLine, ...] = ()
    below_minimum: tuple[BelowMinimumLine, ...] = ()
    event_counts: tuple[EventCountLine, ...] = ()
    overdue_faults: tuple[OverdueFaultLine, ...] = ()
    call_sample: tuple[CallSampleLine, ...] = ()
    present_sections: tuple[str, ...] = SECTION_NAMES

    @property
    def day_count(self) -> int:
        """How many days the card covers: two on Saturday, one otherwise."""
        return (self.covered_to - self.covered_from).days + 1

    def to_payload(self) -> dict[str, Any]:
        """The stored form: a versioned document whose keys are this dataclass's own fields."""
        return {
            "schema": CARD_SCHEMA_VERSION,
            "card_date": self.card_date.isoformat(),
            "covered_from": self.covered_from.isoformat(),
            "covered_to": self.covered_to.isoformat(),
            "generated_at": to_utc(self.generated_at).isoformat(),
            "sections": {
                "emergency_starts": [entry.to_payload() for entry in self.emergency_starts],
                "cash_exceptions": [entry.to_payload() for entry in self.cash_exceptions],
                "discrepancies": [entry.to_payload() for entry in self.discrepancies],
                "flows": [entry.to_payload() for entry in self.flows],
                "open_flows": [entry.to_payload() for entry in self.open_flows],
                "shift_events": [entry.to_payload() for entry in self.shift_events],
                "open_events": [entry.to_payload() for entry in self.open_events],
                "over_cap_withdrawals": [entry.to_payload() for entry in self.over_cap_withdrawals],
                "late_cash": [entry.to_payload() for entry in self.late_cash],
                "held_events": [entry.to_payload() for entry in self.held_events],
                "rewards": [entry.to_payload() for entry in self.rewards],
                "campaign_discounts": [entry.to_payload() for entry in self.campaign_discounts],
                "purchase_orders": [entry.to_payload() for entry in self.purchase_orders],
                "flagged_deliveries": [entry.to_payload() for entry in self.flagged_deliveries],
                "parcels_in_transit": [entry.to_payload() for entry in self.parcels_in_transit],
                "weekly_count": [entry.to_payload() for entry in self.weekly_count],
                "below_minimum": [entry.to_payload() for entry in self.below_minimum],
                "event_counts": [entry.to_payload() for entry in self.event_counts],
                "overdue_faults": [entry.to_payload() for entry in self.overdue_faults],
                "call_sample": [entry.to_payload() for entry in self.call_sample],
            },
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Card":
        """Read a stored card back; schema 2, 3 and 4 (before these sections) are accepted."""
        schema = int(payload["schema"])
        if schema != CARD_SCHEMA_VERSION and schema not in LEGACY_SCHEMA_VERSIONS:
            raise ValueError(f"unknown daily card schema: {schema}")
        sections = payload["sections"]
        return cls(
            card_date=date.fromisoformat(str(payload["card_date"])),
            covered_from=date.fromisoformat(str(payload["covered_from"])),
            covered_to=date.fromisoformat(str(payload["covered_to"])),
            generated_at=parse_iso(str(payload["generated_at"])),
            emergency_starts=tuple(EmergencyStart.from_payload(entry) for entry in sections["emergency_starts"]),
            cash_exceptions=tuple(CashException.from_payload(entry) for entry in sections["cash_exceptions"]),
            discrepancies=tuple(DiscrepancyLine.from_payload(entry) for entry in sections["discrepancies"]),
            flows=tuple(FlowLine.from_payload(entry) for entry in sections["flows"]),
            open_flows=tuple(OpenFlow.from_payload(entry) for entry in sections["open_flows"]),
            shift_events=tuple(ShiftEventLine.from_payload(entry) for entry in sections.get("shift_events", ())),
            open_events=tuple(OpenEvent.from_payload(entry) for entry in sections.get("open_events", ())),
            # The three money-and-offline sections are read back too: the page renders the stored
            # document, so a section this reader forgets is a section the manager never sees.
            over_cap_withdrawals=tuple(
                WithdrawalLine.from_payload(entry) for entry in sections.get("over_cap_withdrawals", ())
            ),
            late_cash=tuple(LateCashLine.from_payload(entry) for entry in sections.get("late_cash", ())),
            held_events=tuple(HeldLine.from_payload(entry) for entry in sections.get("held_events", ())),
            rewards=tuple(RewardLine.from_payload(entry) for entry in sections.get("rewards", ())),
            campaign_discounts=tuple(
                CampaignDiscountLine.from_payload(entry) for entry in sections.get("campaign_discounts", ())
            ),
            purchase_orders=tuple(
                PurchaseOrderLine.from_payload(entry) for entry in sections.get("purchase_orders", ())
            ),
            flagged_deliveries=tuple(
                FlaggedDeliveryLine.from_payload(entry) for entry in sections.get("flagged_deliveries", ())
            ),
            parcels_in_transit=tuple(
                ParcelLine.from_payload(entry) for entry in sections.get("parcels_in_transit", ())
            ),
            weekly_count=tuple(WeeklyCountLine.from_payload(entry) for entry in sections.get("weekly_count", ())),
            below_minimum=tuple(BelowMinimumLine.from_payload(entry) for entry in sections.get("below_minimum", ())),
            event_counts=tuple(EventCountLine.from_payload(entry) for entry in sections.get("event_counts", ())),
            overdue_faults=tuple(OverdueFaultLine.from_payload(entry) for entry in sections.get("overdue_faults", ())),
            call_sample=tuple(CallSampleLine.from_payload(entry) for entry in sections.get("call_sample", ())),
            present_sections=tuple(name for name in SECTION_NAMES if name in sections),
        )


class DailyCardService:
    """Builds, stores and reads the manager's daily card (S01 §6, SHF-6.1)."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Clock | None = None,
        audit: AuditService | None = None,
        settings: SettingsService | None = None,
    ) -> None:
        self._database = database
        self._clock = clock or SystemClock()
        self._audit = audit or AuditService(database, clock=self._clock)
        # The warehouse count's deadline is a versioned setting (R-09). The card reads it through the
        # same service the manager's settings page writes, built here when the app did not hand one in.
        self._settings_service = settings

    def _settings(self) -> SettingsService:
        """The settings reader this card uses; built once on first use (R-09)."""
        if self._settings_service is None:
            self._settings_service = SettingsService(self._database, clock=self._clock)
        return self._settings_service

    def build(self, run_day: date) -> Card | None:
        """The card for `run_day` as the log, the flows and the rosters describe it now; None on Friday."""
        window = window_for(run_day)
        if window is None:
            return None
        moment = self._clock.now_utc()
        with self._database.read() as connection:
            names = _Names(connection)
            catalog = _CatalogNames(connection)
            index = _EventIndex(connection)
            shift_event_rows = _shift_events(connection, window, names, catalog, index)
            return Card(
                card_date=window.card_date,
                covered_from=window.covered_from,
                covered_to=window.covered_to,
                generated_at=moment,
                emergency_starts=_emergency_starts(connection, window, names),
                cash_exceptions=_cash_exceptions(connection, window),
                discrepancies=_discrepancies(connection, window, names),
                flows=_flows(connection, window, names),
                open_flows=_open_flows(connection, moment, names),
                shift_events=shift_event_rows,
                open_events=_open_events(moment, names, catalog, index),
                over_cap_withdrawals=_over_cap_withdrawals(connection, window, names, index),
                late_cash=_late_cash(connection, window, names),
                held_events=_held_events(connection, window, names),
                rewards=_rewards(connection, window, names),
                campaign_discounts=_campaign_discounts(connection, window),
                purchase_orders=_purchase_orders(connection, moment, names, catalog),
                flagged_deliveries=_flagged_deliveries(connection, moment, names, index),
                parcels_in_transit=_parcels_in_transit(connection, moment, names),
                weekly_count=(_weekly_count(connection, moment, self._settings()),),
                below_minimum=_below_minimum(connection, moment, catalog),
                event_counts=event_counts(shift_event_rows),
                overdue_faults=overdue_faults(
                    index.events, moment, lambda staff_id: names.person(staff_id).name or UNNAMED
                ),
                call_sample=call_sample(connection, window),
            )

    def regenerate(self, run_day: date) -> Card | None:
        """Build the card, rewrite the one stored document for its date, then audit it once."""
        card = self.build(run_day)
        if card is None:
            return None
        with self._database.write() as connection:
            card_repo.upsert(
                connection,
                {
                    "card_date": card.card_date.isoformat(),
                    "covered_from": card.covered_from.isoformat(),
                    "covered_to": card.covered_to.isoformat(),
                    "schema_version": CARD_SCHEMA_VERSION,
                    "generated_at_utc": utc_iso(card.generated_at),
                    "generated_at_tehran": tehran_iso(card.generated_at),
                    "payload": canonical_json(card.to_payload()),
                },
            )
        self._record(card)
        return card

    def generate_due(self, now: datetime) -> tuple[Card, ...]:
        """Store every card a run at `now` still owes, oldest first; Friday owes none (ADR-018)."""
        moment = to_tehran(now)
        with self._database.read() as connection:
            newest = card_repo.newest_date(connection)
        cards = (self.regenerate(day) for day in due_card_days(moment.date(), newest=newest))
        return tuple(card for card in cards if card is not None)

    def latest(self) -> Card | None:
        """The newest stored card."""
        with self._database.read() as connection:
            return _card(card_repo.latest(connection))

    def history(self, *, limit: int = 50) -> list[Card]:
        """The stored cards, newest first."""
        with self._database.read() as connection:
            return [Card.from_payload(row.payload) for row in card_repo.history(connection, limit=limit)]

    def get(self, card_date: date) -> Card | None:
        """The stored card of one day."""
        with self._database.read() as connection:
            return _card(card_repo.get(connection, card_date))

    def _record(self, card: Card) -> None:
        """One internal audit row per generated card: the window it covers, never a count or reference."""
        self._audit.record(
            AuditEntry(
                actor_id=ACTOR,
                action=GENERATED_ACTION,
                subject_id=card.card_date.isoformat(),
                detail={
                    "card_date": card.card_date.isoformat(),
                    "covered_from": card.covered_from.isoformat(),
                    "covered_to": card.covered_to.isoformat(),
                    "days": card.day_count,
                },
            )
        )


class _Names:
    """Staff names for one build; a person who left the roster keeps their id and no name."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._read: dict[str, str | None] = {}

    def person(self, staff_id: str) -> Person:
        """The id with the name the roster holds now, looked up once per build."""
        if staff_id not in self._read:
            record = staff_repo.find(self._connection, staff_id)
            self._read[staff_id] = None if record is None else record.full_name
        return Person(staff_id, self._read[staff_id])


def _card(row: card_repo.DailyCardRow | None) -> Card | None:
    """A stored document read back as a card, or None when the day was never generated."""
    return None if row is None else Card.from_payload(row.payload)


def _window_events(connection: Connection, window: CardWindow, event_type: str) -> list[EventRecord]:
    """The events of one type whose server stamp falls in the card's half-open window."""
    span = events_repo.iter_between(connection, after=utc_iso(window.start), until=utc_iso(window.end))
    return [event for event in span if event.type == event_type]


def _flow_of(connection: Connection, handover_id: str | None) -> str:
    """The kind of flow an event belongs to; a flow's row is written before any of its events.

    An empty name is honest for a log whose flow row is missing, and keeps one odd event from
    stopping the manager's morning card.
    """
    if handover_id is None:
        return ""
    row = handover_repo.get(connection, handover_id)
    return "" if row is None else row.flow.value


def _emergency_starts(connection: Connection, window: CardWindow, names: _Names) -> tuple[EmergencyStart, ...]:
    """H-08: every emergency start of the window, newest first — who, which manager, when."""
    events = _window_events(connection, window, _EMERGENCY)
    return tuple(
        EmergencyStart(
            handover_id=str(event.handover_id or ""),
            flow=_flow_of(connection, event.handover_id),
            at_tehran=event.at_tehran,
            recorded_by=names.person(event.actor_id),
            manager=names.person(str(event.payload["manager_id"])),
        )
        for event in reversed(events)
    )


def _cash_exceptions(connection: Connection, window: CardWindow) -> tuple[CashException, ...]:
    """Receipts stamped inside a flow's own window, newest first; the amount, never a reference."""
    rows = audit_repo.within(
        connection, after=utc_iso(window.start), until=utc_iso(window.end), action=EXCEPTION_ACTION
    )
    return tuple(
        CashException(
            handover_id=str(row.subject_id or ""),
            flow=str(row.detail.get("flow", "")),
            at_tehran=row.at_tehran,
            amount=int(row.detail.get("amount", 0)),
            outflow=int(row.detail.get("outflow", 0)),
        )
        for row in rows
    )


def _discrepancies(connection: Connection, window: CardWindow, names: _Names) -> tuple[DiscrepancyLine, ...]:
    """Every recorded gap of the window, newest first, with its size and which way it went.

    `payload["difference"]` is signed (positive a shortage, negative a surplus) because the log keeps
    the direction; the card keeps the size as a positive figure and states the direction beside it, so
    a manager reading «۹۱٬۱۱۱» also reads whether the clinic was short or over, never what it was
    compared against.
    """
    events = _window_events(connection, window, _DISCREPANCY)
    return tuple(
        DiscrepancyLine(
            handover_id=str(event.handover_id or ""),
            flow=_flow_of(connection, event.handover_id),
            at_tehran=event.at_tehran,
            difference=abs(int(event.payload["difference"])),
            direction=SURPLUS if int(event.payload["difference"]) < 0 else SHORTAGE,
            item_id=None if event.payload.get("item_id") is None else str(event.payload["item_id"]),
            culprit=None if event.payload.get("culprit_id") is None else names.person(str(event.payload["culprit_id"])),
        )
        for event in reversed(events)
    )


def _flows(connection: Connection, window: CardWindow, names: _Names) -> tuple[FlowLine, ...]:
    """The flows whose own cutoff falls on the covered day, oldest first."""
    return tuple(
        FlowLine(
            handover_id=row.handover_id,
            flow=row.flow.value,
            shift=row.shift_id,
            opened_at_tehran=tehran_iso(row.opened_at),
            closed=row.signed_at is not None,
            emergency=row.emergency_by is not None,
            early_left=row.early_left,
            outgoing=names.person(row.outgoing_id),
            incoming=names.person(row.incoming_id),
        )
        for row in handover_repo.flows_opened_between(connection, after=window.start, until=window.end)
    )


def _open_flows(connection: Connection, moment: datetime, names: _Names) -> tuple[OpenFlow, ...]:
    """Every flow still waiting for a signature, oldest first so the forgotten one comes first."""
    return tuple(
        OpenFlow(
            handover_id=row.handover_id,
            flow=row.flow.value,
            shift=row.shift_id,
            opened_at_tehran=tehran_iso(row.opened_at),
            age_hours=_age_hours(moment, row.opened_at),
            outgoing=names.person(row.outgoing_id),
            incoming=names.person(row.incoming_id),
        )
        for row in handover_repo.open_flows(connection)
    )


def _age_hours(moment: datetime, opened_at: datetime) -> int:
    """Whole hours a flow has waited for its signature; floor, so 19h 59m reads 19."""
    return int((to_utc(moment) - to_utc(opened_at)).total_seconds() // _SECONDS_PER_HOUR)


class _CatalogNames:
    """The newest catalog name of an item or a location, looked up once per build.

    A row stored before the catalog kept that id names the id itself: the card must still report the
    event, and an honest identifier is better than a guess at a name nobody wrote down.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._items: dict[str, str] = {}
        self._locations: dict[str, str] = {}

    def item(self, item_id: str) -> str:
        if item_id not in self._items:
            row = catalog_repo.find_item_latest(self._connection, item_id)
            self._items[item_id] = item_id if row is None else row.name
        return self._items[item_id]

    def location(self, location_id: str) -> str:
        if location_id not in self._locations:
            row = catalog_repo.find_location_latest(self._connection, location_id)
            self._locations[location_id] = location_id if row is None else row.name
        return self._locations[location_id]


class _EventIndex:
    """Every event read once per build, with the references that decide a shift event's status.

    A decision or a fault closing is itself an event; reading them into maps keeps the status of a row
    a lookup instead of one query per row. Later events overwrite earlier ones, so a second decision
    (the service refuses one, but the log is the authority) would be the one the card reads.
    """

    def __init__(self, connection: Connection) -> None:
        self.events = tuple(events_repo.iter_events(connection))
        self._decisions: dict[str, str] = {}
        self._assigned: set[str] = set()
        self._fixed: set[str] = set()
        for event in self.events:
            if event.ref_event_id is None:
                continue
            if event.type in DECISION_TYPES:
                self._decisions[event.ref_event_id] = event.type
            elif event.type == _FAULT_ASSIGNED:
                self._assigned.add(event.ref_event_id)
            elif event.type == _FAULT_FIXED:
                self._fixed.add(event.ref_event_id)

    def status(self, event: EventRecord, kind: KindSpec) -> str:
        """The one status the card reads for a row: a decision, the fault chain, or just recorded."""
        if kind.requires_approval:
            decision = self._decisions.get(event.event_id)
            if decision is None:
                # A record stored before the clinic asked for a second person has no decider at all:
                # it is finished, not an open item nobody could ever close.
                return STATUS_PENDING if waits_for_decision(kind, event) else STATUS_RECORDED
            return STATUS_APPROVED if decision == APPROVED else STATUS_REJECTED
        if event.type == _FAULT_REPORTED:
            if event.event_id in self._fixed:
                return STATUS_FIXED
            if event.event_id in self._assigned:
                return STATUS_ASSIGNED
            return STATUS_REPORTED
        return STATUS_RECORDED


def _registered_kind(event: EventRecord) -> KindSpec | None:
    """The kind that owns a row, or None for a handover event and for a decision (never its own row)."""
    kind = kind_for(event.type)
    if kind is None or is_decision(event.type):
        return None
    return kind


def _shift_events(
    connection: Connection, window: CardWindow, names: _Names, catalog: _CatalogNames, index: _EventIndex
) -> tuple[ShiftEventLine, ...]:
    """Every registered shift event of the window, newest first, with its current status."""
    span = events_repo.iter_between(connection, after=utc_iso(window.start), until=utc_iso(window.end))
    rows: list[ShiftEventLine] = []
    for event in reversed(list(span)):
        kind = _registered_kind(event)
        if kind is not None:
            rows.append(_shift_event_line(event, kind, names, catalog, index))
    return tuple(rows)


def _open_events(moment: datetime, names: _Names, catalog: _CatalogNames, index: _EventIndex) -> tuple[OpenEvent, ...]:
    """Everything still waiting — an open fault, an undecided approval — whatever day it opened on.

    Oldest first, because a fault nobody fixed for three days is exactly what the manager must see
    before the one recorded this morning. A rejected, approved, fixed or merely recorded event is not
    waiting on anybody and stays out.
    """
    rows: list[OpenEvent] = []
    for event in index.events:
        kind = _registered_kind(event)
        if kind is None or index.status(event, kind) not in OPEN_STATUSES:
            continue
        line = _shift_event_line(event, kind, names, catalog, index)
        rows.append(OpenEvent(line, _age_hours(moment, parse_iso(event.server_at))))
    return tuple(rows)


def _shift_event_line(
    event: EventRecord, kind: KindSpec, names: _Names, catalog: _CatalogNames, index: _EventIndex
) -> ShiftEventLine:
    """One row: the kind's vocabulary, the person the roster names, and its own resolved summary."""
    return ShiftEventLine(
        event_type=event.type,
        code=kind.code,
        label_fa=kind.label_fa,
        at_tehran=event.at_tehran,
        actor=names.person(event.actor_id),
        summary=_summary(kind, event.payload, names, catalog),
        status=index.status(event, kind),
        ref_event_id=event.ref_event_id,
        event_id=event.event_id,
    )


def _summary(kind: KindSpec, payload: Mapping[str, Any], names: _Names, catalog: _CatalogNames) -> dict[str, Any]:
    """The kind's own summary with each id replaced by the name the clinic's lists hold now.

    Which value is a person, an item or a place is the kind's own `fields`, the same metadata the
    phone's wizard reads; an amount, a quantity, a note or a choice stays exactly as recorded.
    """
    fields = {field.key: field.kind for field in kind.fields}
    return {
        key: _summary_value(fields.get(key), value, names, catalog) for key, value in summary_for(kind, payload).items()
    }


def _summary_value(field_kind: FieldKind | None, value: Any, names: _Names, catalog: _CatalogNames) -> Any:
    """One summarized value; a key with no field spec is the kind's own already-usable text."""
    if value is None or field_kind is None:
        return value
    if field_kind is FieldKind.STAFF:
        return names.person(str(value)).name or str(value)
    if field_kind is FieldKind.ITEM:
        return catalog.item(str(value))
    if field_kind is FieldKind.LOCATION:
        return catalog.location(str(value))
    if field_kind is FieldKind.MULTI:
        # The clinic's only multi field is the cleaning checklist, which ticks catalog items.
        return [_summary_value(FieldKind.ITEM, item, names, catalog) for item in value]
    return value


def _over_cap_withdrawals(
    connection: Connection, window: CardWindow, names: _Names, index: _EventIndex
) -> tuple[WithdrawalLine, ...]:
    """Every withdrawal that named a manager: only an above-cap one asks for that name (F3/F4).

    The wizard asks for the manager's name above the cap and refuses it at or below the cap, so the
    stored name is the record's own proof of which side of the cap it was on, whatever the setting says
    today. The owner wants them impossible to miss, next to the emergency starts.
    """
    rows: list[WithdrawalLine] = []
    for event in _window_events(connection, window, _WITHDRAWN):
        manager_id = event.payload.get("manager_id")
        if not manager_id:
            continue
        rows.append(
            WithdrawalLine(
                event_id=event.event_id,
                at_tehran=event.at_tehran,
                amount=int(event.payload["amount"]),
                reason=str(event.payload.get("reason") or ""),
                recipient=names.person(str(event.payload.get("recipient_id") or "")),
                manager=names.person(str(manager_id)),
                status=_decision_status(index, event),
            )
        )
    return tuple(rows)


def _decision_status(index: _EventIndex, event: EventRecord) -> str:
    """`pending`, `approved` or `rejected` for a record that asks the manager (C1)."""
    kind = _registered_kind(event)
    return STATUS_PENDING if kind is None else index.status(event, kind)


def _late_cash(connection: Connection, window: CardWindow, names: _Names) -> tuple[LateCashLine, ...]:
    """A cash record that happened before a flow's cutoff but reached the server after it settled."""
    rows: list[LateCashLine] = []
    for event in _window_events(connection, window, _LATE_CASH):
        handover_id = event.payload.get("handover_id")
        rows.append(
            LateCashLine(
                event_id=event.event_id,
                handover_id=None if handover_id is None else str(handover_id),
                amount=int(event.payload["amount"]),
                occurred_at_tehran=tehran_iso(parse_iso(str(event.payload["occurred_at"]))),
                received_at_tehran=event.at_tehran,
                actor=names.person(event.actor_id),
                explains_discrepancy=(
                    False if handover_id is None else _flow_has_cash_discrepancy(connection, str(handover_id))
                ),
            )
        )
    return tuple(rows)


def _flow_has_cash_discrepancy(connection: Connection, handover_id: str) -> bool:
    """Whether a flow settled its box on a gap — the gap a late record may explain (H-11)."""
    rows = connection.exec_driver_sql(
        "SELECT payload FROM events WHERE handover_id = ? AND type = ?", (handover_id, _DISCREPANCY)
    ).mappings()
    return any(json.loads(row["payload"]).get("scope") == "cash" for row in rows)


def _held_events(connection: Connection, window: CardWindow, names: _Names) -> tuple[HeldLine, ...]:
    """Every held record of the window no manager has released or rejected yet (F5)."""
    rows: list[HeldLine] = []
    for event in _window_events(connection, window, _HELD):
        if events_repo.count_ref(connection, event.event_id, (_RELEASED, _HOLD_REJECTED)):
            continue
        payload = event.payload
        kind = kind_for(str(payload.get("type") or ""))
        rows.append(
            HeldLine(
                held_event_id=event.event_id,
                event_id=str(payload["event_id"]),
                label=kind.label_fa if kind is not None else "رویداد ناشناس",
                reason=str(payload["reason"]),
                message=HELD_MESSAGES.get(str(payload["reason"]), HELD_MESSAGES["stale"]),
                occurred_at_tehran=tehran_iso(parse_iso(str(payload["occurred_at"]))),
                received_at_tehran=event.at_tehran,
                actor=names.person(event.actor_id),
            )
        )
    return tuple(rows)


def _rewards(connection: Connection, window: CardWindow, names: _Names) -> tuple[RewardLine, ...]:
    """What the day's rewards became, then every record still waiting for the manager (S01 §5).

    A record belongs to the day it became what it is: the confirmation or the revocation that moved
    it, and its own instant while nobody has decided yet. Those are two days, so a reward confirmed
    and revoked later keeps its confirmation row on the day it was paid and puts the deduction on the
    day it was taken back — the card counts the same facts the ledger pays and deducts, and a revoked
    reward never removes a row from a card that already reported it. A pending record is on every card
    until the manager decides it — whatever shift recorded it, the `open_events` rule — and a rejected
    one is not a row at all: it pays nothing, and the refusal is already the status of its own
    shift-event row.

    This reads the log and nothing else: `build` never accrues, so a reward only reaches a card because
    the reward service already wrote it, at the rate of the month the work happened in.
    """
    timeline = reward_repo.timeline(connection)
    decisions = {row.ref_event_id: row for row in timeline if row.type in DECISION_TYPES}
    revocations = {row.ref_event_id: row for row in timeline if row.type == reward_repo.REWARD_REVOKED}
    rows: list[RewardLine] = []
    for row in timeline:
        if row.type not in _EARNED_TYPES:
            continue
        decision = decisions.get(row.event_id)
        if row.type == reward_repo.REWARD_CONFIRMED or (decision is not None and decision.type == APPROVED):
            # The manager's own credit for an assigned task carries its confirmation; an earned record
            # is paid in the month its approval landed in, so the row is that day's decision.
            at = row.at_tehran if decision is None else decision.at_tehran
            rows.append(_reward_row(row, CONFIRMED, at, names))
        elif decision is None:
            rows.append(_reward_row(row, PENDING, row.at_tehran, names))
        # The revocation is a fact of its own day, so it is read beside the confirmation, never instead.
        revocation = revocations.get(row.event_id)
        if revocation is not None:
            rows.append(_reward_row(row, REVOKED, revocation.at_tehran, names, revoked=True))
    kept = [row for row in rows if row.status == PENDING or _inside(window, row.at_tehran)]
    return tuple(sorted(kept, key=lambda row: (row.at_tehran, row.event_id)))


def _reward_row(
    row: reward_repo.RewardEventRow,
    status: str,
    at_tehran: str,
    names: _Names,
    *,
    revoked: bool = False,
) -> RewardLine:
    """One stored reward record as a card row: its earner, its work in Persian and its amount."""
    payload = row.payload
    work_type = str(payload.get("work_type") or "")
    amount = int(payload.get("amount_toman") or 0)
    return RewardLine(
        event_id=row.event_id,
        status=status,
        earner=names.person(str(payload.get("staff_id") or "")),
        work_type=work_type,
        label_fa=WORK_TYPE_LABELS.get(work_type, work_type),
        # A revocation gives money back, so it reads negative, exactly like the payroll's own line.
        amount=-amount if revoked else amount,
        at_tehran=at_tehran,
        deduction_month=deduction_month(parse_iso(at_tehran)) if revoked else None,
    )


def _campaign_discounts(connection: Connection, window: CardWindow) -> tuple[CampaignDiscountLine, ...]:
    """The «گزارش کمپین» of the window: one row per campaign that gave a discount inside it (F8).

    The figures are the report page's own — the count of «ت» events of the window times the campaign's
    amount — and a campaign with none in the window is not a row at all: the section's empty case is
    the muted line every section already gets.
    """
    counts: dict[str, int] = {}
    for event in _window_events(connection, window, _DISCOUNT):
        campaign_id = str(event.payload.get("campaign_id") or "")
        counts[campaign_id] = counts.get(campaign_id, 0) + 1
    if not counts:
        return ()
    rows: list[CampaignDiscountLine] = []
    for campaign in campaign_repo.newest_first(connection):
        count = counts.pop(campaign.campaign_id, None)
        if count is None:
            continue
        rows.append(
            CampaignDiscountLine(
                campaign_id=campaign.campaign_id,
                name=campaign.name,
                service=campaign.service,
                count=count,
                amount=campaign.amount_toman,
                total=count * campaign.amount_toman,
            )
        )
    # A discount whose campaign has left the calendar still happened, and the report page keeps it
    # too: it takes a row of its own, by its id, and neither page invents an amount for it.
    rows.extend(
        CampaignDiscountLine(campaign_id, campaign_id, "", count, None, None)
        for campaign_id, count in sorted(counts.items())
    )
    return tuple(rows)


def _inside(window: CardWindow, at_tehran: str) -> bool:
    """Whether a stored Tehran instant falls in the card's half-open window."""
    return window.start <= parse_iso(at_tehran) < window.end


# --- the purchasing chain and the warehouse (SHF-4, S01 §4, M-19..M-21) ----------------------

_REASON_FA = {"short": "کم", "extra": "زیاد", "wrong": "اشتباه", "broken": "شکسته"}
_STORE_CUSTODY = "storekeeper"


def _purchase_orders(
    connection: Connection, moment: datetime, names: _Names, catalog: _CatalogNames
) -> tuple[PurchaseOrderLine, ...]:
    """Every order still waiting for the manager, whatever shift drafted it (P-02, M-19).

    The lines carry the item's name and the quantity the storekeeper asked for; a price is never part
    of an order, because the accountant prices what actually arrived.
    """
    rows: list[PurchaseOrderLine] = []
    for order in order_states(events_repo.iter_events(connection)).values():
        if order.status not in UNDECIDED:
            continue
        rows.append(
            PurchaseOrderLine(
                order_id=order.order_id,
                at_tehran=order.at_tehran,
                age_hours=_age_hours(moment, parse_iso(order.at_tehran)),
                actor=names.person(order.actor_id),
                line_count=len(order.lines),
                lines=tuple(f"{catalog.item(line.item_id)} × {line.quantity}" for line in order.lines),
                note=order.note,
            )
        )
    return tuple(rows)


def _flagged_deliveries(
    connection: Connection, moment: datetime, names: _Names, index: _EventIndex
) -> tuple[FlaggedDeliveryLine, ...]:
    """A parcel with a problem label and no manager decision: its payment is locked (P-06, M-19)."""
    rows: list[FlaggedDeliveryLine] = []
    flags = flag_event_ids(index.events)
    for parcel in delivery_states(events_repo.iter_events(connection)).values():
        if not parcel.payment_locked or parcel.returned:
            continue
        rows.append(
            FlaggedDeliveryLine(
                delivery_id=parcel.delivery_id,
                invoice_reference=parcel.invoice_reference,
                reason_fa=_REASON_FA.get(parcel.flagged_reason or "", parcel.flagged_reason or ""),
                at_tehran=parcel.at_tehran,
                age_hours=_age_hours(moment, parse_iso(parcel.at_tehran)),
                actor=names.person(parcel.actor_id),
                flag_event_id=flags.get(parcel.delivery_id),
            )
        )
    return tuple(rows)


def _parcels_in_transit(connection: Connection, moment: datetime, names: _Names) -> tuple[ParcelLine, ...]:
    """A received parcel the storekeeper has not taken yet, with how long it has been on its way (P-12)."""
    rows: list[ParcelLine] = []
    for parcel in delivery_states(events_repo.iter_events(connection)).values():
        if parcel.returned or parcel.custody == _STORE_CUSTODY or parcel.status == "paid":
            continue
        rows.append(
            ParcelLine(
                delivery_id=parcel.delivery_id,
                invoice_reference=parcel.invoice_reference,
                packages=parcel.packages,
                at_tehran=parcel.at_tehran,
                age_hours=_age_hours(moment, parse_iso(parcel.at_tehran)),
                actor=names.person(parcel.actor_id),
            )
        )
    return tuple(rows)


def _weekly_count(connection: Connection, moment: datetime, settings: SettingsService) -> WeeklyCountLine:
    """This week's answer for the main store: done, still due, or past its deadline (P-11, M-20).

    The deadline is the versioned setting (R-09), read as the shift of the moment the card is built.
    The week closes only when every counted item of the catalog was counted in it, stock or no stock
    (decision 2), so a count of a few items leaves the card asking.
    """
    shift = covering(to_tehran(moment))
    due_weekday = settings.number(SettingKey.WEEKLY_COUNT_WEEKDAY, shift)
    start, _end = weekly_window(to_tehran(moment).date(), due_weekday=due_weekday)
    last: datetime | None = None
    counted: set[str] = set()
    for event in events_repo.iter_events(connection):
        if event.type != EventType.STOCK_WEEKLY_COUNT.value or event.payload.get("location_id") != MAIN_STORE:
            continue
        at = parse_iso(event.at_tehran)
        last = at
        if to_tehran(at).date() >= start:
            counted.add(str(event.payload["item_id"]))
    items = {
        item.item_id
        for item in catalog_repo.list_items_effective(connection, shift.text)
        if item.kind == "counted" and not item.retired
    }
    status = weekly_status(
        last,
        moment,
        due_weekday=due_weekday,
        due_time=settings.clock_time(SettingKey.WEEKLY_COUNT_TIME, shift),
        complete=items <= counted,
    )
    return WeeklyCountLine(
        state=status.state,
        window_from=status.window_start.isoformat(),
        window_to=status.window_end.isoformat(),
        last_count_on=None if status.last_count_at is None else status.last_count_at.date().isoformat(),
    )


def _below_minimum(connection: Connection, moment: datetime, catalog: _CatalogNames) -> tuple[BelowMinimumLine, ...]:
    """Counted items under their catalog minimum, with the figure the projection holds now (P-01, M-21)."""
    shift = covering(to_tehran(moment))
    stock = {(row["item_id"], row["location_id"]): int(row["qty"]) for row in projections_repo.stock_rows(connection)}
    rows: list[BelowMinimumLine] = []
    for item in catalog_repo.list_items_effective(connection, shift.text):
        if item.kind != "counted" or item.retired:
            continue
        quantity = stock.get((item.item_id, item.location_id), 0)
        if quantity < item.minimum:
            rows.append(
                BelowMinimumLine(
                    item_id=item.item_id,
                    name=item.name,
                    location=catalog.location(item.location_id),
                    minimum=item.minimum,
                    quantity=quantity,
                )
            )
    return tuple(rows)
