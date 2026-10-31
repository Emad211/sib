"""The held-record events (F5): a record the server keeps instead of dropping or applying (ADR-016).

A record whose own instant is in the future or more than three days old is not refused — the clinic
would lose it — and not applied either: the manager decides. `event.held` keeps the submission as it
arrived, `event.released` says the manager let it through (the original event is appended then, once,
with its own identity and its own instant), and `event.hold_rejected` says it stays out for good.
None of the three changes a projection by itself.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from shift.domain.payloads import Identifier, Note, Payload


class SubmissionHeld(Payload):
    """One record kept aside: what it was, when it happened and why it waits."""

    reason: Literal["future", "stale"]
    type: Annotated[str, Field(min_length=1, max_length=64)]
    event_id: Identifier
    occurred_at: datetime
    payload: dict[str, Any]


class HeldReleased(Payload):
    """The manager let a held record through; the original event carries the movement itself."""

    event_id: Identifier
    note: Note | None = None


class HeldRejected(Payload):
    """The manager kept a held record out of the books; the reason stays for whoever asks later."""

    event_id: Identifier
    note: Note | None = None
