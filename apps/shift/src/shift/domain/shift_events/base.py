"""Registry of shift events (S01 §3, SHF-3): pure metadata, no IO and no database.

A shift event is a button on the staff phone: a code (ک، ب، ت…), a short label, a handful of
questions, one append-only event and — only where the clinic keeps a shelf or the box — one
projection effect. The registry keeps that description in one place so the server, the phone's
wizard and the manager's card all read the same vocabulary instead of three hand-written lists.

Every kind module registers itself at import; `shift_event_specs()` is what `domain/events.py`
merges into the event vocabulary, and `kind_for` is what the service and the card use to label a
row. Nothing here imports the event store or a connection.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from shift.domain.effects import EffectBuilder
from shift.domain.permissions import Permission, Role

APPROVED = "event.approved"
REJECTED = "event.rejected"
DECISION_TYPES = frozenset({APPROVED, REJECTED})

# The order the phone shows the groups in; money first, because it is the one that can hurt.
GROUP_ORDER = ("money", "stock", "care", "equipment")


class KindGroup(StrEnum):
    """The card of the phone: money, stock, care (patient items, vitals, cleaning), equipment."""

    MONEY = "money"
    STOCK = "stock"
    CARE = "care"
    EQUIPMENT = "equipment"


class FieldKind(StrEnum):
    """How the wizard renders one question; the server still validates the payload itself."""

    TEXT = "text"
    AMOUNT = "amount"
    QUANTITY = "quantity"
    CHOICE = "choice"
    ITEM = "item"
    LOCATION = "location"
    STAFF = "staff"
    DATE = "date"
    BOOLEAN = "boolean"
    MULTI = "multi"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One question of one event: its key in the payload, its Persian label and its shape."""

    key: str
    label_fa: str
    kind: FieldKind
    required: bool = True
    options: tuple[tuple[str, str], ...] = ()
    max_length: int = 200
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class EventView:
    """The little a kind's rule needs to know about another event it references."""

    event_id: str
    type: str
    actor_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ShiftEventContext:
    """Everything a kind's cross-field rule may read, supplied by the service.

    The callables are lookups, never writes: a rule decides whether an event may be appended, and
    the service owns the transaction. Keeping them as callables means a kind module stays pure and
    a test can hand it a dictionary instead of a database.
    """

    actor_id: str
    actor_roles: tuple[str, ...]
    shift_work_date: date
    shift_text: str
    withdrawal_cap: int
    # Every withdrawal this shift has already recorded, by its own occurred shift: the cap is a
    # running total, not a per-record limit (ADR-019).
    withdrawn_in_shift: Callable[[], int]
    item_at: Callable[[str, str], bool]
    staff_roles: Callable[[str], frozenset[str] | None]
    campaign_active: Callable[[str], bool]
    find_event: Callable[[str], EventView | None]
    decision_for: Callable[[str], str | None]
    assignee_for: Callable[[str], str | None]
    fixed_for: Callable[[str], bool]
    ref_event_id: str | None = None
    target_event: EventView | None = None


@dataclass(frozen=True, slots=True)
class KindSpec:
    """One button: its payload model, its effect, its questions and its confirmation rule.

    Who may close a record is the kind's own business: `approvable_by` names the roles, and
    `confirmer_field` names the payload key of the one person the clinic asked to confirm it (the
    «تأییدکننده» of waste, the «گیرنده» of a withdrawal). A kind that declares either waits for a
    decision; one that declares neither is never an open item.

    `phone` says whether the kind is a button at all. A reward is written by the service or by the
    manager's own screen, never typed on a staff phone: a self-service reward button would be a
    self-dealing hole (shf5-DESIGN.md §3), so `kinds_for` leaves such a kind out of the wizard while
    `kinds()` still answers every label the manager's card needs (SHF-5).
    """

    event_type: str
    code: str
    label_fa: str
    group: KindGroup
    fields: tuple[FieldSpec, ...]
    permission: Permission
    payload_model: type[BaseModel]
    effects: EffectBuilder
    sample: Callable[[], dict[str, Any]]
    summarize: Callable[[BaseModel], dict[str, Any]]
    validate: Callable[[BaseModel, ShiftEventContext], None] | None = None
    approvable_by: tuple[Role, ...] = ()
    confirmer_field: str | None = None
    needs_reference: bool = False
    reference_types: tuple[str, ...] = ()
    unique_reference: bool = False
    cross_shift: bool = False
    card_fields: tuple[str, ...] = ()
    # Some records change who may decide them from their own payload: an above-cap withdrawal is the
    # manager's decision, a below-cap one is the recipient's (ADR-019, owner rule 3).
    manager_only_when: Callable[[Mapping[str, Any]], bool] | None = None
    # A reward record is never a phone button: a reward the earner could record for themselves would
    # be a self-dealing hole (SHF-5, shf5-DESIGN.md §3), so the wizard skips these kinds entirely.
    phone: bool = True

    @property
    def requires_approval(self) -> bool:
        """Whether the record waits for a second person's decision.

        Derived from the two ways the clinic names a decider — a role or a person — so the manager's
        card cannot show a pending row that nobody is allowed to close, and a kind cannot silently ask
        for a confirmation it has no way to receive.
        """
        return bool(self.approvable_by or self.confirmer_field)


class ShiftEventError(ValueError):
    """A shift event the clinic's rules refuse; the phone shows the message, nothing is written."""


_REGISTRY: dict[str, KindSpec] = {}


def register(*kinds: KindSpec) -> None:
    """Add kinds to the closed vocabulary; a repeated type is a programming error."""
    for kind in kinds:
        if kind.event_type in _REGISTRY:
            raise ShiftEventError(f"shift event type registered twice: {kind.event_type!r}")
        if kind.event_type in DECISION_TYPES and kind.requires_approval:
            raise ShiftEventError("a decision event is never itself approvable")
        if kind.confirmer_field is not None and kind.confirmer_field not in {field.key for field in kind.fields}:
            raise ShiftEventError(f"{kind.event_type!r} confirms on a question it never asks: {kind.confirmer_field!r}")
        _REGISTRY[kind.event_type] = kind


def kinds() -> tuple[KindSpec, ...]:
    """Every registered kind, in the phone's group order and then in the order modules loaded."""
    order = {name: index for index, name in enumerate(GROUP_ORDER)}
    return tuple(sorted(_REGISTRY.values(), key=lambda kind: order[kind.group.value]))


def kind_for(event_type: str) -> KindSpec | None:
    """The kind for an event type, or None when the type is not a shift event (handover, accounting)."""
    return _REGISTRY.get(event_type)


def is_decision(event_type: str) -> bool:
    """True for `event.approved` and `event.rejected`."""
    return event_type in DECISION_TYPES


def registered_types() -> frozenset[str]:
    """Every type the wizard and the card know about."""
    return frozenset(_REGISTRY)


def offline_types() -> frozenset[str]:
    """Every shift event may be queued offline (ADR-016 keeps only the handover online)."""
    return frozenset(_REGISTRY)


def kinds_for(permission: Permission) -> tuple[KindSpec, ...]:
    """The buttons one permission opens; the phone never shows a button the API would refuse.

    A kind that is not a button (`phone=False`, the reward kinds) is left out here: it is written by
    the service or by the manager's screen, and offering it on the phone would be a self-dealing hole.
    """
    return tuple(kind for kind in kinds() if kind.permission is permission and kind.phone)


def shift_event_specs() -> Mapping[str, tuple[type[BaseModel], EffectBuilder]]:
    """Payload model and effect builder per type, for `domain/events.py` to compose."""
    return {kind.event_type: (kind.payload_model, kind.effects) for kind in _REGISTRY.values()}


def approval_roles(event_type: str) -> tuple[Role, ...]:
    """Who may decide a pending kind; empty for a kind that needs no approval."""
    kind = kind_for(event_type)
    return () if kind is None else kind.approvable_by


def decision_status(event_type: str, event_payload: Mapping[str, Any]) -> str:
    """The stored decision as a card status: `approved` or `rejected`."""
    return "approved" if event_type == APPROVED else "rejected"


def summary_for(kind: KindSpec, payload: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    """The card/approval summary of one event, read from the kind's own summarizer."""
    model = payload if isinstance(payload, BaseModel) else kind.payload_model.model_validate(payload)
    return dict(kind.summarize(model))


def unknown_fields(spec: KindSpec, keys: Sequence[str]) -> tuple[str, ...]:
    """Keys a caller supplied that the kind never asks for; the payload model also forbids them."""
    known = {field.key for field in spec.fields}
    return tuple(key for key in keys if key not in known)
