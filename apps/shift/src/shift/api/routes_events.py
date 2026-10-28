"""Events API: single idempotent ingest endpoint for offline outbox replay."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, OutboxService, OutboxValidationError

router = APIRouter(prefix="/api/v1", tags=["events"])


def _resolve_default_guard() -> Callable[[Request], Any]:
    """Resolve require() from shift.api.deps if available; fallback to no-op."""
    try:
        from shift.api.deps import require  # type: ignore[import-not-found]
        from shift.domain.permissions import Permission  # type: ignore[import-not-found]

        perm = getattr(Permission, "EVENTS_APPEND", None) or getattr(Permission, "HANDOVER_SUBMIT", None)
        if perm:
            return require(perm)
    except Exception:
        pass

    def _injected_guard(request: Request) -> None:
        # Until the auth agent lands, depend on an injected guard so wiring is a one-line change.
        return None

    return _injected_guard


# Dependency hook for auth guard
events_guard = _resolve_default_guard()


class EventIn(BaseModel):
    """Client event submission schema."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="Event type key from domain vocabulary")
    payload: dict[str, Any] = Field(..., description="Event payload matching registered schema")
    actor_id: str = Field(..., min_length=1, description="Staff actor identifier")
    device_id: str = Field(..., min_length=1, description="Enrolled device identifier")
    work_date: date | str = Field(..., description="Business work date (YYYY-MM-DD)")
    client_at: datetime | str = Field(..., description="Time of event on client device (ISO 8601)")
    client_event_id: str | None = Field(default=None, description="Client-minted ULID")
    event_id: str | None = Field(default=None, description="Alias for client_event_id")
    shift_id: str | None = Field(default=None, description="Active shift ID when applicable")
    ref_event_id: str | None = Field(default=None, description="Optional referenced event ULID")
    idempotency_key: str | None = Field(default=None, description="Replay prevention key (defaults to client_event_id)")


class EventOut(BaseModel):
    """Event ingestion response."""

    seq: int
    event_id: str
    created: bool
    status: str


def get_event_store(request: Request) -> EventStore:
    """Retrieve app EventStore from application state."""
    return request.app.state.events


@router.post(
    "/events",
    response_model=EventOut,
    status_code=status.HTTP_200_OK,
    summary="Append an offline-minted event idempotently",
)
def post_event(
    event_in: EventIn,
    store: Annotated[EventStore, Depends(get_event_store)],
    _actor: Annotated[Any, Depends(events_guard)],
) -> EventOut:
    """Validate event payload against domain registry and insert idempotently.

    Duplicate idempotency_key replays return the existing seq with HTTP 200.
    Invalid payloads return HTTP 422.
    """
    submission = EventSubmission(
        type=event_in.type,
        payload=event_in.payload,
        actor_id=event_in.actor_id,
        device_id=event_in.device_id,
        work_date=event_in.work_date,
        client_at=event_in.client_at,
        client_event_id=event_in.client_event_id,
        event_id=event_in.event_id,
        shift_id=event_in.shift_id,
        ref_event_id=event_in.ref_event_id,
        idempotency_key=event_in.idempotency_key,
    )

    outbox_svc = OutboxService(store)
    try:
        result = outbox_svc.record_event(submission)
    except OutboxValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return EventOut(
        seq=result.seq,
        event_id=result.event_id,
        created=result.created,
        status=result.status,
    )
