"""Shift events (S01 §3, SHF-3): the staff phone's records, their rules and their approvals.

One endpoint receives every event, online or replayed from the phone's outbox (ADR-016). A type
registered in `domain.shift_events` is checked against the kind's permission and cross-field rule
before it is appended; an unknown type keeps the older outbox path unchanged, so the handover's
online-only refusal still belongs to `OutboxService`. The projection engine is synced after every
append, exactly as the older path did: a stock or cash event has to change the next handover's
reference before the phone can start that handover.

Approvals are events too. `event.approved` / `event.rejected` carry a `ref_event_id`, name the role
that may decide the target kind, and are refused for the target's own author or for a second decision.
No approval changes a projection: the event it decides already did (or never will).
"""

from dataclasses import dataclass
from typing import Any

from shift.adapters.db import events_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock
from shift.domain.events import validate_payload
from shift.domain.policy import SettingKey, ShiftKey
from shift.domain.shift_events import (
    APPROVED,
    DECISION_TYPES,
    REJECTED,
    EventView,
    ShiftEventContext,
    ShiftEventError,
    is_decision,
    kind_for,
    kinds,
    summary_for,
)
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, ItemKind
from shift.services.event_store import EventStore, EventValidationError
from shift.services.outbox import EventSubmission, OutboxService, OutboxValidationError, SubmissionResult
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService


class ShiftEventRuleError(OutboxValidationError):
    """A shift event the clinic's rules refuse; the route answers 422 with this message."""


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """One undecided event the signed-in approver may decide now."""

    event_id: str
    type: str
    code: str
    label_fa: str
    at_tehran: str
    shift_id: str
    actor: dict[str, str | None]
    summary: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "code": self.code,
            "label": self.label_fa,
            "at_tehran": self.at_tehran,
            "shift_id": self.shift_id,
            "actor": self.actor,
            "summary": self.summary,
        }


class ShiftEventService:
    """Writes the shift events of one shift and reads the approvals waiting for one person."""

    def __init__(
        self,
        database: Database,
        events: EventStore,
        projections: ProjectionEngine,
        catalog: CatalogService,
        settings: SettingsService,
        *,
        campaigns: Any | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._database = database
        self._events = events
        self._projections = projections
        self._catalog = catalog
        self._settings = settings
        self._campaigns = campaigns
        self._clock = clock or SystemClock()
        self._outbox = OutboxService(events, projections)

    # --- writing ---------------------------------------------------------------------------

    def record(self, submission: EventSubmission, *, principal: Principal, shift: ShiftKey) -> SubmissionResult:
        """Validate one phone event against its kind and append it idempotently."""
        kind = kind_for(submission.type)
        if kind is None:
            # Handover, accounting mirror and legacy stock types keep the outbox path and its
            # ADR-016 refusal for anything live-only.
            return self._outbox.record_event(submission)

        if kind.permission not in principal.permissions:
            raise ShiftEventRuleError("نقش شما اجازهٔ ثبت این رویداد را ندارد.")

        try:
            payload = validate_payload(submission.type, submission.payload)
        except Exception as exc:
            raise ShiftEventRuleError(f"Invalid payload for {submission.type!r}: {exc}") from exc

        existing = self._existing(submission)
        if existing is not None:
            return SubmissionResult(existing.seq, existing.event_id, False, "replayed")

        try:
            new_event = submission.to_new_event()
        except OutboxValidationError:
            raise
        except Exception as exc:
            raise ShiftEventRuleError(str(exc)) from exc

        target = self._reference_target(kind, new_event.ref_event_id, shift)
        context = self._context(principal, shift, new_event.ref_event_id, target)

        if is_decision(submission.type):
            self._check_decision(target, principal, shift)
        elif kind.validate is not None:
            try:
                kind.validate(payload, context)
            except ShiftEventError as exc:
                raise ShiftEventRuleError(str(exc)) from exc

        try:
            result = self._events.append(new_event)
        except EventValidationError as exc:
            raise ShiftEventRuleError(str(exc)) from exc
        self._projections.sync()
        return SubmissionResult(
            result.event.seq, result.event.event_id, result.created, "recorded" if result.created else "replayed"
        )

    def _existing(self, submission: EventSubmission) -> EventRecord | None:
        """A replay returns the stored row before any rule runs again.

        A campaign can expire or a cap can change between the first attempt and the retry; the event
        was accepted under the rules of its own moment, so a retry must not turn into a new refusal
        that keeps the phone's queue stuck forever.
        """
        key = submission.idempotency_key or submission.client_event_id or submission.event_id
        if not key:
            return None
        with self._database.read() as connection:
            found = events_repo.find_by_idempotency_key(connection, key)
            if found is not None:
                return found
            event_id = submission.client_event_id or submission.event_id
            return None if not event_id else events_repo.find_by_event_id(connection, event_id)

    def _reference_target(self, kind: Any, ref_event_id: str | None, shift: ShiftKey) -> EventRecord | None:
        """Resolve the target of a reference event; every rule about it is enforced before append."""
        if not kind.needs_reference and not is_decision(kind.event_type):
            return None
        if not ref_event_id:
            raise ShiftEventRuleError("این رویداد باید به یک ثبت قبلی ارجاع بدهد.")
        with self._database.read() as connection:
            target = events_repo.find_by_event_id(connection, ref_event_id)
        if target is None:
            raise ShiftEventRuleError("ثبت مرجع پیدا نشد؛ ابتدا رویداد اصلی را ثبت کنید.")
        if target.shift_id != shift.text:
            raise ShiftEventRuleError("ثبت مرجع باید از همین شیفت باشد.")
        if kind.needs_reference and kind.reference_types and target.type not in kind.reference_types:
            raise ShiftEventRuleError("این رویداد به نوع ثبت‌شدهٔ درستی ارجاع نمی‌دهد.")
        if kind.needs_reference and kind.unique_reference:
            with self._database.read() as connection:
                repeats = events_repo.count_ref(connection, ref_event_id, (kind.event_type,))
            if repeats:
                raise ShiftEventRuleError("این ثبت قبلاً بسته شده است.")
        return target

    def _check_decision(self, target: EventRecord | None, principal: Principal, shift: ShiftKey) -> None:
        """Who may decide which pending event, once."""
        if target is None:
            raise ShiftEventRuleError("ثبت مرجع پیدا نشد؛ ابتدا رویداد اصلی را ثبت کنید.")
        target_kind = kind_for(target.type)
        if target_kind is None or not target_kind.approvable_by:
            raise ShiftEventRuleError("این رویداد نیاز به تأیید ندارد.")
        approvers = {role.value for role in target_kind.approvable_by}
        if not approvers.intersection(principal.roles):
            raise ShiftEventRuleError("نقش شما اجازهٔ تأیید این رویداد را ندارد.")
        if target.actor_id == principal.staff_id:
            raise ShiftEventRuleError("ثبت‌کننده نمی‌تواند همان رویداد را تأیید کند.")
        if target.shift_id != shift.text:
            raise ShiftEventRuleError("تأیید فقط برای رویدادهای شیفت جاری است.")
        with self._database.read() as connection:
            decided = events_repo.latest_ref(connection, target.event_id, tuple(DECISION_TYPES))
        if decided is not None:
            raise ShiftEventRuleError("برای این رویداد قبلاً تصمیم ثبت شده است.")

    def _context(
        self,
        principal: Principal,
        shift: ShiftKey,
        ref_event_id: str | None,
        target: EventRecord | None,
    ) -> ShiftEventContext:
        """The lookups a kind rule may use; each one opens a short read connection."""
        items = {(item.item_id, item.location_id) for item in self._catalog.items(shift)}
        cap = self._settings.number(SettingKey.WITHDRAWAL_CAP_TOMAN, shift)

        def item_at(item_id: str, location_id: str) -> bool:
            return (item_id, location_id) in items

        def staff_roles(staff_id: str) -> frozenset[str] | None:
            with self._database.read() as connection:
                person = staff_repo.find(connection, staff_id)
                roles = staff_repo.roles_for(connection, staff_id) if person is not None else ()
            if person is None or not person.active:
                return None
            # The login session's roles are the authority here (R-07/R-08); the catalog role
            # versions are the manager's next-shift preview, not the running credential.
            return frozenset(roles)

        def find_event(event_id: str) -> EventView | None:
            with self._database.read() as connection:
                row = events_repo.find_by_event_id(connection, event_id)
            return _view(row)

        def decision_for(event_id: str) -> str | None:
            with self._database.read() as connection:
                row = events_repo.latest_ref(connection, event_id, tuple(DECISION_TYPES))
            return None if row is None else row.type

        def assignee_for(event_id: str) -> str | None:
            with self._database.read() as connection:
                row = events_repo.latest_ref(connection, event_id, ("equipment.fault_assigned",))
            return None if row is None else str(row.payload.get("assignee_id", "")) or None

        def fixed_for(event_id: str) -> bool:
            with self._database.read() as connection:
                row = events_repo.latest_ref(connection, event_id, ("equipment.fault_fixed",))
            return row is not None

        def campaign_active(campaign_id: str) -> bool:
            if self._campaigns is None:
                return False
            active = self._campaigns.active_on(shift.work_date)
            return any(campaign.campaign_id == campaign_id for campaign in active)

        return ShiftEventContext(
            actor_id=principal.staff_id,
            actor_roles=principal.roles,
            shift_work_date=shift.work_date,
            shift_text=shift.text,
            withdrawal_cap=cap,
            item_at=item_at,
            staff_roles=staff_roles,
            campaign_active=campaign_active,
            find_event=find_event,
            decision_for=decision_for,
            assignee_for=assignee_for,
            fixed_for=fixed_for,
            ref_event_id=ref_event_id,
            target_event=None if target is None else _view(target),
        )

    # --- reading ---------------------------------------------------------------------------

    def pending_for(self, principal: Principal, shift: ShiftKey) -> list[PendingApproval]:
        """Undecided events in this shift that this person's roles may decide."""
        roles = set(principal.roles)
        with self._database.read() as connection:
            events = list(events_repo.iter_shift(connection, shift.text))
            names = _Names(connection)
            decisions = {
                event.ref_event_id
                for event in events
                if event.type in DECISION_TYPES and event.ref_event_id is not None
            }
            pending: list[PendingApproval] = []
            for event in events:
                kind = kind_for(event.type)
                if kind is None or not kind.approvable_by or event.event_id in decisions:
                    continue
                if not roles.intersection({role.value for role in kind.approvable_by}):
                    continue
                pending.append(
                    PendingApproval(
                        event_id=event.event_id,
                        type=event.type,
                        code=kind.code,
                        label_fa=kind.label_fa,
                        at_tehran=event.at_tehran,
                        shift_id=shift.text,
                        actor={"id": event.actor_id, "name": names.full_name(event.actor_id)},
                        summary=summary_for(kind, event.payload),
                    )
                )
        return pending

    def page_context(self, principal: Principal, shift: ShiftKey) -> dict[str, Any]:
        """The phone's JSON catalogue: only the buttons this person may press, plus their lists.

        No patient data and no approval rows: the shell is cached on the phone and may be opened
        offline by the same person tomorrow, so it must not carry another shift's work (R-01, R-07).
        """
        items = self._catalog.items(shift)
        locations = self._catalog.locations(shift)
        with self._database.read() as connection:
            people = staff_repo.list_with_roles(connection)
        campaigns = [] if self._campaigns is None else self._campaigns.active_on(shift.work_date)
        # The owner's rule: outside a manager-defined campaign there is no discount button at all.
        campaign_available = bool(campaigns)
        return {
            "kinds": [
                {
                    "type": kind.event_type,
                    "code": kind.code,
                    "label": kind.label_fa,
                    "group": kind.group.value,
                    "needs_approval": bool(kind.approvable_by),
                    "fields": [
                        {
                            "key": field.key,
                            "label": field.label_fa,
                            "kind": field.kind.value,
                            "required": field.required,
                            "options": [list(option) for option in field.options],
                            "max_length": field.max_length,
                            "minimum": field.minimum,
                            "maximum": field.maximum,
                        }
                        for field in kind.fields
                    ],
                }
                for kind in kinds()
                if kind.permission in principal.permissions
                and not is_decision(kind.event_type)
                and (kind.event_type != "campaign.discount_applied" or campaign_available)
            ],
            "items": [
                {
                    "id": item.item_id,
                    "name": item.name,
                    "location_id": item.location_id,
                    "unit": item.unit,
                    "kind": item.kind.value,
                }
                for item in items
            ],
            "counted_items": [item.item_id for item in items if item.kind is ItemKind.COUNTED],
            "locations": [{"id": place.location_id, "name": place.name} for place in locations],
            "staff": [
                {"id": person.staff.staff_id, "name": person.staff.full_name, "roles": list(person.roles)}
                for person in people
                if person.staff.active
            ],
            "campaigns": [
                {"id": campaign.campaign_id, "name": campaign.name, "amount": campaign.amount_toman}
                for campaign in campaigns
            ],
            "pending_url": "/api/v1/shift-events/pending",
            "approved_type": APPROVED,
            "rejected_type": REJECTED,
        }


class _Names:
    """Names for one read; a person who left the roster keeps the id and no name."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._read: dict[str, str | None] = {}

    def full_name(self, staff_id: str) -> str | None:
        if staff_id not in self._read:
            person = staff_repo.find(self._connection, staff_id)
            self._read[staff_id] = None if person is None else person.full_name
        return self._read[staff_id]


def _view(event: EventRecord | None) -> EventView | None:
    if event is None:
        return None
    return EventView(event.event_id, event.type, event.actor_id, event.payload)
