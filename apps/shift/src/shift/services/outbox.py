"""Outbox orchestration service: validates and records offline-minted events.

ADR-016 keeps the handover out of this path: a blind count is compared against a reference that must
never reach the phone, so a handover runs on the live server and its events are refused here.

A phone may sit offline across midnight, so an event is attributed by the instant it **occurred**, not
by the shift that happens to be running when it finally arrives (F5). The server's own received time
is stored beside it (`server_at`), which is what lets the manager see how late a record was. An instant
in the future or older than three days is not refused and not applied: it is held for the manager.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from shift.common.clock import Clock, SystemClock, parse_iso, to_tehran
from shift.domain.events import EventType, UnknownEventType, spec_for, validate_payload
from shift.domain.occurred_time import OccurredVerdict, verdict
from shift.domain.shift_clock import covering
from shift.services.event_store import EventStore, EventValidationError, NewEvent
from shift.services.projections import ProjectionEngine

if TYPE_CHECKING:  # the held store imports this module's submission, so the cycle is type-only
    from shift.services.held_events import HeldEvents

# Day and evening shifts belong to their calendar date; the night shift that
# starts at 19:30 belongs to the date on which it opened, even after midnight.
NIGHT_SHIFT_END = time(7, 30)

# ADR-016: a handover runs against the live server, where the blind comparison happens, so its
# vocabulary never arrives from the offline queue. The handover service is its only writer, and the
# refusal is written for the person holding the phone (Persian, no numbers) while keeping the ADR
# token the operator scripts and the older tests look for.
LIVE_ONLY = frozenset(
    {event_type.value for event_type in EventType if event_type.value.startswith("handover.")}
    | {EventType.DISCREPANCY_RECORDED.value}
)
LIVE_ONLY_REFUSAL = "رویدادهای تحویل شیفت فقط روی سرور درمانگاه ثبت می‌شوند و از صف آفلاین پذیرفته نمی‌شوند (ADR-016)."


class OutboxValidationError(ValueError):
    """Payload or event metadata from the outbox failed validation."""


def business_date_for(client_at: datetime) -> date:
    """Return the shift business date for an event instant (Tehran wall clock)."""
    local = to_tehran(client_at)
    if local.time() < NIGHT_SHIFT_END:
        return local.date() - timedelta(days=1)
    return local.date()


def _parse_work_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise OutboxValidationError("work_date must be a date, not a datetime")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OutboxValidationError("work_date must be in YYYY-MM-DD format") from exc


@dataclass(frozen=True, slots=True)
class EventSubmission:
    """Incoming JSON representation of an offline-minted event.

    Identity fields are filled by the route from the authenticated `Principal`;
    `requested_work_date` is only a client hint that must agree with the
    authenticated shift date (or the derived Tehran business date).
    """

    type: str
    payload: Mapping[str, Any]
    actor_id: str
    device_id: str
    client_at: str | datetime
    client_event_id: str | None = None
    event_id: str | None = None
    shift_id: str | None = None
    ref_event_id: str | None = None
    idempotency_key: str | None = None
    requested_work_date: str | date | None = None
    shift_work_date: date | None = None

    def to_new_event(self) -> NewEvent:
        """Convert into a validated NewEvent instance.

        The shift and the business date come from the instant the record happened, never from the
        shift that is running when it arrives: a queue that crossed midnight, or a phone that crossed
        it offline, still belongs to the shift that was on duty when the money or the stock moved
        (F5). `requested_work_date` stays a hint the phone sends; a disagreement is a clock or a
        queue effect, not a reason to throw the record away.
        """
        cid = self.client_event_id or self.event_id
        if not cid:
            raise OutboxValidationError("client_event_id or event_id is required")

        parsed_client_at: datetime
        if isinstance(self.client_at, str):
            try:
                parsed_client_at = parse_iso(self.client_at)
            except Exception as exc:
                raise OutboxValidationError("client_at must be an ISO-8601 aware datetime") from exc
        else:
            parsed_client_at = self.client_at

        try:
            expected = business_date_for(parsed_client_at)
        except ValueError as exc:
            raise OutboxValidationError("client_at must be an ISO-8601 aware datetime") from exc
        # The un-validated hint is parsed anyway: a malformed one is the caller's error, not a reason
        # to write a record against a date nobody can read.
        _parse_work_date(self.requested_work_date)
        occurred_shift = covering(to_tehran(parsed_client_at))

        return NewEvent(
            client_event_id=cid,
            type=self.type,
            payload=self.payload,
            actor_id=self.actor_id,
            device_id=self.device_id,
            work_date=expected,
            client_at=parsed_client_at,
            shift_id=occurred_shift.text,
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
    held: bool = False


class OutboxService:
    """Server-side outbox receiver for append-only idempotent ingest."""

    def __init__(
        self,
        event_store: EventStore,
        projections: ProjectionEngine | None = None,
        *,
        held: "HeldEvents | None" = None,
        clock: Clock | None = None,
    ) -> None:
        self._store = event_store
        self._projections = projections
        self._held = held
        self._clock = clock or SystemClock()

    def record_event(self, submission: EventSubmission) -> SubmissionResult:
        """Validate payload against domain schema, append idempotently and sync projections."""
        try:
            spec_for(submission.type)
        except UnknownEventType as exc:
            raise OutboxValidationError(str(exc)) from exc

        if submission.type in LIVE_ONLY:
            raise OutboxValidationError(LIVE_ONLY_REFUSAL)

        try:
            validate_payload(submission.type, submission.payload)
        except Exception as exc:
            raise OutboxValidationError(f"Invalid payload for {submission.type!r}: {exc}") from exc

        held = self._hold_if_implausible(submission)
        if held is not None:
            return held

        try:
            new_event = submission.to_new_event()
            result = self._store.append(new_event)
        except EventValidationError as exc:
            raise OutboxValidationError(str(exc)) from exc

        # Replayed items run this too: a crash between append and sync then heals
        # on the client's next retry instead of leaving proj_stock/proj_cash stale.
        if self._projections is not None:
            self._projections.sync()

        return SubmissionResult(
            seq=result.event.seq,
            event_id=result.event.event_id,
            created=result.created,
            status="recorded" if result.created else "replayed",
        )

    def _hold_if_implausible(self, submission: EventSubmission) -> SubmissionResult | None:
        """Keep a future-dated or three-day-old record for the manager instead of applying it (F5).

        A None answer means the record may be applied now. The course this takes changes no box and
        no shelf: the held record is written, the phone is answered with 200 and the manager decides.
        """
        if self._held is None:
            return None
        occurred = submission.client_at
        occurred = parse_iso(occurred) if isinstance(occurred, str) else occurred
        try:
            checked = verdict(occurred, self._clock.now_utc())
        except ValueError as exc:
            raise OutboxValidationError("client_at must be an ISO-8601 aware datetime") from exc
        if checked is OccurredVerdict.OK:
            return None
        return self._held.hold(submission, reason=checked.value, shift=covering(to_tehran(occurred)))
