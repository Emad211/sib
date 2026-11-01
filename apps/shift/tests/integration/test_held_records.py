"""F5: a record from an offline phone is never lost, and an implausible one waits for the manager.

Two behaviours, both through the real API: a record whose own instant belongs to the shift that was
on duty when it happened is accepted and attributed there, even when the queue crossed midnight; and
a record stamped in the future or more than three days old is kept, changes nothing, and waits on the
manager's list until it is released (applied once) or rejected (kept out for good).
"""

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, seed_roster
from auth_fixtures import login as login_request
from fastapi import status
from fastapi.testclient import TestClient
from photo_fixtures import RECEIPT_SHA, keep_receipt
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import events_repo, projections_repo
from shift.api.deps import running_shift
from shift.common.clock import TEHRAN
from shift.common.ids import new_ulid
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering
from shift.services.outbox import business_date_for

RUNNING = ShiftKey(date(2026, 10, 28), 2)
SHELF = "shelf_reception"
PARA = "para_500_amp"


def _headers(session: dict[str, Any]) -> dict[str, str]:
    """The session-bound CSRF header every unsafe route demands."""
    return {"X-CSRFToken": session["csrf_token"]}


def _login(client: TestClient, role: str) -> dict[str, Any]:
    """Log one seeded role in through the real form (the helper loads the login page for its CSRF)."""
    person = ROLES[role]
    response = login_request(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


def _waste(client: TestClient, session: dict[str, Any], *, occurred: datetime, quantity: int = 3) -> Any:
    """One well-formed waste report from the reception phone, stamped at `occurred`."""
    event_id = new_ulid()
    return client.post(
        "/api/v1/events",
        json={
            "client_event_id": event_id,
            "idempotency_key": event_id,
            "type": "stock.waste_reported",
            "payload": {
                "item_id": PARA,
                "location_id": SHELF,
                "quantity": quantity,
                "waste_kind": "broken",
                "culprit_id": "staff-nurse",
                "confirmer_id": "staff-store",
                "photo_sha256": RECEIPT_SHA,
            },
            "client_at": occurred.isoformat(),
            "work_date": occurred.date().isoformat(),
        },
        headers=_headers(session),
    )


def _qty(client: TestClient) -> int:
    """The reception shelf's quantity of the item these tests move; zero when the shelf has no row."""
    with client.app.state.database.read() as connection:
        rows = projections_repo.stock_rows(connection)
    return next((int(row["qty"]) for row in rows if row["item_id"] == PARA), 0)


@pytest.fixture()
def seeded(client: TestClient) -> TestClient:
    """The real app with one staff member and one phone per role, on a fixed running shift."""
    seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
    seed_catalog(client.app.state.database)
    keep_receipt(client.app)
    client.app.dependency_overrides[running_shift] = lambda: RUNNING
    return client


def test_a_queue_that_crossed_midnight_joins_the_shift_it_happened_in(seeded: TestClient) -> None:
    """The record is attributed by its own instant, and the server's received time is kept beside it."""
    nurse = _login(seeded, "nurse")
    now = datetime.now(tz=TEHRAN)
    occurred = (now - timedelta(hours=12)).replace(hour=23, minute=30)
    if occurred >= now:
        occurred -= timedelta(days=1)

    response = _waste(seeded, nurse, occurred=occurred)

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["status"] == "recorded"
    with seeded.app.state.database.read() as connection:
        stored = events_repo.find_by_event_id(connection, response.json()["event_id"])
    assert stored is not None
    assert stored.work_date == business_date_for(occurred).isoformat()
    assert stored.shift_id == covering(occurred).text
    assert stored.client_at != stored.server_at


def test_a_future_record_is_held_then_released_once_and_moves_the_shelf_once(seeded: TestClient) -> None:
    """A handset whose clock ran ahead cannot move stock by itself; the manager decides."""
    nurse = _login(seeded, "nurse")
    before = _qty(seeded)
    occurred = datetime.now(tz=TEHRAN) + timedelta(days=2)

    held = _waste(seeded, nurse, occurred=occurred)

    assert held.status_code == status.HTTP_200_OK, held.text
    assert held.json()["status"] == "held"
    assert held.json()["held"] is True
    assert _qty(seeded) == before

    manager = _login(seeded, "manager")
    listing = seeded.get("/api/v1/manager/held-events")
    assert listing.status_code == status.HTTP_200_OK, listing.text
    rows = [row for row in listing.json() if row["event_id"] == held.json()["event_id"]]
    assert len(rows) == 1
    assert rows[0]["reason"] == "future"
    assert "آینده" in rows[0]["message"]

    released = seeded.post(f"/api/v1/manager/held-events/{rows[0]['held_event_id']}/release", headers=_headers(manager))
    assert released.status_code == status.HTTP_200_OK, released.text
    assert _qty(seeded) == before - 3

    again = seeded.post(f"/api/v1/manager/held-events/{rows[0]['held_event_id']}/release", headers=_headers(manager))
    assert again.status_code == status.HTTP_404_NOT_FOUND
    assert _qty(seeded) == before - 3


def test_a_rejected_record_never_moves_the_shelf(seeded: TestClient) -> None:
    """Rejecting keeps the record out of the books; nothing is deleted and nothing is applied."""
    nurse = _login(seeded, "nurse")
    before = _qty(seeded)
    stale = datetime.now(tz=TEHRAN) - timedelta(days=4)

    held = _waste(seeded, nurse, occurred=stale)

    assert held.status_code == status.HTTP_200_OK, held.text
    manager = _login(seeded, "manager")
    listing = seeded.get("/api/v1/manager/held-events").json()
    rows = [row for row in listing if row["event_id"] == held.json()["event_id"]]
    assert rows[0]["reason"] == "stale"

    rejected = seeded.post(f"/api/v1/manager/held-events/{rows[0]['held_event_id']}/reject", headers=_headers(manager))

    assert rejected.status_code == status.HTTP_200_OK, rejected.text
    assert _qty(seeded) == before
    assert all(row["event_id"] != held.json()["event_id"] for row in seeded.get("/api/v1/manager/held-events").json())


def _post(client: TestClient, session: dict[str, Any], body: dict[str, Any]) -> Any:
    """One ingest request from a signed-in phone."""
    return client.post("/api/v1/events", json=body, headers=_headers(session))


def test_the_same_held_record_is_kept_once(seeded: TestClient) -> None:
    """A phone that re-sends a kept record must not fill the manager's list with copies of it."""
    nurse = _login(seeded, "nurse")
    event_id = new_ulid()
    body = {
        "client_event_id": event_id,
        "idempotency_key": event_id,
        "type": "stock.waste_reported",
        "payload": {
            "item_id": PARA,
            "location_id": SHELF,
            "quantity": 1,
            "waste_kind": "broken",
            "culprit_id": "staff-nurse",
            "confirmer_id": "staff-store",
        },
        "client_at": (datetime.now(tz=TEHRAN) + timedelta(days=2)).isoformat(),
    }

    first = _post(seeded, nurse, body)
    second = _post(seeded, nurse, body)

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert second.json()["created"] is False
    _login(seeded, "manager")
    rows = [row for row in seeded.get("/api/v1/manager/held-events").json() if row["event_id"] == event_id]
    assert len(rows) == 1


def test_a_held_approval_keeps_its_reference_and_is_released_by_the_manager(seeded: TestClient) -> None:
    """An approval from a phone whose clock ran ahead still names the record it decides."""
    receptionist = _login(seeded, "reception")
    transfer = new_ulid()
    recorded = _post(
        seeded,
        receptionist,
        {
            "client_event_id": transfer,
            "idempotency_key": transfer,
            "type": "card.transfer_reported",
            "payload": {"amount": 150_000, "admission_no": "A-1001", "photo_sha256": RECEIPT_SHA},
            "client_at": datetime.now(tz=TEHRAN).isoformat(),
        },
    )
    assert recorded.status_code == status.HTTP_200_OK, recorded.text

    accountant = _login(seeded, "accountant")
    approval = new_ulid()
    held = _post(
        seeded,
        accountant,
        {
            "client_event_id": approval,
            "idempotency_key": approval,
            "type": "event.approved",
            "payload": {"note": "تأیید شد"},
            "ref_event_id": recorded.json()["event_id"],
            "client_at": (datetime.now(tz=TEHRAN) + timedelta(days=1)).isoformat(),
        },
    )
    assert held.status_code == status.HTTP_200_OK, held.text
    assert held.json()["status"] == "held"

    manager = _login(seeded, "manager")
    rows = [row for row in seeded.get("/api/v1/manager/held-events").json() if row["event_id"] == approval]
    assert len(rows) == 1
    released = seeded.post(f"/api/v1/manager/held-events/{rows[0]['held_event_id']}/release", headers=_headers(manager))
    assert released.status_code == status.HTTP_200_OK, released.text

    with seeded.app.state.database.read() as connection:
        decided = events_repo.latest_ref(connection, recorded.json()["event_id"], ("event.approved",))
        releasing = events_repo.latest_ref(connection, rows[0]["held_event_id"], ("event.released",))
    assert decided is not None
    assert decided.actor_id == "staff-accountant"
    # R-04: the decision that released the record names the manager who made it.
    assert releasing is not None
    assert releasing.actor_id == "staff-manager"
