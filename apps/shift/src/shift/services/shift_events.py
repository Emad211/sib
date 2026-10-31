"""Shift events (S01 §3, SHF-3): the staff phone's records, their rules and their confirmations.

One endpoint receives every event, online or replayed from the phone's outbox (ADR-016). A type
registered in `domain.shift_events` is checked against the kind's permission and cross-field rule
before it is appended; an unknown type keeps the older outbox path unchanged, so the handover's
online-only refusal still belongs to `OutboxService`. The projection engine is synced after every
append, exactly as the older path did: a stock or cash event has to change the next handover's
reference before the phone can start that handover.

Confirmations are events too. `event.approved` / `event.rejected` carry a `ref_event_id` and are
refused for the target's own author or for a second decision. Who may decide is the target's own
business: the person its payload names (`confirmer_field` — the «تأییدکننده» of waste, the «گیرنده»
of a withdrawal), the holder of one of its roles (`approvable_by`), or the manager, who may always
decide an open record. Nothing confirms itself: when the person a withdrawal named is its own
recorder, any other staff member answers for it instead. No confirmation changes a projection: the
event it confirms already did (or never will), and a record that nobody may decide stays open on the
manager's card rather than closing by itself.

An open item outlives its shift (F2). A night shift's withdrawal, fault or held belonging is closed by
whoever is on duty when the right person arrives, so a decision may decide an undecided target of an
earlier shift, the reference kinds that declare themselves `cross_shift` may point at one, and
`pending_for` reads the whole log instead of the running shift. Every other reference keeps the
same-shift rule: a shift never borrows another shift's records (R-01), and the pending list carries
only each item itself — its summary, its actor and when — never a shift's figures.
"""

import json
from dataclasses import dataclass
from typing import Any

from shift.adapters.db import events_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, parse_iso, to_tehran
from shift.domain.event_types import EventType
from shift.domain.events import validate_payload
from shift.domain.occurred_time import OccurredVerdict
from shift.domain.occurred_time import verdict as occurred_verdict
from shift.domain.permissions import Role
from shift.domain.policy import SettingKey, ShiftKey
from shift.domain.shift_clock import covering
from shift.domain.shift_events import (
    APPROVED,
    DECISION_TYPES,
    REJECTED,
    EventView,
    KindSpec,
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
from shift.services.held_events import HeldEvents
from shift.services.late_cash import notice_late_cash
from shift.services.outbox import EventSubmission, OutboxService, OutboxValidationError, SubmissionResult
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService


class ShiftEventRuleError(OutboxValidationError):
    """A shift event the clinic's rules refuse; the route answers 422 with this message."""


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """One open event of the log the signed-in person may decide now, whatever shift opened it."""

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
    """Writes the shift events of the running shift and reads the open items waiting for one person."""

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
        held: HeldEvents | None = None,
    ) -> None:
        self._database = database
        self._events = events
        self._projections = projections
        self._catalog = catalog
        self._settings = settings
        self._campaigns = campaigns
        self._clock = clock or SystemClock()
        self._held = held
        self._outbox = OutboxService(events, projections, held=held, clock=self._clock)

    # --- writing ---------------------------------------------------------------------------

    def record(
        self,
        submission: EventSubmission,
        *,
        principal: Principal,
        shift: ShiftKey,
        held_checked: bool = False,
    ) -> SubmissionResult:
        """Validate one phone event against its kind and append it idempotently.

        `held_checked` is the manager's release path: the record already waited once, so its own
        instant is no longer a reason to keep it again (F5).

        The shift a record is judged against is the one it **happened** in, not the one that is running
        when it arrives: settings are versioned per shift (R-09), so a campaign, a cap or the catalog a
        queue is checked against must be the ones the person actually worked with. Otherwise a legal
        offline record is refused with a 422 the phone treats as final, and the clinic loses it (F5).
        """
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

        happened = _occurred_shift(submission) or shift
        if not held_checked:
            kept = self._hold_if_implausible(submission, principal, happened)
            if kept is not None:
                return kept

        try:
            new_event = submission.to_new_event()
        except OutboxValidationError:
            raise
        except Exception as exc:
            raise ShiftEventRuleError(str(exc)) from exc

        target = self._reference_target(kind, new_event.ref_event_id, happened)
        context = self._context(principal, happened, new_event.ref_event_id, target)

        if is_decision(submission.type):
            self._check_decision(target, principal)
        elif kind.validate is not None:
            try:
                kind.validate(payload, context)
            except ShiftEventError as exc:
                raise ShiftEventRuleError(str(exc)) from exc

        try:
            result = self._events.append(new_event)
        except EventValidationError as exc:
            raise ShiftEventRuleError(str(exc)) from exc
        # A money record that happened before a settled flow's cutoff is kept and flagged there, so
        # the settled box is never moved twice (H-11). The sync below applies both records.
        notice_late_cash(self._database, self._events, result.event, clock=self._clock)
        self._projections.sync()
        return SubmissionResult(
            result.event.seq, result.event.event_id, result.created, "recorded" if result.created else "replayed"
        )

    def _hold_if_implausible(
        self, submission: EventSubmission, principal: Principal, shift: ShiftKey
    ) -> SubmissionResult | None:
        """Keep a future-dated or three-day-old record for the manager instead of applying it (F5).

        The record is never refused: the phone is answered with 200 and a `held` status, the manager
        sees it on the daily card, and releasing it runs exactly this path once more, with the hold
        already checked. A record the clinic would otherwise lose is a record the clinic cannot
        explain later.
        """
        if self._held is None:
            return None
        occurred = submission.client_at
        try:
            occurred = parse_iso(occurred) if isinstance(occurred, str) else occurred
            checked = occurred_verdict(occurred, self._clock.now_utc())
        except ValueError as exc:
            raise ShiftEventRuleError("زمان ثبت رویداد باید یک لحظهٔ کامل با منطقهٔ زمانی باشد.") from exc
        if checked is OccurredVerdict.OK:
            return None
        return self._held.hold(submission, reason=checked.value, shift=shift)

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

    def _reference_target(self, kind: KindSpec, ref_event_id: str | None, shift: ShiftKey) -> EventRecord | None:
        """Resolve the target of a reference event; every rule about it is enforced before append.

        A decision and a kind that declares `cross_shift` may point at an earlier shift's event: a
        fault, a held belonging or an undecided confirmation is closed when the person it waits for
        arrives, not when the shift ends. Every other reference stays inside its own shift (R-01).
        """
        if not kind.needs_reference and not is_decision(kind.event_type):
            return None
        if not ref_event_id:
            raise ShiftEventRuleError("این رویداد باید به یک ثبت قبلی ارجاع بدهد.")
        with self._database.read() as connection:
            target = events_repo.find_by_event_id(connection, ref_event_id)
        if target is None:
            raise ShiftEventRuleError("ثبت مرجع پیدا نشد؛ ابتدا رویداد اصلی را ثبت کنید.")
        if not kind.cross_shift and not is_decision(kind.event_type) and target.shift_id != shift.text:
            raise ShiftEventRuleError("ثبت مرجع باید از همین شیفت باشد.")
        if kind.needs_reference and kind.reference_types and target.type not in kind.reference_types:
            raise ShiftEventRuleError("این رویداد به نوع ثبت‌شدهٔ درستی ارجاع نمی‌دهد.")
        if kind.needs_reference and kind.unique_reference:
            with self._database.read() as connection:
                repeats = events_repo.count_ref(connection, ref_event_id, (kind.event_type,))
            if repeats:
                raise ShiftEventRuleError("این ثبت قبلاً بسته شده است.")
        return target

    def _check_decision(self, target: EventRecord | None, principal: Principal) -> None:
        """Who may decide which undecided event, once — whatever shift recorded it."""
        if target is None:
            raise ShiftEventRuleError("ثبت مرجع پیدا نشد؛ ابتدا رویداد اصلی را ثبت کنید.")
        target_kind = kind_for(target.type)
        if target_kind is None or not waits_for_decision(target_kind, target):
            raise ShiftEventRuleError("این رویداد نیاز به تأیید ندارد.")
        if target.actor_id == principal.staff_id:
            raise ShiftEventRuleError("ثبت‌کننده نمی‌تواند همان رویداد را تأیید کند.")
        if not _may_decide(target_kind, target, principal):
            raise ShiftEventRuleError("شما اجازهٔ تأیید این رویداد را ندارید.")
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

        def withdrawn_in_shift() -> int:
            """Every withdrawal this shift has already recorded, by the shift it occurred in (ADR-019)."""
            with self._database.read() as connection:
                rows = connection.exec_driver_sql(
                    "SELECT payload FROM events WHERE type = ? AND shift_id = ?",
                    (EventType.CASH_WITHDRAWN.value, shift.text),
                ).mappings()
            return sum(int(json.loads(str(row["payload"])).get("amount", 0)) for row in rows)

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
            withdrawn_in_shift=withdrawn_in_shift,
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

    def pending_for(self, principal: Principal) -> list[PendingApproval]:
        """Every open item this person may decide now, whatever shift it was opened in.

        An item is open when its kind waits for a confirmation, no decision references it yet, and the
        caller is one of its deciders — which already excludes its author and an item whose only
        decider is the author. The scan is the whole log, oldest first, because a withdrawal or a
        waste left open last night is exactly what the person the clinic named has to see in the
        morning. Each row is the item itself, its actor and its time: no shift's cash figures and no
        other event's data (R-01, R-07).
        """
        with self._database.read() as connection:
            events = list(events_repo.iter_events(connection))
            names = _Names(connection)
            decided = {
                event.ref_event_id
                for event in events
                if event.type in DECISION_TYPES and event.ref_event_id is not None
            }
            pending: list[PendingApproval] = []
            for event in events:
                kind = kind_for(event.type)
                if kind is None or not waits_for_decision(kind, event) or event.event_id in decided:
                    continue
                if not _may_decide(kind, event, principal):
                    continue
                pending.append(
                    PendingApproval(
                        event_id=event.event_id,
                        type=event.type,
                        code=kind.code,
                        label_fa=kind.label_fa,
                        at_tehran=event.at_tehran,
                        shift_id=event.shift_id or "",
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
                    "needs_approval": kind.requires_approval,
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


def _may_decide(kind: KindSpec, event: EventRecord, principal: Principal) -> bool:
    """The deciders of one record: the person it names, a role it names, or the manager — never its author.

    Nothing confirms itself: the recorder never decides their own record and no decision is written on
    their behalf, so a record left with no decider stays open on the manager's card instead of closing
    by itself. When the person a withdrawal named is its own recorder, any other staff member answers
    for it instead — the session proves the caller is an active staff member (`AuthService` refuses a
    deactivated one) — and the manager may always decide an open record, whatever its kind.
    """
    if event.actor_id == principal.staff_id:
        return False
    # The owner's waste rule names the culprit so the clinic can ask them about it; the person who
    # caused the loss never confirms it, manager or not (E-05).
    culprit = event.payload.get("culprit_id")
    if culprit is not None and str(culprit) == principal.staff_id:
        return False
    roles = set(principal.roles)
    if kind.manager_only_when is not None and kind.manager_only_when(event.payload):
        # An above-cap withdrawal names a manager at the desk and the manager decides it later.
        return Role.MANAGER.value in roles
    if Role.MANAGER.value in roles:
        return True
    if {role.value for role in kind.approvable_by}.intersection(roles):
        return True
    named = None if kind.confirmer_field is None else event.payload.get(kind.confirmer_field)
    if named is None:
        return False
    if named == principal.staff_id:
        return True
    # The named person recorded it themselves (a recipient taking cash): they can never confirm it.
    return named == event.actor_id


def waits_for_decision(kind: KindSpec, event: EventRecord) -> bool:
    """Whether a record still waits on somebody: it asks for a confirmation and names a decider.

    A row stored before the clinic asked for a second person — a waste record of SHF-3's first version
    carries no «تأییدکننده» — has no decider at all, so it is a finished record and not an open item
    that nobody could ever close. A kind that is only sometimes confirmable says so from its payload:
    an above-cap withdrawal waits for the manager, a below-cap one for nobody (ADR-019).
    """
    if not kind.requires_approval:
        return False

    if kind.approvable_by:
        return True
    return bool(str(event.payload.get(kind.confirmer_field or "") or "").strip())


def _occurred_shift(submission: EventSubmission) -> ShiftKey | None:
    """The shift a record happened in, from its own instant; None when the instant cannot be read."""
    occurred = submission.client_at
    if isinstance(occurred, str):
        try:
            occurred = parse_iso(occurred)
        except ValueError:
            return None
    try:
        return covering(to_tehran(occurred))
    except ValueError:
        return None
