"""Events API: single idempotent ingest endpoint for offline outbox replay.

The endpoint never trusts client-asserted identity: ``actor_id``, ``device_id``,
``shift_id`` and the business date come from the authenticated session and the clinic
clock, through ``api.deps.require(Permission.EVENTS_APPEND)``. Unsafe requests need a
CSRF proof (session token or the shell's double-submit cookie).
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from shift.api.deps import require, running_shift
from shift.domain.permissions import Permission
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.outbox import EventSubmission, OutboxValidationError
from shift.services.shift_events import ShiftEventService

MAX_REQUEST_BYTES = 64 * 1024
_MAX_IDENTIFIER = 128


class _BodySizeLimitedRoute(APIRoute):
    """Reject an oversized declared body before FastAPI reads and parses it."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def limited(request: Request) -> Response:
            raw_length = request.headers.get("content-length")
            if raw_length is not None:
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail="invalid Content-Length"
                    ) from exc
                if length > MAX_REQUEST_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"request body exceeds {MAX_REQUEST_BYTES} bytes",
                    )
            return await original(request)

        return limited


router = APIRouter(prefix="/api/v1", tags=["events"], route_class=_BodySizeLimitedRoute)


class EventIn(BaseModel):
    """Client event submission schema; identity fields are discarded."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64, description="Event type key from domain vocabulary")
    payload: dict[str, Any] = Field(..., description="Event payload matching registered schema")
    client_at: datetime | str = Field(..., description="Time of event on client device (ISO 8601)")
    work_date: date | str | None = Field(default=None, description="Optional client hint, validated server-side")
    client_event_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Client-minted ULID")
    event_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Alias for client_event_id")
    ref_event_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Referenced event ULID")
    idempotency_key: str | None = Field(
        default=None, max_length=_MAX_IDENTIFIER, description="Replay prevention key (defaults to client_event_id)"
    )
    # Accepted only so a PWA build cached before this change does not 422 after
    # deploy; the route overwrites every one of them from the session principal.
    actor_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Ignored; from session")
    device_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Ignored; from session")
    shift_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Ignored; from session")


class EventOut(BaseModel):
    """Event ingestion response."""

    seq: int
    event_id: str
    created: bool
    status: str


class PendingEventOut(BaseModel):
    """One undecided shift event the signed-in approver may decide."""

    event_id: str
    type: str
    code: str
    label: str
    at_tehran: str
    shift_id: str
    actor: dict[str, str | None]
    summary: dict[str, Any]


def get_shift_events(request: Request) -> ShiftEventService:
    """The shift-event service created in the lifespan (routes never build their own)."""
    return request.app.state.shift_events


@router.get(
    "/shift-events/pending",
    response_model=list[PendingEventOut],
    summary="Events waiting for this approver in the running shift",
)
def get_pending_events(
    principal: Annotated[Principal, Depends(require(Permission.SHIFT_EVENT_APPROVE))],
    shift: Annotated[ShiftKey, Depends(running_shift)],
    service: Annotated[ShiftEventService, Depends(get_shift_events)],
) -> list[PendingEventOut]:
    """Only the current shift, only the kinds this person's role may decide (R-01, R-07)."""
    return [PendingEventOut(**entry.to_payload()) for entry in service.pending_for(principal, shift)]


@router.post(
    "/events",
    response_model=EventOut,
    status_code=status.HTTP_200_OK,
    summary="Append an offline-minted event idempotently",
)
def post_event(
    event_in: EventIn,
    response: Response,
    principal: Annotated[Principal, Depends(require(Permission.EVENTS_APPEND))],
    shift: Annotated[ShiftKey, Depends(running_shift)],
    service: Annotated[ShiftEventService, Depends(get_shift_events)],
) -> EventOut:
    """Validate an event payload and insert it idempotently for the session principal.

    Duplicate idempotency_key replays return the existing seq with HTTP 200.
    Invalid payloads or a clinic rule refusal return HTTP 422; anonymous requests fail closed with 401.
    """
    submission = EventSubmission(
        type=event_in.type,
        payload=event_in.payload,
        actor_id=principal.staff_id,
        device_id=principal.device_id,
        client_at=event_in.client_at,
        client_event_id=event_in.client_event_id,
        event_id=event_in.event_id,
        shift_id=shift.text,
        ref_event_id=event_in.ref_event_id,
        idempotency_key=event_in.idempotency_key,
        requested_work_date=event_in.work_date,
        shift_work_date=shift.work_date,
    )

    try:
        result = service.record(submission, principal=principal, shift=shift)
    except OutboxValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return EventOut(
        seq=result.seq,
        event_id=result.event_id,
        created=result.created,
        status=result.status,
    )
