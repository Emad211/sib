"""Held records: the server keeps an implausible offline record instead of dropping it (F5).

ADR-016 sends every phone record through the outbox, and a phone may sit offline for a whole shift or
cross midnight. None of that loses a record: the server attributes it by its own instant. Two cases
are still not trustworthy enough to apply alone — an instant in the future (a handset whose clock ran
ahead) and an instant older than three days — and those are **held**: the record is written to the log
as `event.held`, the phone gets its 200, the box and the shelves do not move, and the manager releases
or rejects it on the daily card. Releasing appends the original record with its own identity and its
own instant, so its movement lands exactly once; rejecting keeps it out of the books for good.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, parse_iso, utc_iso
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.occurred_time import HELD_MESSAGES
from shift.domain.policy import ShiftKey
from shift.services.event_store import EventStore, NewEvent
from shift.services.outbox import EventSubmission, SubmissionResult

HELD = EventType.EVENT_HELD.value
RELEASED = EventType.EVENT_RELEASED.value
HOLD_REJECTED = EventType.EVENT_HOLD_REJECTED.value
_DECISIONS = (RELEASED, HOLD_REJECTED)
NOT_HELD = "این رویداد در فهرست نگه‌داشته‌شده‌ها نیست."
ALREADY_DECIDED = "برای این رویداد قبلاً تصمیم گرفته شده است."


@dataclass(frozen=True, slots=True)
class Held:
    """One held record read back from the log, with the submission it kept."""

    held_event_id: str
    event_id: str
    type: str
    reason: str
    occurred_at: datetime
    received_at: datetime
    actor_id: str
    device_id: str
    shift_id: str | None
    work_date: str
    body: Mapping[str, Any]

    @property
    def message_fa(self) -> str:
        """The Persian line the manager reads beside the held record."""
        return HELD_MESSAGES.get(self.reason, HELD_MESSAGES["stale"])

    def submission(self) -> EventSubmission:
        """The original submission, so releasing it runs exactly the path it would have run on arrival."""
        return EventSubmission(
            type=self.type,
            payload=self.body,
            actor_id=self.actor_id,
            device_id=self.device_id,
            client_at=self.occurred_at,
            client_event_id=self.event_id,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "held_event_id": self.held_event_id,
            "event_id": self.event_id,
            "type": self.type,
            "reason": self.reason,
            "message": self.message_fa,
            "occurred_at": utc_iso(self.occurred_at),
            "received_at": utc_iso(self.received_at),
            "actor_id": self.actor_id,
        }


class HeldEvents:
    """Writes and reads the held records; the only writer of `event.held` and its two decisions."""

    def __init__(self, database: Database, store: EventStore, *, clock: Clock | None = None) -> None:
        self._database = database
        self._store = store
        self._clock = clock or SystemClock()

    def hold(self, submission: EventSubmission, *, reason: str, shift: ShiftKey) -> SubmissionResult:
        """Keep one implausible record in the log without letting its movement into the books."""
        moment = self._clock.now_utc()
        event_id = submission.client_event_id or submission.event_id
        if not event_id:
            raise ValueError("a held submission needs its client event id")
        occurred = (
            submission.client_at if isinstance(submission.client_at, datetime) else parse_iso(submission.client_at)
        )
        result = self._store.append(
            NewEvent(
                client_event_id=new_ulid(moment),
                type=HELD,
                payload={
                    "reason": reason,
                    "type": submission.type,
                    "event_id": event_id,
                    "occurred_at": utc_iso(occurred),
                    "payload": dict(submission.payload),
                },
                actor_id=submission.actor_id,
                device_id=submission.device_id,
                work_date=shift.work_date,
                client_at=moment,
                shift_id=shift.text,
                ref_event_id=event_id,
            )
        )
        return SubmissionResult(
            seq=result.event.seq,
            # The phone recognises its own record by the id it minted; the held row keeps its own id
            # (`held_event_id`) for the manager's decision, and the two are never mixed up.
            event_id=event_id,
            created=result.created,
            status="held",
            held=True,
        )

    def pending(self) -> list[Held]:
        """Every held record no manager has released or rejected yet, oldest first."""
        with self._database.read() as connection:
            events = list(events_repo.iter_events(connection))
        decided = {event.ref_event_id for event in events if event.type in _DECISIONS}
        return [
            held
            for event in events
            if event.type == HELD and event.event_id not in decided and (held := _held(event)) is not None
        ]

    def release(self, held_event_id: str, *, record: Callable[[EventSubmission], SubmissionResult]) -> Held:
        """Let a held record through, once: its own event lands now and its movement applies now."""
        held = self._open(held_event_id)
        record(held.submission())
        self._append(held, RELEASED)
        return held

    def reject(self, held_event_id: str) -> Held:
        """Keep a held record out of the books for good; nothing is applied and nothing is deleted."""
        held = self._open(held_event_id)
        self._append(held, HOLD_REJECTED)
        return held

    def _open(self, held_event_id: str) -> Held:
        """The held record behind an id, or a refusal when it is unknown or already decided."""
        with self._database.read() as connection:
            event = events_repo.find_by_event_id(connection, held_event_id)
            decided = None if event is None else events_repo.count_ref(connection, held_event_id, _DECISIONS)
        if event is None or event.type != HELD:
            raise UnknownHeld(NOT_HELD)
        if decided:
            raise UnknownHeld(ALREADY_DECIDED)
        held = _held(event)
        if held is None:
            raise UnknownHeld(NOT_HELD)
        return held

    def _append(self, held: Held, event_type: str) -> EventRecord:
        """Record the manager's decision on the held record; the decision changes no projection."""
        moment = self._clock.now_utc()
        return self._store.append(
            NewEvent(
                client_event_id=new_ulid(moment),
                type=event_type,
                payload={"event_id": held.event_id},
                actor_id=held.actor_id,
                device_id=held.device_id,
                work_date=date.fromisoformat(held.work_date),
                client_at=moment,
                shift_id=held.shift_id,
                ref_event_id=held.held_event_id,
            )
        ).event


class UnknownHeld(ValueError):
    """The id is not an open held record; the route answers 404 with this message."""


def _held(event: EventRecord) -> Held | None:
    """Read one `event.held` row back into the submission it kept."""
    payload = event.payload
    try:
        return Held(
            held_event_id=event.event_id,
            event_id=str(payload["event_id"]),
            type=str(payload["type"]),
            reason=str(payload["reason"]),
            occurred_at=parse_iso(str(payload["occurred_at"])),
            received_at=parse_iso(str(event.server_at)),
            actor_id=event.actor_id,
            device_id=event.device_id,
            shift_id=event.shift_id,
            work_date=event.work_date,
            body=dict(payload["payload"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
