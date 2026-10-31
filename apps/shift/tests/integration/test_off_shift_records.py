"""R-15: an offline record is judged at the moment it happened, not when it reached the server.

The case the rule exists for: a nurse's phone is offline, she signs the shift over, and only then does
the queue replay. Refusing that record with a 403 loses the clinic's own work, so it is applied
normally. A record she made while she was not on shift at all is not dropped either: it is held for
the manager, through the same `event.held` flow as an implausible instant, with a Persian reason.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, SPARE, login, put_on_shift, seed_roster
from fastapi.testclient import TestClient

from shift.adapters.db import handover_repo
from shift.app import create_app
from shift.common.clock import to_tehran
from shift.common.ids import new_ulid
from shift.config.settings import Settings
from shift.domain.daily_card import window_for
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.shift_clock import covering

# Near the server's own clock: a record from this evening is plausible, so the hold rule that fires here
# is the shift rule and not the three-day one (F5).
NOW = datetime.now(UTC).replace(microsecond=0)
VITALS = {"type": "vitals.recorded", "payload": {"admission_no": "A-2001", "systolic": 120, "diastolic": 80}}


@contextmanager
def _app(root: Path, *, on_shift: bool) -> Iterator[TestClient]:
    settings = Settings(
        environment="development",  # plain-HTTP test client: no Secure cookies
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
    )
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key, on_shift=on_shift)
        yield client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with _app(tmp_path, on_shift=True) as open_client:
        yield open_client


def _headers(client: TestClient, key: str) -> dict[str, str]:
    person = ROLES[key]
    issued = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert issued.status_code == 200, issued.text
    return {"X-CSRFToken": issued.json()["csrf_token"]}


def _post(client: TestClient, headers: dict[str, str], *, client_at: datetime, body: dict | None = None):
    return client.post(
        "/api/v1/events",
        json={**(body or VITALS), "client_at": client_at.isoformat(), "client_event_id": new_ulid(client_at)},
        headers=headers,
    )


def _sign_over(client: TestClient, *, opened_at: datetime, signed_at: datetime) -> None:
    """The nurse took the flow over at `opened_at` and signed it to the spare nurse at `signed_at`."""
    shift = covering(to_tehran(opened_at))
    with client.app.state.database.write() as connection:
        handover_repo.insert(
            connection,
            handover_id="01J00000000000000000000001",
            shift_id=shift.text,
            flow=HandoverFlow.NURSING,
            opened_at=opened_at,
            work_date=shift.work_date,
            outgoing_id=ROLES["nurse"].staff_id,
            incoming_id=SPARE.staff_id,
            created_at=opened_at,
        )
        handover_repo.mark_signed(connection, "01J00000000000000000000001", signed_at)
        handover_repo.set_custodian(
            connection,
            flow=HandoverFlow.NURSING,
            custodian_id=SPARE.staff_id,
            updated_at=signed_at,
            updated_by=ROLES["manager"].staff_id,
        )


def test_a_record_made_on_shift_is_accepted_after_she_signed_over(client: TestClient) -> None:
    """The bug R-15 exists for: the queue replays after the sign-over and the record still lands."""
    put_on_shift(client.app.state.database)
    _sign_over(client, opened_at=NOW - timedelta(hours=2), signed_at=NOW - timedelta(hours=1))
    headers = _headers(client, "nurse")
    assert client.get("/api/v1/auth/me").json()["on_shift"] == [], "she handed the flow on"

    made_while_on_shift = _post(client, headers, client_at=NOW - timedelta(minutes=90))

    assert made_while_on_shift.status_code == 200, made_while_on_shift.text
    body = made_while_on_shift.json()
    assert body["held"] is False, "her own shift's record is applied, not kept for the manager"
    assert body["status"] == "recorded"


def test_the_manager_card_shows_the_presence_and_the_kept_record(tmp_path: Path) -> None:
    """ADR-021 on the manager's card: who said «حضور در شیفت», and the off-shift record it kept."""
    with _app(tmp_path / "card", on_shift=False) as client:
        headers = _headers(client, "multi_role")
        # A reception kind: a record outside the person's own grants is refused, not held.
        card = {"type": "card.transfer_reported", "payload": {"amount": 150_000, "admission_no": "A-1001"}}
        kept = _post(client, headers, client_at=NOW - timedelta(minutes=5), body=card)
        assert kept.json()["held"] is True
        # She says «حضور در شیفت» afterwards: the earlier record was made while she was off shift.
        declared = client.post("/api/v1/shift/presence", json={"role": "reception"}, headers=headers)
        assert declared.status_code == 201, declared.text

        # The card that covers today is built tomorrow (it reports the day that just ended).
        today = to_tehran(NOW).date()
        run_day = today + timedelta(days=1)
        while window_for(run_day) is None:
            run_day += timedelta(days=1)
        assert client.app.state.daily_card.regenerate(run_day) is not None

        page = client.get(f"/manager/card/{run_day.isoformat()}", headers=_headers(client, "manager"))

    assert page.status_code == 200, page.text
    assert "حضور در شیفت" in page.text
    assert ROLES["multi_role"].full_name in page.text, "the card names who declared presence"
    assert "رویدادهای نگه‌داشته‌شده" in page.text, "the kept off-shift record waits on the same card"


def test_a_record_made_while_off_shift_is_held_for_the_manager_with_a_persian_reason(
    tmp_path: Path,
) -> None:
    """Off shift at the instant of the record: kept, in Persian, and never a 403 (R-15, R-14)."""
    with _app(tmp_path / "off", on_shift=False) as client:
        headers = _headers(client, "nurse")

        kept = _post(client, headers, client_at=NOW - timedelta(minutes=5))

        assert kept.status_code == 200, kept.text
        assert kept.json()["held"] is True
        listed = client.get("/api/v1/manager/held-events", headers=_headers(client, "manager")).json()
        assert [row["event_id"] for row in listed] == [kept.json()["event_id"]]
        assert "در شیفت نبوده است" in listed[0]["message"], "the manager reads why it was kept"
    assert all(person.full_name not in kept.text for person in ROLES.values())
