"""The two reward pages (S01 §5, SHF-5) through the real app: the manager's screen and one's own.

A reward row is a name and a figure, so exactly two things can hurt the clinic from these pages and
both are gated here: a person must never receive another person's row, and the manager's screen must
stay behind `reward.grant` alone. The module drives both pages and the manager's four form posts
through `TestClient`, with the router mounted the way `app.py` will mount it, on a migrated synthetic
database whose accounting mirrors hold invented rows only (ADR-006).
"""

from collections.abc import Iterator
from datetime import datetime

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from clocks import FixedClock
from fastapi import Request
from fastapi.testclient import TestClient

from shift.adapters.db.core import Database
from shift.api import routes_rewards
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.common.ids import new_ulid
from shift.config.settings import Settings
from shift.domain.permissions import permissions_for
from shift.services.auth import Principal
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine
from shift.services.rewards import RewardsService
from shift.services.shift_events import ShiftEventService

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=TEHRAN)
MONTH = "1405-08"
NEXT_MONTH = "1405-09"
VITALS_RATE = 50_000
REGISTRATION_RATE = 20_000
NURSE_ACC = "31"
RECEPTION_ACC = "reception-user-7"
NURSE_AMOUNT = "۵۰٬۰۰۰"
RECEPTION_AMOUNT = "۲۰٬۰۰۰"
APPROVED = "event.approved"


def _rewards(request: Request) -> RewardsService:
    """The service the app created; the router is mounted here exactly as `app.py` will mount it."""
    return request.app.state.rewards


def _shift_events(request: Request) -> ShiftEventService:
    return request.app.state.shift_events


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    """One app with the reward router mounted, a fixed clinic month and a synthetic roster."""
    app = create_app(settings)
    app.include_router(routes_rewards.build_rewards_router(rewards=_rewards, shift_events=_shift_events))
    with TestClient(app) as test_client:
        # One clinic clock for the whole test: the store, the approval flow and the reward service all
        # read the same instant, so a record is never "three days old" just because the test ran late.
        clock = FixedClock(NOW)
        database = test_client.app.state.database
        events = EventStore(database, clock=clock)
        test_client.app.state.clock = clock
        test_client.app.state.events = events
        test_client.app.state.rewards = RewardsService(database, events, clock=clock, audit=test_client.app.state.audit)
        test_client.app.state.shift_events = ShiftEventService(
            database,
            events,
            ProjectionEngine(database),
            test_client.app.state.catalog,
            test_client.app.state.settings_service,
            clock=clock,
        )
        seed_roster(database, pepper=test_client.app.state.secret_key)
        test_client.app.state.rewards.map_staff(ROLES["nurse"].staff_id, NURSE_ACC, principal=_principal("manager"))
        test_client.app.state.rewards.map_staff(
            ROLES["reception"].staff_id, RECEPTION_ACC, principal=_principal("manager")
        )
        yield test_client


def _principal(role: str) -> Principal:
    """The principal the seeding needs; a route builds the same one from the session."""
    person = ROLES[role]
    return Principal(
        staff_id=person.staff_id,
        full_name=person.full_name,
        roles=person.roles,
        permissions=permissions_for(person.roles),
        device_id=person.device_id,
        device_label="گوشی آزمایشی",
        session_hash=f"session-{person.staff_id}",
        csrf_token=f"csrf-{person.staff_id}",
        expires_at=datetime(2026, 10, 30, tzinfo=TEHRAN),
    )


def _sign_in(client: TestClient, role: str = "manager") -> str:
    """Sign in the real way and return the session's CSRF token for the unsafe verbs."""
    client.cookies.clear()
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _vital(database: Database, *, injection_id: int, acc_user: str) -> None:
    """One sugar/BP service as accounting's view reports it, stamped in Tehran local time."""
    with database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO acc_vital (injection_id, invoice_id, nurse_id, kind, closed_at, ingested_at) "
            "VALUES (?, 1, ?, 'sugar', '2026-10-27 09:15:00', '2026-10-27 09:15:00')",
            (injection_id, int(acc_user)),
        )


def _registration(database: Database, *, patient_id: int, acc_user: str) -> None:
    """One registration with a valid national ID and mobile, which is what earns the reward."""
    with database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO acc_registration "
            "(patient_id, created_by, created_at, has_valid_nid, has_valid_phone, is_foreign, ingested_at) "
            "VALUES (?, ?, '2026-10-27 10:00:00', 1, 1, 0, '2026-10-27 10:00:00')",
            (patient_id, acc_user),
        )


def _paid_month(client: TestClient, csrf: str) -> None:
    """A rate, two works of two people, the accrual and the manager's confirmation of both."""
    service: RewardsService = client.app.state.rewards
    service.set_rate("vitals", MONTH, VITALS_RATE, principal=_principal("manager"))
    service.set_rate("registration", MONTH, REGISTRATION_RATE, principal=_principal("manager"))
    _vital(client.app.state.database, injection_id=9001, acc_user=NURSE_ACC)
    _registration(client.app.state.database, patient_id=5001, acc_user=RECEPTION_ACC)
    assert service.accrue(MONTH).created == 2
    for record in service.pending():
        approved = client.post(
            "/api/v1/events",
            json={
                "type": APPROVED,
                "payload": {},
                "client_at": NOW.isoformat(),
                "client_event_id": new_ulid(NOW),
                "idempotency_key": new_ulid(NOW),
                "ref_event_id": record.event_id,
            },
            headers={"X-CSRFToken": csrf},
        )
        assert approved.status_code == 200, approved.text


def test_the_manager_sees_the_month_table_with_its_figures_and_names(client: TestClient) -> None:
    csrf = _sign_in(client, "manager")
    _paid_month(client, csrf)

    page = client.get("/manager/rewards")

    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    for text in (ROLES["nurse"].full_name, "قند و فشار", NURSE_AMOUNT, ROLES["reception"].full_name):
        assert text in page.text


def test_a_receptionist_gets_a_403_that_holds_no_figure(client: TestClient) -> None:
    _paid_month(client, _sign_in(client, "manager"))
    _sign_in(client, "reception")

    page = client.get("/manager/rewards")

    assert page.status_code == 403
    assert NURSE_AMOUNT not in page.text
    assert ROLES["nurse"].full_name not in page.text


def test_a_signed_out_browser_is_sent_to_the_login_screen(client: TestClient) -> None:
    for path in ("/manager/rewards", "/rewards"):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 303
        assert page.headers["location"].startswith("/login")


def test_a_person_reads_only_their_own_row(client: TestClient) -> None:
    _paid_month(client, _sign_in(client, "manager"))
    _sign_in(client, "nurse")

    page = client.get("/rewards")

    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    for own in (ROLES["nurse"].full_name, NURSE_AMOUNT, "رتبهٔ شما"):
        assert own in page.text
    for other in (ROLES["reception"].full_name, RECEPTION_AMOUNT, "پذیرش یک"):
        assert other not in page.text


def test_the_manager_sets_a_rate_through_the_form(client: TestClient) -> None:
    csrf = _sign_in(client, "manager")

    response = client.post(
        "/manager/rewards/rates",
        data={"work_type": "vitals", "month": MONTH, "amount_toman": str(VITALS_RATE)},
        headers={"X-CSRFToken": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert MONTH in response.headers["location"]
    assert [(rate.work_type, rate.amount_toman) for rate in client.app.state.rewards.rates(MONTH)] == [
        ("vitals", VITALS_RATE)
    ]


def test_a_refused_rate_is_a_400_and_writes_nothing(client: TestClient) -> None:
    csrf = _sign_in(client, "manager")

    response = client.post(
        "/manager/rewards/rates",
        data={"work_type": "vitals", "month": MONTH, "amount_toman": "-5"},
        headers={"X-CSRFToken": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert client.app.state.rewards.rates(MONTH) == []


def test_the_manager_revokes_a_confirmed_reward_through_the_form(client: TestClient) -> None:
    csrf = _sign_in(client, "manager")
    _paid_month(client, csrf)
    earned = next(row for row in client.app.state.rewards.records(MONTH) if row.staff_id == ROLES["nurse"].staff_id)

    response = client.post(
        "/manager/rewards/revoke",
        data={"month": MONTH, "event_id": earned.event_id, "reason": "ثبت جعلی بود"},
        headers={"X-CSRFToken": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303, response.text
    assert client.app.state.rewards.payroll(MONTH).net_toman == VITALS_RATE + REGISTRATION_RATE
    assert client.app.state.rewards.payroll(NEXT_MONTH).deducted_toman == VITALS_RATE


def test_the_accrual_button_and_the_mapping_form_do_their_one_job(client: TestClient) -> None:
    csrf = _sign_in(client, "manager")
    client.app.state.rewards.set_rate("vitals", MONTH, VITALS_RATE, principal=_principal("manager"))
    _vital(client.app.state.database, injection_id=9101, acc_user=NURSE_ACC)

    accrued = client.post(
        "/manager/rewards/accrue",
        data={"month": MONTH},
        headers={"X-CSRFToken": csrf},
        follow_redirects=False,
    )
    mapped = client.post(
        "/manager/rewards/staff-map",
        data={"staff_id": ROLES["storekeeper"].staff_id, "acc_user": "77"},
        headers={"X-CSRFToken": csrf},
        follow_redirects=False,
    )

    assert (accrued.status_code, mapped.status_code) == (303, 303)
    assert len(client.app.state.rewards.pending()) == 1
    mapped_users = {option.staff_id: option.acc_user for option in client.app.state.rewards.staff_options()}
    assert mapped_users[ROLES["storekeeper"].staff_id] == "77"


def test_the_own_page_reports_the_team_goal_without_naming_anybody(client: TestClient) -> None:
    """The competition rule: own rank and the team's total, and not one other figure (S01 §5)."""
    _paid_month(client, _sign_in(client, "manager"))
    _sign_in(client, "reception")

    page = client.get(f"/rewards?month={MONTH}")

    assert page.status_code == 200
    assert "هدف تیمی این ماه" in page.text
    assert RECEPTION_AMOUNT in page.text
    assert NURSE_AMOUNT not in page.text
    assert ROLES["nurse"].full_name not in page.text


def test_a_month_with_no_reward_reads_as_empty_not_as_an_error(client: TestClient) -> None:
    _sign_in(client, "manager")

    page = client.get("/manager/rewards?month=1405-09")

    assert page.status_code == 200
    assert "در این ماه پاداشی تأیید یا ابطال نشده است." in page.text
