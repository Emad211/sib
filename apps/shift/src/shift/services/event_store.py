"""Append-only event store: validation, idempotency, server time and hash chain."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, require_aware, tehran_iso, utc_iso
from shift.common.hashing import GENESIS_HASH, canonical_json
from shift.common.ids import is_ulid
from shift.domain.event_hash import event_hash
from shift.domain.events import EventSpec, spec_for, validate_payload


class EventValidationError(ValueError):
    """The caller supplied an event that cannot enter the log."""


@dataclass(frozen=True, slots=True)
class NewEvent:
    """An event as submitted by an offline client; server times are not trusted."""

    client_event_id: str
    type: str
    payload: Mapping[str, Any]
    actor_id: str
    device_id: str
    work_date: date
    client_at: datetime
    shift_id: str | None = None
    ref_event_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class AppendResult:
    event: EventRecord
    created: bool


class EventStore:
    """Write side of the event log; one transaction per batch, one writer per process."""

    def __init__(self, database: Database, *, clock: Clock | None = None, hash_chain: bool = True) -> None:
        self._database = database
        self._clock = clock or SystemClock()
        self._hash_chain = hash_chain

    def append(self, event: NewEvent) -> AppendResult:
        """Append one event, returning the existing row when it is a retry."""
        return self.append_many([event])[0]

    def append_many(self, events: Sequence[NewEvent]) -> list[AppendResult]:
        """Append a batch atomically; duplicates return their original `seq` unchanged."""
        if not events:
            return []
        with self._database.write() as connection:
            previous = events_repo.last_event(connection)
            results: list[AppendResult] = []
            for event in events:
                result = self._append_one(connection, event, previous)
                results.append(result)
                if result.created:
                    previous = result.event
            return results

    def _append_one(self, connection: Connection, event: NewEvent, previous: EventRecord | None) -> AppendResult:
        spec = self._validate(event)
        key = event.idempotency_key or event.client_event_id
        existing = events_repo.find_by_idempotency_key(connection, key)
        if existing is None:
            existing = events_repo.find_by_event_id(connection, event.client_event_id)
        if existing is not None:
            return AppendResult(existing, False)

        payload = validate_payload(event.type, event.payload)
        server_at = self._clock.now_utc()
        previous_hash = (previous.hash if previous is not None else GENESIS_HASH) if self._hash_chain else ""
        payload_json = canonical_json(payload.model_dump(mode="json"))
        values: dict[str, Any] = {
            "event_id": event.client_event_id,
            "type": event.type,
            "schema_v": spec.schema_v,
            "payload": payload_json,
            "actor_id": event.actor_id,
            "device_id": event.device_id,
            "shift_id": event.shift_id,
            "work_date": event.work_date.isoformat(),
            "client_at": event.client_at.isoformat(timespec="microseconds"),
            "server_at": utc_iso(server_at),
            "at_tehran": tehran_iso(server_at),
            "ref_event_id": event.ref_event_id,
            "idempotency_key": key,
            "prev_hash": previous_hash,
            "hash": "",
        }
        values["hash"] = event_hash(previous_hash, values)
        seq = events_repo.insert_event(connection, values)
        record = EventRecord(
            seq=seq,
            event_id=event.client_event_id,
            type=event.type,
            schema_v=spec.schema_v,
            payload=payload.model_dump(mode="json"),
            actor_id=event.actor_id,
            device_id=event.device_id,
            shift_id=event.shift_id,
            work_date=event.work_date.isoformat(),
            client_at=values["client_at"],
            server_at=values["server_at"],
            at_tehran=values["at_tehran"],
            ref_event_id=event.ref_event_id,
            idempotency_key=key,
            prev_hash=previous_hash,
            hash=values["hash"],
        )
        return AppendResult(record, True)

    def _validate(self, event: NewEvent) -> EventSpec:
        if not is_ulid(event.client_event_id):
            raise EventValidationError("client_event_id must be a ULID")
        if not event.actor_id.strip() or not event.device_id.strip():
            raise EventValidationError("actor_id and device_id are required")
        if isinstance(event.work_date, datetime) or not isinstance(event.work_date, date):
            raise EventValidationError("work_date must be a date")
        if not isinstance(event.client_at, datetime):
            raise EventValidationError("client_at must be a datetime")
        require_aware(event.client_at)
        if event.shift_id is not None and not event.shift_id.strip():
            raise EventValidationError("shift_id must be non-empty when supplied")
        if event.ref_event_id is not None and not is_ulid(event.ref_event_id):
            raise EventValidationError("ref_event_id must be a ULID")
        if event.idempotency_key is not None and not event.idempotency_key.strip():
            raise EventValidationError("idempotency_key must be non-empty when supplied")
        spec = spec_for(event.type)
        if spec.requires_shift and not event.shift_id:
            raise EventValidationError(f"event {event.type!r} requires shift_id")
        return spec
