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
    """Event ingestion response; `held` is additive, so an older cached shell keeps working (F5)."""

    seq: int
    event_id: str
    created: bool
    status: str
    held: bool = False


class PendingEventOut(BaseModel):
    """One open shift event the signed-in person may decide, whatever shift opened it."""

    event_id: str
    type: str
    code: str
    label: str
    at_tehran: str
    shift_id: str
    actor: dict[str, str | None]
    summary: dict[str, Any]


class OpenTargetOut(BaseModel):
    """One open record the signed-in person may act on from the wizard (SHF-7, F2)."""

    event_id: str
    type: str
    code: str
    label: str
    at_tehran: str
    actor: dict[str, str | None]
    summary: dict[str, Any]
    actions: list[str]


def get_shift_events(request: Request) -> ShiftEventService:
    """The shift-event service created in the lifespan (routes never build their own)."""
    return request.app.state.shift_events


PHOTO_MISSING = "عکس رسید پیدا نشد؛ ابتدا عکس را بفرستید."


def _require_photo(request: Request, payload: dict[str, Any]) -> None:
    """An event that names a receipt photo may only point at a photo this server already holds.

    The phone uploads the blob before it replays the event (SHF-7), so a hash with no file behind it
    is a broken reference on the manager's card, not a record worth keeping: the route refuses it
    here, before the service applies any projection effect.
    """
    sha = payload.get("photo_sha256")
    if sha is None:
        return
    store = getattr(request.app.state, "photos", None)
    if store is None or not store.exists(str(sha)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=PHOTO_MISSING)


@router.get(
    "/shift-events/pending",
    response_model=list[PendingEventOut],
    summary="The open items this signed-in person may decide now",
)
def get_pending_events(
    principal: Annotated[Principal, Depends(require(Permission.EVENTS_APPEND))],
    service: Annotated[ShiftEventService, Depends(get_shift_events)],
) -> list[PendingEventOut]:
    """Any phone that may append may ask; the service answers with its own open items only (R-01, R-07).

    The confirmation the owner asks for may be a nurse's or a receptionist's, so the route cannot be
    the approval roles' alone. It returns the item, its actor and its time — no shift's figures and
    nothing another person could decide.
    """
    return [PendingEventOut(**entry.to_payload()) for entry in service.pending_for(principal)]


@router.get(
    "/shift-events/open",
    response_model=list[OpenTargetOut],
    summary="The open records this signed-in person may act on in the wizard",
)
def get_open_events(
    principal: Annotated[Principal, Depends(require(Permission.EVENTS_APPEND))],
    shift: Annotated[ShiftKey, Depends(running_shift)],
    service: Annotated[ShiftEventService, Depends(get_shift_events)],
) -> list[OpenTargetOut]:
    """The record a closing event points at: a fault to assign or fix, a belonging to deliver.

    Any phone that may append asks; the service answers with the records this person may act on and
    nothing else, whatever shift opened them (F2, R-01, R-07). No cash figure is in the answer.
    """
    return [OpenTargetOut(**entry.to_payload()) for entry in service.open_targets(principal, shift)]


@router.post(
    "/events",
    response_model=EventOut,
    status_code=status.HTTP_200_OK,
    summary="Append an offline-minted event idempotently",
)
def post_event(
    event_in: EventIn,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require(Permission.EVENTS_APPEND))],
    shift: Annotated[ShiftKey, Depends(running_shift)],
    service: Annotated[ShiftEventService, Depends(get_shift_events)],
) -> EventOut:
    """Validate an event payload and insert it idempotently for the session principal.

    Duplicate idempotency_key replays return the existing seq with HTTP 200.
    Invalid payloads or a clinic rule refusal return HTTP 422; anonymous requests fail closed with 401.
    """
    _require_photo(request, event_in.payload)
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
        held=result.held,
    )
