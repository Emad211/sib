"""Events API: single idempotent ingest endpoint for offline outbox replay.

The endpoint never trusts client-asserted identity: ``actor_id``, ``device_id``
and ``shift_id`` come from the authenticated :class:`~shift.api.principal.Principal`.
It fails closed (503) when the auth guard has not been installed, and unsafe
requests need a double-submit CSRF token.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from shift.api.principal import as_principal, require_csrf_token, require_principal
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, OutboxService, OutboxValidationError
from shift.services.projections import ProjectionEngine

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
    # deploy; the route overwrites every one of them from the Principal.
    actor_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Ignored; from session")
    device_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Ignored; from session")
    shift_id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER, description="Ignored; from session")


class EventOut(BaseModel):
    """Event ingestion response."""

    seq: int
    event_id: str
    created: bool
    status: str


def get_event_store(request: Request) -> EventStore:
    """Retrieve the app EventStore from application state."""
    return request.app.state.events


def get_projections(request: Request) -> ProjectionEngine:
    """Retrieve the app projection engine so each append becomes visible immediately."""
    return request.app.state.projections


@router.post(
    "/events",
    response_model=EventOut,
    status_code=status.HTTP_200_OK,
    summary="Append an offline-minted event idempotently",
)
def post_event(
    event_in: EventIn,
    response: Response,
    store: Annotated[EventStore, Depends(get_event_store)],
    projections: Annotated[ProjectionEngine, Depends(get_projections)],
    principal_result: Annotated[Any, Depends(require_principal)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
) -> EventOut:
    """Validate an event payload and insert it idempotently for the session principal.

    Duplicate idempotency_key replays return the existing seq with HTTP 200.
    Invalid payloads return HTTP 422; anonymous requests fail closed.
    """
    # Coerce again so an `app.dependency_overrides[require_principal]` wired to
    # the auth lane's session object still yields a validated Principal.
    principal = as_principal(principal_result)
    submission = EventSubmission(
        type=event_in.type,
        payload=event_in.payload,
        actor_id=principal.actor_id,
        device_id=principal.device_id,
        client_at=event_in.client_at,
        client_event_id=event_in.client_event_id,
        event_id=event_in.event_id,
        shift_id=principal.shift_id,
        ref_event_id=event_in.ref_event_id,
        idempotency_key=event_in.idempotency_key,
        requested_work_date=event_in.work_date,
        shift_work_date=principal.work_date,
    )

    outbox_svc = OutboxService(store, projections)
    try:
        result = outbox_svc.record_event(submission)
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
