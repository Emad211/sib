"""What waits for the manager's own decision right now, and the two decisions taken from that page (C1).

The daily card is a snapshot of yesterday; a decision belongs to the present. So the card links to one
live page that lists, from the log as it stands:

* a flow whose outgoing person left early and that still waits for the manager (H-08) — the manager
  confirms on their own phone that the incoming person finishes alone, instead of a phone call;
* a discrepancy no manager has answered yet — two related gaps are both recorded and the manager decides
  (S01 «اگر دو مغایرت به هم مربوط باشند… مدیر تصمیم می‌گیرد»); the answer is a `manager.reviewed`
  record that names the discrepancy, so the log keeps who decided and when;
* a broken device nobody has been assigned to yet, with a way into the assignment.

Nothing here shows a reference number: a discrepancy's own difference is its record, never the figure
the count was compared with (R-02).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from shift.adapters.db import events_repo, handover_repo, staff_repo
from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, parse_iso, to_tehran
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.permissions import Role
from shift.domain.shift_clock import covering
from shift.services.auth import Principal
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore, NewEvent
from shift.services.handover import Caller, HandoverService

# How far back an unanswered discrepancy stays on the page; older ones are read on past cards.
DISCREPANCY_WINDOW = timedelta(days=14)
FLOW_LABELS = {HandoverFlow.RECEPTION: "پذیرش", HandoverFlow.NURSING: "پرستاری"}
REVIEW_LABELS = {"approved": "پذیرفته شد", "rejected": "پذیرفته نشد"}
_DISCREPANCY = EventType.DISCREPANCY_RECORDED.value
_REVIEWED = EventType.MANAGER_REVIEWED.value
_FAULT = EventType.EQUIPMENT_FAULT_REPORTED.value
_FAULT_CLOSERS = (EventType.EQUIPMENT_FAULT_ASSIGNED.value, EventType.EQUIPMENT_FAULT_FIXED.value)

NOT_A_MANAGER = "این تصمیم را فقط مدیر می‌گیرد."
UNKNOWN_ITEM = "این مورد پیدا نشد یا دیگر منتظر تصمیم نیست؛ صفحه را تازه کنید."
ALREADY_REVIEWED = "این مغایرت قبلاً پاسخ گرفته است."


class DecisionRefused(ValueError):
    """The manager's decision could not be recorded; the message is Persian and carries no number."""


@dataclass(frozen=True, slots=True)
class EarlyLeave:
    handover_id: str
    flow: str
    shift: str
    outgoing: str
    incoming: str
    opened_at_tehran: str


@dataclass(frozen=True, slots=True)
class OpenDiscrepancy:
    event_id: str
    flow: str
    at_tehran: str
    difference: int
    what: str
    culprit: str | None


@dataclass(frozen=True, slots=True)
class UnassignedFault:
    event_id: str
    device: str
    description: str
    actor: str
    at_tehran: str
    photo_sha256: str | None


@dataclass(frozen=True, slots=True)
class PendingDecisions:
    early_leaves: tuple[EarlyLeave, ...]
    discrepancies: tuple[OpenDiscrepancy, ...]
    faults: tuple[UnassignedFault, ...]

    @property
    def count(self) -> int:
        return len(self.early_leaves) + len(self.discrepancies) + len(self.faults)


class ManagerDecisions:
    """Reads the live list and writes the manager's two answers, never anything else."""

    def __init__(
        self,
        database: Database,
        store: EventStore,
        handover: HandoverService,
        catalog: CatalogService,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._database = database
        self._store = store
        self._handover = handover
        self._catalog = catalog
        self._clock = clock or SystemClock()

    def pending(self) -> PendingDecisions:
        now = self._clock.now_utc()
        with self._database.read() as connection:
            names = {person.staff.staff_id: person.staff.full_name for person in staff_repo.list_with_roles(connection)}
            early = tuple(
                EarlyLeave(
                    handover_id=row.handover_id,
                    flow=FLOW_LABELS.get(row.flow, str(row.flow)),
                    shift=row.shift_id,
                    outgoing=names.get(row.outgoing_id, "—"),
                    incoming=names.get(row.incoming_id, "—"),
                    opened_at_tehran=to_tehran(row.opened_at).isoformat(),
                )
                for row in handover_repo.open_flows(connection)
                if row.early_left and row.emergency_by is None
            )
            events = list(events_repo.iter_events(connection))
        reviewed = {event.ref_event_id for event in events if event.type == _REVIEWED and event.ref_event_id}
        closed_faults = {event.ref_event_id for event in events if event.type in _FAULT_CLOSERS and event.ref_event_id}
        item_names = self._item_names(now)
        floor = now - DISCREPANCY_WINDOW
        flows: dict[str, str] = {}
        discrepancies: list[OpenDiscrepancy] = []
        faults: list[UnassignedFault] = []
        for event in events:
            if event.type == _DISCREPANCY and event.event_id not in reviewed and parse_iso(event.client_at) >= floor:
                payload = event.payload
                if event.handover_id and event.handover_id not in flows:
                    flows[event.handover_id] = self._flow_label(event.handover_id)
                culprit = payload.get("culprit_id")
                discrepancies.append(
                    OpenDiscrepancy(
                        event_id=event.event_id,
                        flow=flows.get(event.handover_id or "", "—"),
                        at_tehran=event.at_tehran,
                        difference=int(payload.get("difference") or 0),
                        what="صندوق"
                        if payload.get("scope") == "cash"
                        else item_names.get(str(payload.get("item_id")), "کالا"),
                        culprit=None if culprit is None else names.get(str(culprit), "—"),
                    )
                )
            elif event.type == _FAULT and event.event_id not in closed_faults:
                faults.append(
                    UnassignedFault(
                        event_id=event.event_id,
                        device=str(event.payload.get("device") or ""),
                        description=str(event.payload.get("description") or ""),
                        actor=names.get(event.actor_id, "—"),
                        at_tehran=event.at_tehran,
                        photo_sha256=event.payload.get("photo_sha256"),
                    )
                )
        return PendingDecisions(early, tuple(discrepancies), tuple(faults))

    def confirm_early_leave(self, handover_id: str, manager: Principal) -> None:
        """H-08 from the manager's own phone: the incoming person finishes the flow alone."""
        self._require_manager(manager)
        if not any(row.handover_id == handover_id for row in self.pending().early_leaves):
            raise DecisionRefused(UNKNOWN_ITEM)
        self._handover.confirm_emergency_start_as_manager(
            handover_id, caller=Caller(actor_id=manager.staff_id, device_id=manager.device_id)
        )

    def review(self, event_id: str, decision: str, note: str | None, manager: Principal) -> str:
        """The manager's answer to one discrepancy, recorded once, naming the discrepancy it answers."""
        self._require_manager(manager)
        if decision not in REVIEW_LABELS:
            raise DecisionRefused(UNKNOWN_ITEM)
        with self._database.read() as connection:
            target = events_repo.find_by_event_id(connection, event_id)
            answered = events_repo.count_ref(connection, event_id, (_REVIEWED,))
        if target is None or target.type != _DISCREPANCY:
            raise DecisionRefused(UNKNOWN_ITEM)
        if answered:
            raise DecisionRefused(ALREADY_REVIEWED)
        moment = self._clock.now_utc()
        shift = covering(to_tehran(moment))
        payload: dict[str, Any] = {"decision": decision, "note": (note or "").strip()[:500] or None}
        result = self._store.append(
            NewEvent(
                client_event_id=new_ulid(moment),
                type=_REVIEWED,
                payload=payload,
                actor_id=manager.staff_id,
                device_id=manager.device_id,
                work_date=shift.work_date,
                client_at=moment,
                shift_id=shift.text,
                ref_event_id=event_id,
                idempotency_key=f"manager-review:{event_id}",
            )
        )
        return result.event.event_id

    def _require_manager(self, principal: Principal) -> None:
        if Role.MANAGER.value not in principal.roles:
            raise DecisionRefused(NOT_A_MANAGER)

    def _flow_label(self, handover_id: str) -> str:
        with self._database.read() as connection:
            row = handover_repo.get(connection, handover_id)
        return "—" if row is None else FLOW_LABELS.get(row.flow, str(row.flow))

    def _item_names(self, now: datetime) -> dict[str, str]:
        return {item.item_id: item.name for item in self._catalog.items(covering(to_tehran(now)))}
