"""The money kinds and the decision events that close them (S01 §3: E-01, E-02, E-03, E-12).

Money is the one group where a quiet bug costs the clinic: a reported transfer the accountant never
sees, a refund approved by someone the owner did not name, a decision written twice or in the wrong
shift, a discount taken off a campaign that is not running. Each case here runs through the real
`ShiftEventService` on a migrated synthetic database, so the kind rule, the permission, the approval
gate and the projection are exercised together. Nothing reads a production database and every value
is invented (ADR-006).
"""

from datetime import date, timedelta
from typing import Any

import pytest
from clocks import FixedClock, inside_shift

from event_fixtures import build_event
from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.ids import new_ulid
from shift.domain.permissions import Role, permissions_for
from shift.domain.policy import ShiftKey
from shift.domain.shift_events import kind_for, summary_for
from shift.services.auth import Principal
from shift.services.campaigns import CampaignService
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, SubmissionResult
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventRuleError, ShiftEventService

WORK_DATE = date(2026, 10, 28)
SHIFT = ShiftKey(WORK_DATE)
NEXT_SHIFT = SHIFT.following()
CLIENT_AT = inside_shift(WORK_DATE, SHIFT.rank)
MANAGER_ID = "staff-manager"


# --- the service, the phones and the reads --------------------------------------------------------


def _principal(*roles: Role) -> Principal:
    """A signed-in person holding `roles`; the permissions are their union, as auth grants them (R-08)."""
    label = "-".join(role.value for role in roles)
    return Principal(
        staff_id=f"staff-{label}",
        full_name="کارمند آزمایشی",
        roles=tuple(role.value for role in roles),
        permissions=permissions_for(roles),
        device_id=f"device-{label}",
        device_label="گوشی آزمایشی",
        session_hash="synthetic-session-hash",
        csrf_token="synthetic-csrf",
        expires_at=CLIENT_AT + timedelta(hours=8),
    )


def _service(database: Database) -> ShiftEventService:
    """The real service the lifespan wires, on the migrated synthetic database."""
    return ShiftEventService(
        database,
        EventStore(database),
        ProjectionEngine(database),
        CatalogService(database),
        SettingsService(database),
        campaigns=CampaignService(database),
        clock=FixedClock(CLIENT_AT),
    )


def _record(
    service: ShiftEventService,
    actor: Principal,
    event_type: str,
    payload: dict[str, Any],
    *,
    key: str,
    ref_event_id: str | None = None,
    shift: ShiftKey = SHIFT,
) -> SubmissionResult:
    """One phone submission; `key` is the outbox idempotency key a replay carries again."""
    return service.record(
        EventSubmission(
            type=event_type,
            payload=payload,
            actor_id=actor.staff_id,
            device_id=actor.device_id,
            client_at=CLIENT_AT,
            client_event_id=new_ulid(CLIENT_AT),
            idempotency_key=key,
            shift_id=shift.text,
            ref_event_id=ref_event_id,
            shift_work_date=shift.work_date,
        ),
        principal=actor,
        shift=shift,
    )


def _events(database: Database, event_type: str, shift: ShiftKey = SHIFT) -> list[EventRecord]:
    """Every stored event of one type in one shift, in server order."""
    with database.read() as connection:
        return [event for event in events_repo.iter_shift(connection, shift.text) if event.type == event_type]


def _cash(database: Database) -> list[dict[str, Any]]:
    with database.read() as connection:
        return projections_repo.cash_rows(connection)


def _stock(database: Database) -> list[dict[str, Any]]:
    with database.read() as connection:
        return projections_repo.stock_rows(connection)


def _open_cash_box(database: Database, amount: int = 1_000_000) -> list[dict[str, Any]]:
    """Seed the reception box the way the handover does, and return the cash a decision must not move."""
    EventStore(database).append(build_event("handover.started", payload={"opening_cash": amount}, shift_id=SHIFT.text))
    ProjectionEngine(database).sync()
    return _cash(database)


# --- «ک» card-to-card: one record, the accountant's decision (E-01, E-12) --------------------------


def test_a_card_transfer_is_one_pending_record_for_the_accountant(database: Database) -> None:
    service = _service(database)
    cash_before = _open_cash_box(database)
    reception = _principal(Role.RECEPTION)
    transfer = {"amount": 150_000, "admission_no": "A-1001"}

    first = _record(service, reception, "card.transfer_reported", transfer, key="card-transfer-1")
    replay = _record(service, reception, "card.transfer_reported", transfer, key="card-transfer-1")

    assert (first.created, first.status) == (True, "recorded")
    assert (replay.created, replay.status) == (False, "replayed"), "a retried outbox item is not a second transfer"
    assert (replay.seq, replay.event_id) == (first.seq, first.event_id)
    assert [event.payload for event in _events(database, "card.transfer_reported")] == [transfer]

    pending = {entry.event_id: entry for entry in service.pending_for(_principal(Role.ACCOUNTANT), SHIFT)}
    assert list(pending) == [first.event_id]
    assert (pending[first.event_id].code, pending[first.event_id].label_fa) == ("ک", "کارت‌به‌کارت")
    assert pending[first.event_id].summary == transfer
    assert service.pending_for(reception, SHIFT) == [], "the phone that reported it holds no approval right"
    assert service.pending_for(_principal(Role.MANAGER), SHIFT) == [], "only the accountant decides a transfer"
    assert _cash(database) == cash_before, "E-01: proj_cash is identical before and after the report"


def test_the_accountant_decides_a_card_transfer_once(database: Database) -> None:
    service = _service(database)
    reception = _principal(Role.RECEPTION)
    accountant = _principal(Role.ACCOUNTANT)
    transfer = _record(
        service, reception, "card.transfer_reported", {"amount": 150_000, "admission_no": "A-1001"}, key="card-1"
    )

    with pytest.raises(ShiftEventRuleError):
        _record(
            service,
            _principal(Role.MANAGER),
            "event.approved",
            {"note": "تأیید شد"},
            key="manager-approval",
            ref_event_id=transfer.event_id,
        )
    assert _events(database, "event.approved") == [], "a manager holds the permission, not this approval right"

    approval = _record(
        service,
        accountant,
        "event.approved",
        {"note": "تأیید شد"},
        key="accountant-approval",
        ref_event_id=transfer.event_id,
    )

    decisions = _events(database, "event.approved")
    assert approval.created
    assert [(event.ref_event_id, event.actor_id, event.payload) for event in decisions] == [
        (transfer.event_id, accountant.staff_id, {"note": "تأیید شد"})
    ]
    assert summary_for(kind_for(decisions[0].type), decisions[0].payload) == {"note": "تأیید شد"}
    assert service.pending_for(accountant, SHIFT) == [], "a decided record leaves the accountant's list"

    with pytest.raises(ShiftEventRuleError):
        _record(
            service,
            accountant,
            "event.rejected",
            {"note": "رد شد"},
            key="accountant-second",
            ref_event_id=transfer.event_id,
        )
    assert _events(database, "event.rejected") == [], "one decision per record, whatever it decided"

    replay = _record(
        service,
        accountant,
        "event.approved",
        {"note": "تأیید شد"},
        key="accountant-approval",
        ref_event_id=transfer.event_id,
    )
    assert (replay.created, replay.seq) == (False, approval.seq)
    assert len(_events(database, "event.approved")) == 1, "a replayed approval writes no second decision"


def test_the_person_who_reported_a_transfer_never_approves_it(database: Database) -> None:
    service = _service(database)
    desk_accountant = _principal(Role.RECEPTION, Role.ACCOUNTANT)
    own = _record(
        service, desk_accountant, "card.transfer_reported", {"amount": 80_000, "admission_no": "A-1007"}, key="card-7"
    )

    with pytest.raises(ShiftEventRuleError):
        _record(
            service,
            desk_accountant,
            "event.approved",
            {"note": "تأیید شد"},
            key="self-approval",
            ref_event_id=own.event_id,
        )

    assert _events(database, "event.approved") == []
    other = _record(
        service, _principal(Role.ACCOUNTANT), "event.approved", {}, key="other-approval", ref_event_id=own.event_id
    )
    assert other.created, "the record stays decidable by another accountant"


def test_a_decision_is_recorded_only_in_the_shift_that_holds_the_record(database: Database) -> None:
    service = _service(database)
    transfer = _record(
        service,
        _principal(Role.RECEPTION),
        "card.transfer_reported",
        {"amount": 150_000, "admission_no": "A-1001"},
        key="card-1",
    )

    with pytest.raises(ShiftEventRuleError):
        _record(
            service,
            _principal(Role.ACCOUNTANT),
            "event.approved",
            {"note": "تأیید شد"},
            key="late-approval",
            ref_event_id=transfer.event_id,
            shift=NEXT_SHIFT,
        )

    assert _events(database, "event.approved") == []
    assert _events(database, "event.approved", shift=NEXT_SHIFT) == []


# --- «ب» refund: the doctor or the manager, never the accountant (E-02) ---------------------------


def test_a_refund_waits_for_the_shift_doctor_or_the_manager(database: Database) -> None:
    service = _service(database)
    reception = _principal(Role.RECEPTION)
    doctor = _principal(Role.SHIFT_DOCTOR)
    first = _record(
        service,
        reception,
        "refund.requested",
        {"admission_no": "A-2001", "reason": "بیمار از ادامهٔ درمان انصراف داد."},
        key="refund-1",
    )
    second = _record(
        service,
        reception,
        "refund.requested",
        {"admission_no": "A-2002", "reason": "اشتباه در ثبت پذیرش."},
        key="refund-2",
    )

    assert {entry.event_id for entry in service.pending_for(doctor, SHIFT)} == {first.event_id, second.event_id}
    assert service.pending_for(_principal(Role.ACCOUNTANT), SHIFT) == [], "the accountant approves money, not refunds"

    with pytest.raises(ShiftEventRuleError):
        _record(
            service,
            _principal(Role.ACCOUNTANT),
            "event.approved",
            {"note": None},
            key="accountant-refund",
            ref_event_id=first.event_id,
        )

    assert _record(
        service, doctor, "event.approved", {"note": "تأیید شد"}, key="doctor-approval", ref_event_id=first.event_id
    ).created
    assert _record(
        service,
        _principal(Role.MANAGER),
        "event.approved",
        {},
        key="manager-approval",
        ref_event_id=second.event_id,
    ).created
    assert {event.ref_event_id for event in _events(database, "event.approved")} == {first.event_id, second.event_id}
    assert service.pending_for(doctor, SHIFT) == []


# --- «ت» campaign discount: only a campaign running on this work day (E-03) ------------------------


def test_a_discount_applies_only_a_campaign_active_on_the_work_day(database: Database) -> None:
    service = _service(database)
    campaigns = CampaignService(database, clock=FixedClock(CLIENT_AT))
    _campaign(campaigns, "cmp-active", starts_on=WORK_DATE - timedelta(days=2), ends_on=WORK_DATE + timedelta(days=2))
    _campaign(campaigns, "cmp-ended", starts_on=WORK_DATE - timedelta(days=10), ends_on=WORK_DATE - timedelta(days=1))
    reception = _principal(Role.RECEPTION)

    accepted = _record(
        service,
        reception,
        "campaign.discount_applied",
        {"campaign_id": "cmp-active", "admission_no": "A-1002"},
        key="campaign-1",
    )

    assert accepted.created
    for campaign_id in ("cmp-ended", "cmp-nobody-defined"):
        with pytest.raises(ShiftEventRuleError, match="کمپین"):
            _record(
                service,
                reception,
                "campaign.discount_applied",
                {"campaign_id": campaign_id, "admission_no": "A-1003"},
                key=f"campaign-{campaign_id}",
            )

    assert [event.payload["campaign_id"] for event in _events(database, "campaign.discount_applied")] == ["cmp-active"]
    assert (_stock(database), _cash(database)) == ([], []), "E-03: the discount moves no projection"


def _campaign(campaigns: CampaignService, campaign_id: str, *, starts_on: date, ends_on: date) -> None:
    campaigns.save(
        campaign_id,
        name=f"تخفیف {campaign_id}",
        service="ویزیت پزشک عمومی",
        amount_toman=50_000,
        starts_on=starts_on,
        ends_on=ends_on,
        actor_id=MANAGER_ID,
    )


# --- the kinds' own payload rules -----------------------------------------------------------------


def test_a_zero_transfer_a_blank_reason_and_an_empty_admission_write_nothing(database: Database) -> None:
    service = _service(database)
    reception = _principal(Role.RECEPTION)

    with pytest.raises(ShiftEventRuleError, match="مبلغ"):
        _record(service, reception, "card.transfer_reported", {"amount": 0, "admission_no": "A-1001"}, key="zero")
    with pytest.raises(ShiftEventRuleError, match="دلیل"):
        _record(service, reception, "refund.requested", {"admission_no": "A-2001", "reason": "   "}, key="blank")
    with pytest.raises(ShiftEventRuleError):
        _record(service, reception, "card.transfer_reported", {"amount": 150_000, "admission_no": ""}, key="empty")

    with database.read() as connection:
        assert events_repo.count_events(connection) == 0
