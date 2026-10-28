"""Outbox orchestration service: validates and records offline-minted events."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from shift.common.clock import parse_iso
from shift.domain.events import UnknownEventType, spec_for, validate_payload
from shift.services.event_store import EventStore, EventValidationError, NewEvent


class OutboxValidationError(ValueError):
    """Payload or event metadata from the outbox failed validation."""


@dataclass(frozen=True, slots=True)
class EventSubmission:
    """Incoming JSON representation of an offline-minted event."""

    type: str
    payload: Mapping[str, Any]
    actor_id: str
    device_id: str
    work_date: str | date
    client_at: str | datetime
    client_event_id: str | None = None
    event_id: str | None = None
    shift_id: str | None = None
    ref_event_id: str | None = None
    idempotency_key: str | None = None

    def to_new_event(self) -> NewEvent:
        """Convert into a validated NewEvent instance."""
        cid = self.client_event_id or self.event_id
        if not cid:
            raise OutboxValidationError("client_event_id or event_id is required")

        parsed_date: date
        if isinstance(self.work_date, str):
            try:
                parsed_date = date.fromisoformat(self.work_date)
            except ValueError as exc:
                raise OutboxValidationError("work_date must be in YYYY-MM-DD format") from exc
        else:
            parsed_date = self.work_date

        parsed_client_at: datetime
        if isinstance(self.client_at, str):
            try:
                parsed_client_at = parse_iso(self.client_at)
            except Exception as exc:
                raise OutboxValidationError("client_at must be an ISO-8601 aware datetime") from exc
        else:
            parsed_client_at = self.client_at

        return NewEvent(
            client_event_id=cid,
            type=self.type,
            payload=self.payload,
            actor_id=self.actor_id,
            device_id=self.device_id,
            work_date=parsed_date,
            client_at=parsed_client_at,
            shift_id=self.shift_id,
            ref_event_id=self.ref_event_id,
            idempotency_key=self.idempotency_key or cid,
        )


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    """Result returned to the client on initial recording or replay."""

    seq: int
    event_id: str
    created: bool
    status: str


class OutboxService:
    """Server-side outbox receiver for append-only idempotent ingest."""

    def __init__(self, event_store: EventStore) -> None:
        self._store = event_store

    def record_event(self, submission: EventSubmission) -> SubmissionResult:
        """Validate payload against domain schema and append idempotently."""
        try:
            spec_for(submission.type)
        except UnknownEventType as exc:
            raise OutboxValidationError(str(exc)) from exc

        try:
            validate_payload(submission.type, submission.payload)
        except Exception as exc:
            raise OutboxValidationError(f"Invalid payload for {submission.type!r}: {exc}") from exc

        try:
            new_event = submission.to_new_event()
            result = self._store.append(new_event)
        except EventValidationError as exc:
            raise OutboxValidationError(str(exc)) from exc

        return SubmissionResult(
            seq=result.event.seq,
            event_id=result.event.event_id,
            created=result.created,
            status="recorded" if result.created else "replayed",
        )
