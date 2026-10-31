"""پاداش end to end (S01 §5, SHF-5): rate, accrual, confirmation, payslip, revocation.

Money, so the whole journey runs through the real services: the manager's rate, the automatic accrual
from the accounting copy, SHF-3's `event.approved` as the confirmation, the payslip of the
confirmation month, and a revocation that is deducted in the next month and never in the one already
paid. The module also pins what must not earn — unrated work, an accounting user nobody mapped, a
registration without both checks — who may confirm (the manager only) and that the accountant may
read the payroll without being able to close anything.

The accounting mirrors are filled directly with invented rows, so nothing here opens a real
database or touches patient data (ADR-006, `_data/` is forbidden).
"""

from datetime import date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, SPARE, Seeded, seed_roster
from clocks import FixedClock

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN
from shift.common.ids import new_ulid
from shift.domain.permissions import permissions_for
from shift.domain.policy import ShiftKey
from shift.services.audit import AuditService
from shift.services.auth import Principal
from shift.services.campaigns import MAX_AMOUNT_TOMAN
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission
from shift.services.projections import ProjectionEngine
from shift.services.rewards import RewardError, RewardsService, UnratedWork
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventRuleError, ShiftEventService

WORK_DATE = date(2026, 10, 28)
SHIFT = ShiftKey(WORK_DATE, 1)
# 1405/08/06 on the clinic's clock, half an hour into the morning shift.
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=TEHRAN)
LATER = datetime(2026, 10, 29, 10, 0, tzinfo=TEHRAN)
MONTH = "1405-08"
NEXT_MONTH = "1405-09"
VITALS_RATE = 50_000
REGISTRATION_RATE = 20_000
NURSE_ACC = "31"
OTHER_ACC = "32"
RECEPTION_ACC = "reception-user-7"
NOBODY_ACC = "99"
APPROVED = "event.approved"
REVOKED = "reward.revoked"


def _principal(person: Seeded) -> Principal:
    """The session a route would hand the service for one synthetic staff member."""
    return Principal(
        staff_id=person.staff_id,
        full_name=person.full_name,
        roles=person.roles,
        permissions=permissions_for(person.roles),
        device_id=person.device_id,
        device_label="گوشی آزمایشی",
        session_hash=f"session-{person.staff_id}",
        csrf_token=f"csrf-{person.staff_id}",
        expires_at=datetime(2026, 10, 30, 23, 0, tzinfo=TEHRAN),
    )


MANAGER = _principal(ROLES["manager"])
NURSE = _principal(ROLES["nurse"])
RECEPTION = _principal(ROLES["reception"])
ACCOUNTANT = _principal(ROLES["accountant"])


def _vital(database: Database, *, injection_id: int, acc_user: str, closed_at: str = "2026-10-27 09:15:00") -> None:
    """One sugar/BP service as accounting's own view reports it, stamped in Tehran local time."""
    with database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO acc_vital (injection_id, invoice_id, nurse_id, kind, closed_at, ingested_at) "
            "VALUES (?, 1, ?, 'sugar', ?, ?)",
            (injection_id, int(acc_user), closed_at, closed_at),
        )


def _registration(
    database: Database,
    *,
    patient_id: int,
    acc_user: str,
    created_at: str = "2026-10-27 10:00:00",
    valid: bool = True,
) -> None:
    """One registration; `valid` is the national-ID and mobile check the owner asks for."""
    with database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO acc_registration "
            "(patient_id, created_by, created_at, has_valid_nid, has_valid_phone, is_foreign, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (patient_id, acc_user, created_at, int(valid), int(valid), created_at),
        )


@pytest.fixture()
def clock() -> FixedClock:
    """One clock for the store and the service, so a test can move the clinic's month forward."""
    return FixedClock(NOW)


@pytest.fixture()
def events(database: Database, clock: FixedClock) -> EventStore:
    return EventStore(database, clock=clock)


@pytest.fixture()
def service(database: Database, events: EventStore, clock: FixedClock) -> RewardsService:
    return RewardsService(database, events, clock=clock)


@pytest.fixture()
def approvals(database: Database, events: EventStore, clock: FixedClock) -> ShiftEventService:
    """The real approval flow of SHF-3, which closes a reward record; no second endpoint exists."""
    return ShiftEventService(
        database,
        events,
        ProjectionEngine(database),
        CatalogService(database),
        SettingsService(database),
        clock=clock,
    )


@pytest.fixture()
def roster(database: Database, service: RewardsService) -> Database:
    """A synthetic roster whose nurse and receptionist already know their accounting user."""
    seed_roster(database)
    service.map_staff(ROLES["nurse"].staff_id, NURSE_ACC, principal=MANAGER)
    service.map_staff(ROLES["reception"].staff_id, RECEPTION_ACC, principal=MANAGER)
    return database


def _record(
    approvals: ShiftEventService,
    principal: Principal,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    target: str | None = None,
) -> Any:
    """One record written through the shift-event service, decision or not, as the screens write it."""
    event_id = new_ulid(NOW)
    return approvals.record(
        EventSubmission(
            type=event_type,
            payload=payload or {},
            actor_id=principal.staff_id,
            device_id=principal.device_id,
            client_at=NOW,
            client_event_id=event_id,
            shift_id=SHIFT.text,
            ref_event_id=target,
            idempotency_key=event_id,
            shift_work_date=SHIFT.work_date,
        ),
        principal=principal,
        shift=SHIFT,
    )


def _decide(
    approvals: ShiftEventService,
    principal: Principal,
    event_type: str,
    target: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """One decision recorded the way a phone records it: through the shift-event service."""
    return _record(approvals, principal, event_type, payload, target=target)


def _logged(database: Database, event_type: str) -> int:
    """How many events of one type the log holds: "writes once" is read from the log itself."""
    with database.read() as connection:
        return sum(1 for event in events_repo.iter_events(connection) if event.type == event_type)


def _rates(service: RewardsService, *, vitals: int = VITALS_RATE, registration: int = REGISTRATION_RATE) -> None:
    service.set_rate("vitals", MONTH, vitals, principal=MANAGER)
    service.set_rate("registration", MONTH, registration, principal=MANAGER)


def test_the_whole_journey_pays_in_the_month_the_manager_confirmed(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    _rates(service)
    _vital(roster, injection_id=9001, acc_user=NURSE_ACC)
    _vital(roster, injection_id=9002, acc_user=NURSE_ACC)
    _registration(roster, patient_id=5001, acc_user=RECEPTION_ACC)

    report = service.accrue(MONTH)
    assert (report.created, report.unrated, report.unmapped) == (3, 0, 0)
    assert len(service.pending()) == 3
    assert service.payroll(MONTH).net_toman == 0, "a record earns nothing before it is confirmed"

    for record in service.pending():
        _decide(approvals, MANAGER, APPROVED, record.event_id)

    payroll = service.payroll(MONTH)

    assert (payroll.month, payroll.earned_toman, payroll.deducted_toman, payroll.net_toman) == (
        MONTH,
        2 * VITALS_RATE + REGISTRATION_RATE,
        0,
        2 * VITALS_RATE + REGISTRATION_RATE,
    )
    assert payroll.per_staff() == (
        (ROLES["nurse"].staff_id, ROLES["nurse"].full_name, 2 * VITALS_RATE),
        (ROLES["reception"].staff_id, ROLES["reception"].full_name, REGISTRATION_RATE),
    )
    assert service.payroll(NEXT_MONTH).net_toman == 0


def test_a_second_accrual_writes_nothing_and_a_later_rate_lets_the_work_accrue(
    roster: Database, service: RewardsService
) -> None:
    _vital(roster, injection_id=9101, acc_user=NURSE_ACC)

    first = service.accrue(MONTH)
    assert (first.created, first.unrated) == (0, 1)
    assert service.unrated(MONTH) == [UnratedWork("vitals", MONTH, 1)]

    service.set_rate("vitals", MONTH, VITALS_RATE, principal=MANAGER)
    second = service.accrue(MONTH)
    third = service.accrue(MONTH)

    assert (second.created, second.replayed) == (1, 0)
    assert (third.created, third.replayed) == (0, 1)
    assert _logged(roster, "reward.earned") == 1
    assert service.unrated(MONTH) == []


def test_an_accounting_user_nobody_mapped_earns_nothing_and_is_listed(
    roster: Database, service: RewardsService
) -> None:
    _rates(service)
    _vital(roster, injection_id=9201, acc_user=NOBODY_ACC)

    report = service.accrue(MONTH)

    assert (report.created, report.unmapped) == (0, 1)
    assert [(entry.acc_user, entry.work_type, entry.works) for entry in service.unmapped(MONTH)] == [
        (NOBODY_ACC, "vitals", 1)
    ]
    assert service.unrated(MONTH) == [], "an unmapped earner is an identity problem, not a rate problem"
    assert service.pending() == []


def test_a_registration_without_both_checks_earns_nothing(roster: Database, service: RewardsService) -> None:
    _rates(service)
    _registration(roster, patient_id=9301, acc_user=RECEPTION_ACC, valid=False)

    report = service.accrue(MONTH)

    assert (report.created, report.unrated, report.unmapped) == (0, 0, 0)
    assert service.copied(MONTH).registrations == 0


def test_the_rate_of_the_month_the_work_happened_in_is_frozen_into_the_record(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    _rates(service, vitals=VITALS_RATE)
    _vital(roster, injection_id=9401, acc_user=NURSE_ACC)
    _vital(roster, injection_id=9402, acc_user=NURSE_ACC, closed_at="2026-11-25 09:00:00")  # 1405-09

    assert service.accrue(MONTH).created == 1, "the next month's work is not this month's accrual"
    service.set_rate("vitals", MONTH, 90_000, principal=MANAGER)
    assert service.accrue(MONTH).created == 0, "an issued record is already written"

    for record in service.pending():
        _decide(approvals, MANAGER, APPROVED, record.event_id)

    assert [line.amount_toman for line in service.table(MONTH)] == [VITALS_RATE], "the issued amount is frozen"
    service.set_rate("vitals", NEXT_MONTH, 70_000, principal=MANAGER)
    assert service.accrue(NEXT_MONTH).created == 1
    assert service.payroll(NEXT_MONTH).net_toman == 0, "nothing is paid before its confirmation"


def test_a_revocation_is_deducted_in_the_next_month_and_never_in_the_original_one(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    _rates(service)
    _vital(roster, injection_id=9501, acc_user=NURSE_ACC)
    service.accrue(MONTH)
    earned = service.pending()[0]
    _decide(approvals, MANAGER, APPROVED, earned.event_id)
    assert service.payroll(MONTH).net_toman == VITALS_RATE

    _decide(approvals, MANAGER, REVOKED, earned.event_id, {"reason": "ثبت جعلی بود"})

    assert service.payroll(MONTH).net_toman == VITALS_RATE, "the month already paid is never rewritten"
    deduction = service.payroll(NEXT_MONTH)
    assert (deduction.earned_toman, deduction.deducted_toman, deduction.net_toman) == (0, VITALS_RATE, -VITALS_RATE)
    assert [line.source for line in deduction.lines] == ["revoked"]
    assert deduction.lines[0].note == "ثبت جعلی بود"


def test_a_pending_record_is_never_revoked(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    _rates(service)
    _vital(roster, injection_id=9601, acc_user=NURSE_ACC)
    service.accrue(MONTH)

    with pytest.raises(ShiftEventRuleError):
        _decide(approvals, MANAGER, REVOKED, service.pending()[0].event_id, {"reason": "ثبت جعلی بود"})


def test_a_receptionist_cannot_confirm_and_reads_only_their_own_standing(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    _rates(service)
    _vital(roster, injection_id=9701, acc_user=NURSE_ACC)
    _vital(roster, injection_id=9703, acc_user=NURSE_ACC)
    _registration(roster, patient_id=9702, acc_user=RECEPTION_ACC)
    service.accrue(MONTH)
    open_nurse_records = [entry for entry in service.pending() if entry.staff_id == ROLES["nurse"].staff_id]
    _decide(approvals, MANAGER, APPROVED, open_nurse_records[0].event_id)

    with pytest.raises(ShiftEventRuleError):
        _decide(approvals, RECEPTION, APPROVED, open_nurse_records[1].event_id)

    standing = service.standing(ROLES["reception"].staff_id, MONTH)

    assert (standing.staff_name, standing.own_toman, standing.own_works) == (ROLES["reception"].full_name, 0, 0)
    assert VITALS_RATE not in [standing.own_toman, standing.own_works, standing.team_works]
    assert ROLES["nurse"].full_name not in str(standing)


def test_the_accountant_reads_the_payroll_but_cannot_confirm(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    _rates(service)
    _vital(roster, injection_id=9801, acc_user=NURSE_ACC)
    service.accrue(MONTH)
    record = service.pending()[0]

    with pytest.raises(ShiftEventRuleError):
        _decide(approvals, ACCOUNTANT, APPROVED, record.event_id)

    assert service.payroll(MONTH).per_staff() == ()
    _decide(approvals, MANAGER, APPROVED, record.event_id)
    assert service.payroll(MONTH).per_staff() == ((ROLES["nurse"].staff_id, ROLES["nurse"].full_name, VITALS_RATE),)


def test_a_manager_never_confirms_the_reward_his_own_work_earns(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    """Two people or no money: a manager who also works a shift cannot close his own record (P-05)."""
    _rates(service)
    service.map_staff(ROLES["manager"].staff_id, OTHER_ACC, principal=MANAGER)
    _vital(roster, injection_id=9901, acc_user=OTHER_ACC)
    service.accrue(MONTH)
    own = service.pending()[0]

    assert own.staff_id == ROLES["manager"].staff_id

    with pytest.raises(ShiftEventRuleError):
        _decide(approvals, MANAGER, APPROVED, own.event_id)

    assert service.payroll(MONTH).net_toman == 0, "his own record stays open for another manager"


def test_a_manager_never_credits_an_assigned_task_to_himself(
    roster: Database, service: RewardsService, approvals: ShiftEventService
) -> None:
    """`reward.confirmed` closes by itself, so the same hand that writes it must not be the earner's."""
    task = {"staff_id": ROLES["manager"].staff_id, "work_type": "manager_task", "amount_toman": 100_000}

    with pytest.raises(ShiftEventRuleError):
        _record(approvals, MANAGER, "reward.confirmed", task)
    assert _logged(roster, "reward.confirmed") == 0

    _record(approvals, MANAGER, "reward.confirmed", {**task, "staff_id": ROLES["nurse"].staff_id})

    assert service.payroll(MONTH).per_staff() == ((ROLES["nurse"].staff_id, ROLES["nurse"].full_name, 100_000),)


@pytest.mark.parametrize(
    ("work_type", "month", "amount"),
    [
        ("vitals", MONTH, -1),
        ("vitals", MONTH, 0),
        ("vitals", MONTH, MAX_AMOUNT_TOMAN + 1),
        ("manager_task", MONTH, 10_000),
        ("vitals", "1405-13", 10_000),
        ("vitals", "plenty", 10_000),
    ],
)
def test_a_refused_rate_is_never_stored(
    roster: Database, service: RewardsService, work_type: str, month: str, amount: object
) -> None:
    with pytest.raises(RewardError):
        service.set_rate(work_type, month, amount, principal=MANAGER)

    assert service.rates(MONTH) == []


def test_a_rate_is_read_back_per_month_and_a_later_amount_replaces_it(
    roster: Database, service: RewardsService
) -> None:
    service.set_rate("vitals", MONTH, "۵۰٬۰۰۰", principal=MANAGER)  # a manager may type Persian digits

    assert [(rate.work_type, rate.amount_toman) for rate in service.rates(MONTH)] == [("vitals", VITALS_RATE)]
    assert service.rates(NEXT_MONTH) == []


def test_the_accounting_mapping_is_audited_and_one_account_belongs_to_one_person(
    database: Database, clock: FixedClock, events: EventStore
) -> None:
    seed_roster(database)
    audit = AuditService(database, clock=clock)
    service = RewardsService(database, events, clock=clock, audit=audit)

    service.map_staff(SPARE.staff_id, OTHER_ACC, principal=MANAGER)
    with pytest.raises(RewardError):
        service.map_staff(ROLES["storekeeper"].staff_id, OTHER_ACC, principal=MANAGER)

    assert [(row.action, row.actor_id, row.subject_id) for row in audit.history(action="reward.staff_map")] == [
        ("reward.staff_map", MANAGER.staff_id, SPARE.staff_id)
    ]
