"""پاداش end to end (S01 §5, SHF-5): the rate table, the accrual, the ledger and the standing.

The manager sets an amount per work type per month; the service reads the accounting mirrors and
appends one `reward.earned` per verifiable work, idempotently, with the amount of the month the work
happened in frozen into the record (a later rate change never rewrites an issued reward). The work
identity is the idempotency key — `reward:vital:<injection_id>`, `reward:registration:<patient_id>` —
so running the accrual twice writes nothing the second time and a rate set afterwards still lets the
same work accrue. Work of a type with no rate for that month writes nothing and comes back through
`unrated()`; an accounting user the manager has not mapped to a staff row earns nothing and comes
back through `unmapped()` (decision 4: never invent a person). The actor of these records is the
shift runtime, not a person — which is what lets a manager confirm them without ever confirming
himself — and the *earner* is the payload's `staff_id`.

The ledger is derived, like `open_decisions` and `my_queue`: it is read from the log on every call
and never stored, so `rebuild` stays exactly the two projections the clinic keeps. All money
arithmetic lives in `domain/rewards.py`; this module only reads, writes and audits. Nothing here
writes to accounting, and no real database is ever opened (`_data/` is forbidden, ADR-006).
"""

import logging
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.accounting import views
from shift.adapters.db import reward_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.reward_repo import RewardEventRow, RewardRateRow, TeamGoalRow, WorkRow
from shift.common.clock import Clock, SystemClock, parse_iso, to_tehran, utc_iso
from shift.common.ids import new_ulid
from shift.domain.policy import PERSIAN_DIGITS, ShiftKey
from shift.domain.rewards import (
    Payroll,
    PayrollLine,
    RewardLine,
    Standing,
    WorkType,
    deduction_month,
    month_of,
    net_toman,
    standing_of,
)
from shift.domain.shift_clock import covering
from shift.domain.shift_events import APPROVED
from shift.services.audit import AuditService, record_action
from shift.services.auth import Principal
from shift.services.campaigns import MAX_AMOUNT_TOMAN
from shift.services.event_store import EventStore, NewEvent

log = logging.getLogger(__name__)

# The service writes the earning records, so the log names the clinic's runtime as their actor: the
# manager's confirmation then never closes a record of the manager's own (SHF-3 rule E-12).
SYSTEM_ACTOR = "shift-runtime"
# Only the two automatic works have a rate; an assigned task is credited with its own amount.
RATE_WORK_TYPES = (WorkType.VITALS.value, WorkType.REGISTRATION.value)
PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"
REVOKED = "revoked"

NO_RATE_WORK_TYPE = "نرخ پاداش فقط برای کار خودکار (قند و فشار و ثبت بیمار) تعیین می‌شود."
BAD_MONTH = "ماه باید به شکل ۱۴۰۵-۰۸ نوشته شود."
NOT_POSITIVE = "مبلغ پاداش باید بیشتر از صفر باشد."
TOO_LARGE = f"مبلغ پاداش بیشتر از {MAX_AMOUNT_TOMAN:,} تومان است؛ این عدد اشتباه است."
BAD_GOAL = "هدف تیمی باید یک عدد درست و بیشتر از صفر باشد."
GOAL_TOO_LARGE = "هدف تیمی بیشتر از ۱۰۰٬۰۰۰ کار است؛ این عدد اشتباه است."
MAX_GOAL_WORKS = 100_000
UNKNOWN_STAFF = "این کارمند پیدا نشد."
ACCOUNT_TAKEN = "این شناسهٔ حسابداری پیش‌تر به کارمند دیگری وصل شده است."

# A month key is the one shape every caller may send; a form may also carry Persian digits.
_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_SEPARATORS = str.maketrans("", "", "،,٬ ")


class RewardError(ValueError):
    """The rate, the mapping or the amount is not one the clinic could mean."""


@dataclass(frozen=True, slots=True)
class RewardRate:
    """The amount one work type is worth in one month, and who set it when."""

    work_type: str
    month: str
    amount_toman: int
    set_by: str
    set_at: str


@dataclass(frozen=True, slots=True)
class TeamGoal:
    """One month's team goal as the screens read it: the number and who set it when."""

    month: str
    goal_works: int
    set_by: str
    set_at: str


@dataclass(frozen=True, slots=True)
class UnratedWork:
    """One work type of one month whose works earned nothing because no rate was set."""

    work_type: str
    month: str
    works: int


@dataclass(frozen=True, slots=True)
class UnmappedEarner:
    """One accounting user of one month that no staff row claims, with how many works wait."""

    acc_user: str
    work_type: str
    month: str
    works: int


@dataclass(frozen=True, slots=True)
class RewardRecord:
    """One reward record as a page reads it; a revoked record's amount is the deduction (negative)."""

    event_id: str
    staff_id: str
    staff_name: str
    work_type: str
    source: str
    amount_toman: int
    at_tehran: str
    note: str | None
    status: str


@dataclass(frozen=True, slots=True)
class AccrualReport:
    """What one accrual run did: how much it wrote, how much it already had, and what it skipped."""

    month: str
    created: int
    replayed: int
    unrated: int
    unmapped: int


@dataclass(frozen=True, slots=True)
class AccountingCopy:
    """What the accounting copy holds for one month, so the manager can see the app read it."""

    month: str
    vitals: int
    registrations: int
    any_rows: bool


@dataclass(frozen=True, slots=True)
class StaffOption:
    """One active staff row and the accounting user it is mapped to (None while unmapped)."""

    staff_id: str
    full_name: str
    acc_user: str | None


def _month(value: object) -> str:
    """A Jalali month key as a URL or a form may carry it (`1405-08`), or a refusal."""
    text = str(value).strip().translate(PERSIAN_DIGITS)
    if _MONTH.fullmatch(text) is None:
        raise RewardError(BAD_MONTH)
    return text


def month_key(value: object, *, fallback: str | None = None) -> str:
    """The month a URL or a form asked for, or `fallback` when the caller sent no month at all."""
    try:
        return _month(value)
    except RewardError:
        if fallback is None:
            raise
        return fallback


def _rate_work_type(value: object) -> str:
    """The work type of one rate, which is never the manager's own assigned task."""
    text = str(value).strip()
    if text not in RATE_WORK_TYPES:
        raise RewardError(NO_RATE_WORK_TYPE)
    return text


def _amount(value: object) -> int:
    """A whole, positive Toman amount no larger than one reward may pay (Persian digits accepted)."""
    if isinstance(value, bool) or isinstance(value, float):
        raise RewardError(NOT_POSITIVE)
    text = str(value).strip().translate(PERSIAN_DIGITS).translate(_SEPARATORS)
    if not text.isdigit():
        raise RewardError(NOT_POSITIVE)
    amount = int(text)
    if amount <= 0:
        raise RewardError(NOT_POSITIVE)
    if amount > MAX_AMOUNT_TOMAN:
        raise RewardError(TOO_LARGE)
    return amount


def _work_month(work: WorkRow) -> str | None:
    """The Jalali month a work happened in; the bridge copies accounting's own timestamp format."""
    try:
        return month_of(views.parse_time(work.done_at))
    except ValueError:
        return None


def _record(row: RewardEventRow, at_tehran: str, status: str, names: Mapping[str, str]) -> RewardRecord:
    """One stored reward payload as a page reads it: names, not ids, and no work id at all."""
    staff_id = str(row.payload.get("staff_id") or "")
    return RewardRecord(
        event_id=row.event_id,
        staff_id=staff_id,
        staff_name=names.get(staff_id, staff_id),
        work_type=str(row.payload.get("work_type") or ""),
        source=str(row.payload.get("source") or row.payload.get("work_type") or ""),
        amount_toman=int(row.payload.get("amount_toman") or 0),
        at_tehran=at_tehran,
        note=None if row.payload.get("note") is None else str(row.payload["note"]),
        status=status,
    )


class RewardsService:
    """Sets rates, accrues the automatic works, reads the ledger and audits every manager change."""

    def __init__(
        self,
        database: Database,
        events: EventStore,
        *,
        clock: Clock | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self._database = database
        self._events = events
        self._clock = clock or SystemClock()
        # The lifespan injects the durable audit trail; a unit test without one keeps the sink.
        self._audit = audit

    # --- the manager's rates ---------------------------------------------------------------

    def rates(self, month: object) -> list[RewardRate]:
        """Every rate set for one month, by work type; a month with no rate has none at all."""
        key = _month(month)
        with self._database.read() as connection:
            return [_rate(row) for row in reward_repo.rates(connection, key)]

    def set_rate(self, work_type: object, month: object, amount_toman: object, *, principal: Principal) -> RewardRate:
        """Set (or fix) what one work type is worth in one month; the same month replaces the amount."""
        kind = _rate_work_type(work_type)
        key = _month(month)
        amount = _amount(amount_toman)
        values = {
            "work_type": kind,
            "month": key,
            "amount_toman": amount,
            "set_by": principal.staff_id,
            "set_at": utc_iso(self._clock.now_utc()),
        }
        with self._database.write() as connection:
            row = reward_repo.upsert_rate(connection, values)
        self._note(
            "reward.rate_set",
            "reward.rate_set %s %s=%d",
            (kind, key, amount),
            actor=principal.staff_id,
            subject=key,
            detail={"work_type": kind, "month": key, "amount_toman": amount},
        )
        return _rate(row)

    # --- the monthly team goal (decision 7) --------------------------------------------------

    def team_goal(self, month: object) -> TeamGoal | None:
        """The team goal the manager set for one month, or None while the month has none."""
        key = _month(month)
        with self._database.read() as connection:
            row = reward_repo.team_goal(connection, key)
        return None if row is None else _goal(row)

    def set_team_goal(self, month: object, goal_works: object, *, principal: Principal) -> TeamGoal:
        """Set (or fix) the one monthly team-goal number the competition shows; display only."""
        key = _month(month)
        goal = _goal_number(goal_works)
        values = {
            "month": key,
            "goal_works": goal,
            "set_by": principal.staff_id,
            "set_at": utc_iso(self._clock.now_utc()),
        }
        with self._database.write() as connection:
            row = reward_repo.set_team_goal(connection, values)
        self._note(
            "reward.goal_set",
            "reward.goal_set %s goal=%d",
            (key, goal),
            actor=principal.staff_id,
            subject=key,
            detail={"month": key, "goal_works": goal},
        )
        return _goal(row)

    # --- the automatic works ----------------------------------------------------------------

    def accrue(self, month: object) -> AccrualReport:
        """Append one earning record per verifiable work of that month, once, at that month's rate."""
        key = _month(month)
        moment = self._clock.now_utc()
        shift = covering(to_tehran(moment))
        with self._database.read() as connection:
            rates = {row.work_type: row.amount_toman for row in reward_repo.rates(connection, key)}
            works = self._works(connection, key)
        planned: list[NewEvent] = []
        unrated: Counter[str] = Counter()
        unmapped: Counter[tuple[str, str]] = Counter()
        for work in works:
            if work.staff_id is None:
                unmapped[(work.acc_user or "", work.source)] += 1
            elif work.source not in rates:
                unrated[work.source] += 1
            else:
                planned.append(_earning(work, rates[work.source], moment=moment, shift=shift))
        results = self._events.append_many(planned) if planned else []
        created = sum(1 for result in results if result.created)
        report = AccrualReport(
            month=key,
            created=created,
            replayed=len(planned) - created,
            unrated=sum(unrated.values()),
            unmapped=sum(unmapped.values()),
        )
        self._note(
            "reward.accrued",
            "reward.accrued %s created=%d replayed=%d unrated=%d unmapped=%d",
            (key, report.created, report.replayed, report.unrated, report.unmapped),
            actor=SYSTEM_ACTOR,
            subject=key,
            detail={"month": key, "created": created, "unrated": report.unrated, "unmapped": report.unmapped},
        )
        return report

    def unrated(self, month: object) -> list[UnratedWork]:
        """The works of one month that earned nothing because their work type had no rate."""
        key = _month(month)
        with self._database.read() as connection:
            rated = {row.work_type for row in reward_repo.rates(connection, key)}
            works = self._works(connection, key)
        counts = Counter(work.source for work in works if work.staff_id is not None and work.source not in rated)
        return [UnratedWork(work_type, key, works) for work_type, works in sorted(counts.items())]

    def unmapped(self, month: object) -> list[UnmappedEarner]:
        """The accounting users of one month that no staff row claims, per user and work type."""
        key = _month(month)
        with self._database.read() as connection:
            works = self._works(connection, key)
        counts = Counter((work.acc_user or "", work.source) for work in works if work.staff_id is None)
        return [
            UnmappedEarner(acc_user, work_type, key, works) for (acc_user, work_type), works in sorted(counts.items())
        ]

    def copied(self, month: object) -> AccountingCopy:
        """What the accounting copy holds for that month: the state the manager needs to trust it."""
        key = _month(month)
        with self._database.read() as connection:
            everything = self._all_works(connection)
        monthly = [work for work in everything if _work_month(work) == key]
        return AccountingCopy(
            month=key,
            vitals=sum(1 for work in monthly if work.source == reward_repo.VITALS),
            registrations=sum(1 for work in monthly if work.source == reward_repo.REGISTRATION),
            any_rows=bool(everything),
        )

    # --- the ledger -------------------------------------------------------------------------

    def records(self, month: object) -> list[RewardRecord]:
        """The reward records paid or revoked in one month, oldest first, with names."""
        key = _month(month)
        with self._database.read() as connection:
            return _month_records(reward_repo.timeline(connection), reward_repo.names(connection), key)

    def pending(self) -> list[RewardRecord]:
        """Every earning record no manager has decided yet, whatever month it belongs to."""
        with self._database.read() as connection:
            timeline = reward_repo.timeline(connection)
            names = reward_repo.names(connection)
        decided = {row.ref_event_id for row in timeline if row.type in reward_repo.DECISIONS}
        return [
            _record(row, row.at_tehran, PENDING, names)
            for row in timeline
            if row.type == reward_repo.REWARD_EARNED and row.event_id not in decided
        ]

    def payroll(self, month: object) -> Payroll:
        """One month of the payroll: confirmations paid in it, and the deductions due in it."""
        key = _month(month)
        records = self.records(key)
        confirmed = [record for record in records if record.status == CONFIRMED]
        revoked = [record for record in records if record.status == REVOKED]
        earned = sum(record.amount_toman for record in confirmed)
        deducted = sum(-record.amount_toman for record in revoked)
        lines = tuple(_line(record) for record in (*confirmed, *revoked))
        return Payroll(
            month=key,
            lines=lines,
            earned_toman=earned,
            deducted_toman=deducted,
            net_toman=net_toman(earned, deducted),
        )

    def table(self, month: object) -> list[PayrollLine]:
        """The manager's full table for one month: every person's line, earned and deducted."""
        return list(self.payroll(month).lines)

    def standing(self, staff_id: str, month: object) -> Standing:
        """One person's own screen: own amount, own count, own rank and the team goal."""
        key = _month(month)
        records = self.records(key)
        confirmed = [
            RewardLine(record.staff_id, record.work_type, record.amount_toman, parse_iso(record.at_tehran))
            for record in records
            if record.status == CONFIRMED
        ]
        revoked = [
            RewardLine(record.staff_id, record.work_type, -record.amount_toman, parse_iso(record.at_tehran))
            for record in records
            if record.status == REVOKED
        ]
        with self._database.read() as connection:
            names = reward_repo.names(connection)
            goal = reward_repo.team_goal(connection, key)
        return standing_of(
            staff_id,
            names.get(staff_id, staff_id),
            key,
            confirmed,
            revoked,
            None if goal is None else goal.goal_works,
        )

    # --- the accounting identity -------------------------------------------------------------

    def map_staff(self, staff_id: str, acc_user: str | None, *, principal: Principal) -> None:
        """Map one staff row to the accounting user that did the work, or clear the mapping."""
        mapped = str(acc_user or "").strip() or None
        with self._database.write() as connection:
            if staff_repo.find(connection, staff_id) is None:
                raise RewardError(UNKNOWN_STAFF)
            if mapped is not None:
                owner = reward_repo.acc_user_owner(connection, mapped)
                if owner is not None and owner != staff_id:
                    raise RewardError(ACCOUNT_TAKEN)
            reward_repo.set_acc_user(connection, staff_id, mapped)
        self._note(
            "reward.staff_map",
            "reward.staff_map staff=%s acc_user=%s",
            (staff_id, mapped),
            actor=principal.staff_id,
            subject=staff_id,
            detail={"staff_id": staff_id, "acc_user": mapped},
        )

    def staff_options(self) -> list[StaffOption]:
        """Every active staff row and its accounting user: the list the mapping form chooses from."""
        with self._database.read() as connection:
            mapped = reward_repo.acc_users(connection)
            people = staff_repo.list_with_roles(connection)
        return [
            StaffOption(person.staff.staff_id, person.staff.full_name, mapped.get(person.staff.staff_id))
            for person in people
            if person.staff.active
        ]

    # --- reads shared by the calls above -----------------------------------------------------

    def _works(self, connection: Connection, month: str) -> list[WorkRow]:
        """The works accounting recorded in one Jalali month, mapped or not."""
        return [work for work in self._all_works(connection) if _work_month(work) == month]

    @staticmethod
    def _all_works(connection: Connection) -> list[WorkRow]:
        """Every verifiable work of both mirrors, in the order accounting recorded them."""
        return [*reward_repo.vital_works(connection), *reward_repo.registration_works(connection)]

    def _note(
        self,
        action: str,
        message: str,
        message_args: tuple[object, ...],
        *,
        actor: str,
        subject: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        """Audit one reward action: a durable row plus the audit sink, never the public log."""
        record_action(
            self._audit,
            log,
            action=action,
            actor=actor,
            subject=subject,
            detail=detail,
            message=message,
            message_args=message_args,
        )


def _goal_number(value: object) -> int:
    """A whole, positive number of works, no larger than a month could ever hold (Persian digits ok)."""
    if isinstance(value, bool) or isinstance(value, float):
        raise RewardError(BAD_GOAL)
    text = str(value).strip().translate(PERSIAN_DIGITS).translate(_SEPARATORS)
    if not text.isdigit() or int(text) <= 0:
        raise RewardError(BAD_GOAL)
    if int(text) > MAX_GOAL_WORKS:
        raise RewardError(GOAL_TOO_LARGE)
    return int(text)


def _goal(row: TeamGoalRow) -> TeamGoal:
    """One stored team goal as the screens read it."""
    return TeamGoal(month=row.month, goal_works=row.goal_works, set_by=row.set_by, set_at=row.set_at)


def _rate(row: RewardRateRow) -> RewardRate:
    return RewardRate(
        work_type=row.work_type,
        month=row.month,
        amount_toman=row.amount_toman,
        set_by=row.set_by,
        set_at=row.set_at,
    )


def _line(record: RewardRecord) -> PayrollLine:
    """One ledger line; a revoked record carries its negative amount and the reason it was taken back."""
    return PayrollLine(
        staff_id=record.staff_id,
        staff_name=record.staff_name,
        work_type=record.work_type,
        source=REVOKED if record.status == REVOKED else record.source,
        amount_toman=record.amount_toman,
        at_tehran=record.at_tehran,
        note=record.note,
    )


def _earning(work: WorkRow, amount_toman: int, *, moment: datetime, shift: ShiftKey) -> NewEvent:
    """One earning record: the work identity is the idempotency key, so a second run writes nothing."""
    return NewEvent(
        client_event_id=new_ulid(moment),
        type=reward_repo.REWARD_EARNED,
        payload={
            "staff_id": work.staff_id,
            "work_type": work.source,
            "amount_toman": amount_toman,
            "source": work.source,
            "source_ref": work.source_ref,
        },
        actor_id=SYSTEM_ACTOR,
        device_id=SYSTEM_ACTOR,
        work_date=shift.work_date,
        client_at=moment,
        shift_id=shift.text,
        idempotency_key=f"reward:{work.source}:{work.source_ref}",
    )


def _month_records(timeline: list[RewardEventRow], names: Mapping[str, str], month: str) -> list[RewardRecord]:
    """The records one month pays or takes back: by the confirmation month, and by deduction month."""
    decisions = {row.ref_event_id: row for row in timeline if row.type in reward_repo.DECISIONS}
    targets = (reward_repo.REWARD_EARNED, reward_repo.REWARD_CONFIRMED)
    rewards = {row.event_id: row for row in timeline if row.type in targets}
    records: list[RewardRecord] = []
    for row in timeline:
        if row.type == reward_repo.REWARD_EARNED:
            decision = decisions.get(row.event_id)
            if decision is None or month_of(parse_iso(decision.at_tehran)) != month:
                continue
            status = CONFIRMED if decision.type == APPROVED else REJECTED
            records.append(_record(row, decision.at_tehran, status, names))
        elif row.type == reward_repo.REWARD_CONFIRMED and month_of(parse_iso(row.at_tehran)) == month:
            records.append(_record(row, row.at_tehran, CONFIRMED, names))
        elif row.type == reward_repo.REWARD_REVOKED and deduction_month(parse_iso(row.at_tehran)) == month:
            target = rewards.get(str(row.ref_event_id))
            if target is not None:
                revoked = _record(target, row.at_tehran, REVOKED, names)
                records.append(
                    replace(revoked, amount_toman=-revoked.amount_toman, note=str(row.payload.get("reason") or ""))
                )
    return sorted(records, key=lambda record: (record.at_tehran, record.event_id))
