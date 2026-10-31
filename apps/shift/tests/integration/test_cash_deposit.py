"""The accountant records a cash deposit to the bank through the real routes (B2, owner decision 3).

Money test: the deposit takes the amount off the reception box at once (the «W» term), so the next
count does not read it as a gap; only the accountant (and the manager) may write one; the bank slip is
optional but, when named, must be a photo the server already holds; and replaying the log gives the
same box the live projection has. Every value is invented (ADR-006).
"""

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient

from event_fixtures import build_event
from shift.adapters.db import projections_repo
from shift.app import create_app
from shift.common.clock import to_tehran
from shift.common.ids import new_ulid
from shift.config.settings import Settings
from shift.domain.shift_clock import covering
from shift.services.event_store import EventStore

OPENING = 5_000_000
DEPOSIT = 1_200_000
JPEG = b"\xff\xd8\xff\xe0 synthetic bank slip \xff\xd9"
SLIP = hashlib.sha256(JPEG).hexdigest()
META_TOKEN = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


@pytest.fixture()
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        state = client.app.state
        seed_roster(state.database, pepper=state.secret_key)
        shift = covering(to_tehran(state.clock.now_utc()))
        EventStore(state.database, clock=state.clock).append(
            build_event(
                "handover.started",
                payload={"opening_cash": OPENING},
                shift_id=shift.text,
                actor_id=ROLES["reception"].staff_id,
                device_id=ROLES["reception"].device_id,
                client_at=state.clock.now_utc(),
                work_date=shift.work_date,
                idempotency_key="deposit-opening",
            )
        )
        state.projections.sync()
        yield client


def _sign_in(client: TestClient, role: str) -> str:
    person = ROLES[role]
    assert login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN).status_code == 200
    match = META_TOKEN.search(client.get("/").text)
    assert match is not None
    return match.group(1)


def _box(client: TestClient) -> int:
    with client.app.state.database.read() as connection:
        rows = projections_repo.cash_rows(connection)
    return next(int(row["expected"]) for row in rows if row["cash_box_id"] == "reception")


def _deposit(client: TestClient, token: str, payload: dict[str, object], key: str = "dep-1"):
    return client.post(
        "/api/v1/events",
        json={
            "type": "cash.deposited",
            "payload": payload,
            "client_at": datetime.now(UTC).isoformat(),
            "client_event_id": new_ulid(),
            "idempotency_key": key,
        },
        headers={"X-CSRFToken": token},
    )


def test_the_accountants_deposit_with_a_slip_lowers_the_box_and_replays_to_the_same_figure(app: TestClient) -> None:
    token = _sign_in(app, "accountant")
    stored = app.put(
        "/api/v1/photos/" + SLIP, content=JPEG, headers={"Content-Type": "image/jpeg", "X-CSRFToken": token}
    )
    assert stored.status_code == 201, stored.text

    response = _deposit(app, token, {"amount": DEPOSIT, "photo_sha256": SLIP})

    assert response.status_code == 200, response.text
    assert _box(app) == OPENING - DEPOSIT
    assert _deposit(app, token, {"amount": DEPOSIT, "photo_sha256": SLIP}).json()["created"] is False
    assert _box(app) == OPENING - DEPOSIT, "a replayed deposit never moves the box twice"
    report = app.app.state.projections.rebuild()
    assert report.drift == ()
    assert _box(app) == OPENING - DEPOSIT, "the rebuilt state equals the live state"


def test_a_deposit_needs_no_photo(app: TestClient) -> None:
    token = _sign_in(app, "accountant")

    assert _deposit(app, token, {"amount": DEPOSIT}).status_code == 200
    assert _box(app) == OPENING - DEPOSIT


def test_a_slip_the_server_does_not_hold_is_refused_and_the_box_stays(app: TestClient) -> None:
    token = _sign_in(app, "accountant")

    response = _deposit(app, token, {"amount": DEPOSIT, "photo_sha256": "a" * 64})

    assert response.status_code == 422
    assert _box(app) == OPENING


@pytest.mark.parametrize("amount", [0, -5, 1.5])
def test_a_deposit_of_nothing_or_a_fraction_is_refused(app: TestClient, amount: float) -> None:
    token = _sign_in(app, "accountant")

    assert _deposit(app, token, {"amount": amount}).status_code == 422
    assert _box(app) == OPENING


@pytest.mark.parametrize("role", ["reception", "nurse", "storekeeper"])
def test_a_role_without_the_deposit_permission_cannot_write_one(app: TestClient, role: str) -> None:
    token = _sign_in(app, role)

    response = _deposit(app, token, {"amount": DEPOSIT})

    assert response.status_code == 422
    assert _box(app) == OPENING


def test_the_queue_offers_the_deposit_and_the_wizard_lists_it(app: TestClient) -> None:
    _sign_in(app, "accountant")
    assert "/events?kind=cash.deposited" in app.get("/accountant/queue").text
    assert "cash.deposited" in app.get("/events").text
