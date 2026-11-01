"""The accountant's queue (S01 §3 «ک»/«د»/«ب», owner document «کار حسابدار», SHF-5 F8).

The accountant owns money and price and never counts, and her queue is the open money work of the
clinic: a card-to-card receipt nobody has matched against the bank account yet, a withdrawal that left
the box and waits for the person the record named, a refund the reception asked for and the shift doctor
has not decided, and the month's reward list she adds to the payslip. Purchasing is SHF-4's, so this
queue has no section for it: a section whose data the app does not have is not rendered at all (M-07).

What is open is read from the log on every call — the kind waits for a decision and no `event.approved`
or `event.rejected` names it yet — whatever shift opened it (F2). Who may close which row is not decided
here: `ShiftEventService.pending_for` is the one authority for that, and a row carries `decidable` from
it, so the buttons on the page are exactly the decisions the phone's own endpoint would accept and no
second decision path can drift from the one SHF-3 tested.

No row carries a reference number, a blind count, or a shift's cash figure: it is the item itself, its
actor, its instant and its own amount (R-01, R-02, ADR-011). The withdrawal's cap is the versioned
setting that was in force at the record's own shift, read through `SettingsService`, never today's.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection

from shift.adapters.db import events_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock
from shift.domain.event_types import EventType
from shift.domain.policy import PolicyError, SettingKey, ShiftKey
from shift.domain.shift_events import DECISION_TYPES, kind_for, summary_for
from shift.domain.shift_events.cash_withdrawal import is_above_cap_payload
from shift.services.auth import Principal
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventService

if TYPE_CHECKING:  # the reward ledger is SHF-5 A's module; this one only reads its payroll
    from shift.domain.rewards import Payroll
    from shift.services.rewards import RewardsService

CARD_TRANSFER = EventType.CARD_TRANSFER_REPORTED.value
WITHDRAWN = EventType.CASH_WITHDRAWN.value
REFUND = EventType.REFUND_REQUESTED.value
# The three sections this stream owns, in the owner's order (purchasing belongs to SHF-4).
SECTION_TYPES = frozenset({CARD_TRANSFER, WITHDRAWN, REFUND})
# Every row of these sections is an item still waiting for somebody: a decided one leaves the queue.
PENDING = "pending"


@dataclass(frozen=True, slots=True)
class CardTransferLine:
    """«ک»: a receipt reception sent, waiting for the accountant to match it against the bank account."""

    event_id: str
    at_tehran: str
    actor_name: str
    admission_no: str
    amount_toman: int
    status: str
    decidable: bool
    # The receipt photo reception took (A5): the accountant matches it against the bank account.
    photo_sha256: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "at_tehran": self.at_tehran,
            "actor": self.actor_name,
            "admission_no": self.admission_no,
            "amount_toman": self.amount_toman,
            "status": self.status,
            "decidable": self.decidable,
            "photo_sha256": self.photo_sha256,
        }


@dataclass(frozen=True, slots=True)
class WithdrawalLine:
    """«د»: money that left the box, with the cap its own shift held and which side of it it was on."""

    event_id: str
    at_tehran: str
    actor_name: str
    recipient_name: str
    reason: str
    amount_toman: int
    cap_toman: int | None
    above_cap: bool | None
    status: str
    decidable: bool
    # The receipt of what the money bought (A5), shown beside the row it explains.
    photo_sha256: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "at_tehran": self.at_tehran,
            "actor": self.actor_name,
            "recipient": self.recipient_name,
            "reason": self.reason,
            "amount_toman": self.amount_toman,
            "cap_toman": self.cap_toman,
            "above_cap": self.above_cap,
            "status": self.status,
            "decidable": self.decidable,
            "photo_sha256": self.photo_sha256,
        }


@dataclass(frozen=True, slots=True)
class RefundLine:
    """«ب»: a refund the reception asked for; the shift doctor or the manager decides it (E-02)."""

    event_id: str
    at_tehran: str
    actor_name: str
    admission_no: str
    reason: str
    status: str
    decidable: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "at_tehran": self.at_tehran,
            "actor": self.actor_name,
            "admission_no": self.admission_no,
            "reason": self.reason,
            "status": self.status,
            "decidable": self.decidable,
        }


@dataclass(frozen=True, slots=True)
class PayrollRow:
    """One line of the month's reward ledger: who earned or lost how much, for what work, and when.

    `label` is the Persian name of the work type and `work_type` the key the ledger stores; an amount is
    negative on a deduction, exactly as the payroll answers it, so the page never flips a sign.
    """

    staff_name: str
    label: str
    work_type: str
    amount_toman: int
    at_tehran: str
    note: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "staff": self.staff_name,
            "label": self.label,
            "work_type": self.work_type,
            "amount_toman": self.amount_toman,
            "at_tehran": self.at_tehran,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class StaffPay:
    """One person's figure for the month: what the accountant writes on the payslip."""

    staff_name: str
    net_toman: int

    def to_payload(self) -> dict[str, Any]:
        return {"staff": self.staff_name, "net_toman": self.net_toman}


@dataclass(frozen=True, slots=True)
class PayrollView:
    """The month's reward payroll: the confirmed rewards and the deductions, and the net per person."""

    month: str
    lines: tuple[PayrollRow, ...]
    staff: tuple[StaffPay, ...]
    earned_toman: int
    deducted_toman: int
    net_toman: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "lines": [line.to_payload() for line in self.lines],
            "staff": [pay.to_payload() for pay in self.staff],
            "earned_toman": self.earned_toman,
            "deducted_toman": self.deducted_toman,
            "net_toman": self.net_toman,
        }


@dataclass(frozen=True, slots=True)
class AccountantQueue:
    """The money sections of the queue, oldest first, and the month's payroll when there is one."""

    card_transfers: tuple[CardTransferLine, ...]
    withdrawals: tuple[WithdrawalLine, ...]
    refunds: tuple[RefundLine, ...]
    payroll: PayrollView | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "card_transfers": [line.to_payload() for line in self.card_transfers],
            "withdrawals": [line.to_payload() for line in self.withdrawals],
            "refunds": [line.to_payload() for line in self.refunds],
            "payroll": None if self.payroll is None else self.payroll.to_payload(),
        }


class AccountantQueueService:
    """Reads the open money work of the clinic and the month's reward list."""

    def __init__(
        self,
        database: Database,
        *,
        events: ShiftEventService,
        settings: SettingsService,
        rewards: "RewardsService | None" = None,
        clock: Clock | None = None,
    ) -> None:
        self._database = database
        self._events = events
        self._settings = settings
        self._rewards = rewards
        self._clock = clock or SystemClock()

    def queue(self, principal: Principal, *, month: str | None = None) -> AccountantQueue:
        """The three money sections as `principal` may see them, plus the payroll of `month`.

        Every row of the log is read once, oldest first, so the record that waited longest comes first —
        a transfer nobody matched last week is exactly what the accountant must not miss in the morning.
        """
        decidable = {entry.event_id for entry in self._events.pending_for(principal)}
        with self._database.read() as connection:
            events = list(events_repo.iter_events(connection))
            names = _Names(connection)
            decided = {
                event.ref_event_id
                for event in events
                if event.type in DECISION_TYPES and event.ref_event_id is not None
            }
            caps: dict[str, int | None] = {}
            transfers: list[CardTransferLine] = []
            withdrawals: list[WithdrawalLine] = []
            refunds: list[RefundLine] = []
            for event in events:
                kind = kind_for(event.type)
                if kind is None or event.type not in SECTION_TYPES or event.event_id in decided:
                    continue
                summary = summary_for(kind, event.payload)
                is_decidable = event.event_id in decidable
                if event.type == CARD_TRANSFER:
                    transfers.append(
                        CardTransferLine(
                            event_id=event.event_id,
                            at_tehran=event.at_tehran,
                            actor_name=names.name(event.actor_id),
                            admission_no=str(summary.get("admission_no") or ""),
                            amount_toman=int(summary.get("amount") or 0),
                            status=PENDING,
                            decidable=is_decidable,
                            photo_sha256=event.payload.get("photo_sha256"),
                        )
                    )
                elif event.type == WITHDRAWN:
                    amount = int(summary.get("amount") or 0)
                    cap = self._cap(event, caps)
                    withdrawals.append(
                        WithdrawalLine(
                            event_id=event.event_id,
                            at_tehran=event.at_tehran,
                            actor_name=names.name(event.actor_id),
                            recipient_name=names.name(str(summary.get("recipient_id") or "")),
                            reason=str(summary.get("reason") or ""),
                            amount_toman=amount,
                            cap_toman=cap,
                            # ADR-019: the cap counts a whole shift, so the row asks the stored record's
                            # own payload instead of re-doing the running-total arithmetic here.
                            above_cap=is_above_cap_payload(event.payload),
                            status=PENDING,
                            decidable=is_decidable,
                            photo_sha256=event.payload.get("photo_sha256"),
                        )
                    )
                else:
                    refunds.append(
                        RefundLine(
                            event_id=event.event_id,
                            at_tehran=event.at_tehran,
                            actor_name=names.name(event.actor_id),
                            admission_no=str(summary.get("admission_no") or ""),
                            reason=str(summary.get("reason") or ""),
                            status=PENDING,
                            decidable=is_decidable,
                        )
                    )
        return AccountantQueue(
            card_transfers=tuple(transfers),
            withdrawals=tuple(withdrawals),
            refunds=tuple(refunds),
            payroll=self._payroll(month),
        )

    def _cap(self, event: EventRecord, caps: dict[str, int | None]) -> int | None:
        """The withdrawal cap the clinic's settings held at that record's own shift, once per shift.

        A row stored without a readable shift key has no cap to compare against, and the queue says so
        rather than judging it by today's figure (R-09: a setting binds only the shifts after it).
        """
        key = event.shift_id or ""
        if key not in caps:
            try:
                caps[key] = self._settings.number(SettingKey.WITHDRAWAL_CAP_TOMAN, ShiftKey.parse(key))
            except PolicyError:
                caps[key] = None
        return caps[key]

    def _payroll(self, month: str | None) -> PayrollView | None:
        """The month's payroll from the reward ledger, or None while no ledger is wired to this app."""
        if self._rewards is None:
            return None
        closing = month or _month_of(self._clock.now_utc())
        return _payroll_view(closing, self._rewards.payroll(closing))


class _Names:
    """Names for one read; a person the roster no longer holds is shown by their id."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._read: dict[str, str] = {}

    def name(self, staff_id: str) -> str:
        if staff_id not in self._read:
            person = staff_repo.find(self._connection, staff_id)
            self._read[staff_id] = staff_id if person is None else person.full_name
        return self._read[staff_id]


def _payroll_view(month: str, payroll: "Payroll") -> PayrollView:
    """The rewards service's payroll as the accountant reads it, with every line labelled in Persian."""
    return PayrollView(
        month=month,
        lines=tuple(
            PayrollRow(
                staff_name=line.staff_name,
                label=_work_type_label(line.work_type),
                work_type=line.work_type,
                amount_toman=line.amount_toman,
                at_tehran=line.at_tehran,
                note=line.note,
            )
            for line in payroll.lines
        ),
        staff=tuple(StaffPay(staff_name=name, net_toman=net) for _staff_id, name, net in payroll.per_staff()),
        earned_toman=payroll.earned_toman,
        deducted_toman=payroll.deducted_toman,
        net_toman=payroll.net_toman,
    )


def _work_type_label(work_type: str) -> str:
    """The Persian label of one work type, from the one place that publishes them.

    The import sits here and not at module import time, so the accountant's money sections never depend
    on the reward ledger being importable. A key the ledger does not know reads as itself, which is an
    honest label for a row whose Persian name nobody published.
    """
    from shift.domain.rewards import WORK_TYPE_LABELS

    return WORK_TYPE_LABELS.get(work_type, work_type)


def _month_of(moment: datetime) -> str:
    """The Jalali month an instant falls in, as the reward ledger spells it (`1405-08`)."""
    from shift.domain.rewards import month_of

    return month_of(moment)
