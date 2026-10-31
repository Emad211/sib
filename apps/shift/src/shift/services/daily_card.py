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
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import audit_repo, card_repo, catalog_repo, events_repo, handover_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, parse_iso, tehran_iso, to_tehran, to_utc, utc_iso
from shift.common.hashing import canonical_json
from shift.domain.daily_card import CARD_SCHEMA_VERSION, CardWindow, due_card_days, window_for
from shift.domain.events import EventType
from shift.domain.occurred_time import HELD_MESSAGES
from shift.domain.shift_events import (
    APPROVED,
    DECISION_TYPES,
    FieldKind,
    KindSpec,
    is_decision,
    kind_for,
    summary_for,
)
from shift.services.audit import AuditEntry, AuditService
from shift.services.handover import EXCEPTION_ACTION
from shift.services.shift_events import waits_for_decision

# The background runtime produces the cards, exactly as it produces the snapshots; runtime.RUNTIME_ACTOR
# is the same name, and the card service does not import the asyncio runtime for one string.
ACTOR = "shift-runtime"
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

# The status a shift-event row carries on the card; `open_events` keeps the three that still wait.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_REPORTED = "reported"
STATUS_ASSIGNED = "assigned"
STATUS_FIXED = "fixed"
STATUS_RECORDED = "recorded"
OPEN_STATUSES = frozenset({STATUS_PENDING, STATUS_REPORTED, STATUS_ASSIGNED})

# Every section a card of this code writes, in the document's own order; a stored card that predates
# a section simply does not carry it, and the page then does not render it (schema 2 has five).
SECTION_NAMES = (
    "emergency_starts",
    "over_cap_withdrawals",
    "cash_exceptions",
    "discrepancies",
    "flows",
    "open_flows",
    "shift_events",
    "open_events",
    "late_cash",
    "held_events",
)
LEGACY_SCHEMA_VERSIONS = frozenset({2, 3})


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

    def to_payload(self) -> dict[str, Any]:
        return {
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

    def to_payload(self) -> dict[str, Any]:
        return {
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
class Card:
    """One day's manager card: the window it covers, when it was built, and its seven sections.

    `present_sections` is not part of the document: it remembers which sections the stored card
    carried, so a schema-2 card — written before the shift-event sections existed — does not grow two
    empty blocks when the page renders it. The purchases of SHF-4 and the rewards of SHF-5 still have
    no section here.
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
            },
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Card":
        """Read a stored card back; schema 2 (before the shift-event sections) and 3 are accepted."""
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
            present_sections=tuple(name for name in SECTION_NAMES if name in sections),
        )


class DailyCardService:
    """Builds, stores and reads the manager's daily card (S01 §6, SHF-6.1)."""

    def __init__(self, database: Database, *, clock: Clock | None = None, audit: AuditService | None = None) -> None:
        self._database = database
        self._clock = clock or SystemClock()
        self._audit = audit or AuditService(database, clock=self._clock)

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
                shift_events=_shift_events(connection, window, names, catalog, index),
                open_events=_open_events(moment, names, catalog, index),
                over_cap_withdrawals=_over_cap_withdrawals(connection, window, names),
                late_cash=_late_cash(connection, window, names),
                held_events=_held_events(connection, window, names),
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


def _over_cap_withdrawals(connection: Connection, window: CardWindow, names: _Names) -> tuple[WithdrawalLine, ...]:
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
            )
        )
    return tuple(rows)


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
