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
and the only figure a section carries is a discrepancy's gap or a flow's cash exception total.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import audit_repo, card_repo, events_repo, handover_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, parse_iso, tehran_iso, to_tehran, to_utc, utc_iso
from shift.common.hashing import canonical_json
from shift.domain.daily_card import CARD_SCHEMA_VERSION, CardWindow, due_card_days, window_for
from shift.domain.events import EventType
from shift.services.audit import AuditEntry, AuditService
from shift.services.handover import EXCEPTION_ACTION

# The background runtime produces the cards, exactly as it produces the snapshots; runtime.RUNTIME_ACTOR
# is the same name, and the card service does not import the asyncio runtime for one string.
ACTOR = "shift-runtime"
GENERATED_ACTION = "daily_card.generated"
_EMERGENCY = EventType.HANDOVER_EMERGENCY_START_CONFIRMED.value
_DISCREPANCY = EventType.DISCREPANCY_RECORDED.value
_SECONDS_PER_HOUR = 3600


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

    def to_payload(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "flow": self.flow,
            "at_tehran": self.at_tehran,
            "amount": self.amount,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CashException":
        return cls(
            handover_id=str(payload["handover_id"]),
            flow=str(payload["flow"]),
            at_tehran=str(payload["at_tehran"]),
            amount=int(payload["amount"]),
        )


@dataclass(frozen=True, slots=True)
class DiscrepancyLine:
    """A settled count's gap from its reference; `culprit` None is the unnamed emergency path (H-08)."""

    handover_id: str
    flow: str
    at_tehran: str
    difference: int
    item_id: str | None
    culprit: Person | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "handover_id": self.handover_id,
            "flow": self.flow,
            "at_tehran": self.at_tehran,
            "difference": self.difference,
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
class Card:
    """One day's manager card: the window it covers, when it was built, and its five sections.

    A section whose data does not exist yet is absent from the payload rather than a placeholder: the
    shift events of SHF-3, the purchases of SHF-4 and the rewards of SHF-5 have no section here yet.
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
            },
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Card":
        """Read a stored card back; only the schema version this code writes is accepted."""
        schema = int(payload["schema"])
        if schema != CARD_SCHEMA_VERSION:
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
        )
        for row in rows
    )


def _discrepancies(connection: Connection, window: CardWindow, names: _Names) -> tuple[DiscrepancyLine, ...]:
    """Every recorded gap of the window, newest first, rendered as a magnitude.

    `payload["difference"]` is signed (positive a shortage, negative a surplus) because the log keeps
    the direction; the card shows the size of the gap, and the direction stays in the event log.
    """
    events = _window_events(connection, window, _DISCREPANCY)
    return tuple(
        DiscrepancyLine(
            handover_id=str(event.handover_id or ""),
            flow=_flow_of(connection, event.handover_id),
            at_tehran=event.at_tehran,
            difference=abs(int(event.payload["difference"])),
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
