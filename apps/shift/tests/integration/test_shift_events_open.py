"""The wizard's open-record picker (SHF-7, critic finding 7 of the SHF-3 review).

«تعمیر شد», «ارجاع خرابی» and «تحویل امانت بیمار» close a record an earlier step opened — sometimes in
another shift — so the phone asks the server which open records **this** person may act on. The route
is the phone half of F2: the API and its rules already existed, but nothing on the phone could name the
record. These tests drive the real routes: one person records, the right person is offered the record,
the wrong person is not, and no cash figure ever travels with the list (R-01, R-07).
"""

from collections.abc import Iterator
from datetime import datetime

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from photo_fixtures import keep_receipt, with_photo
from synthetic_catalog import seed as seed_catalog

from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.config.settings import Settings

EVENTS_URL = "/api/v1/events"
OPEN_URL = "/api/v1/shift-events/open"
TODAY = datetime.now(TEHRAN).date().isoformat()


@pytest.fixture()
def staff_client(settings: Settings) -> Iterator[TestClient]:
    """One app on the synthetic roster and reference catalog; each test signs in its own people."""
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_catalog(client.app.state.database)
        keep_receipt(client.app)
        yield client


def _session(client: TestClient, role: str) -> dict[str, str]:
    """The CSRF header of a fresh real login for one role."""
    person = ROLES[role]
    client.cookies.clear()
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return {"X-CSRFToken": response.json()["csrf_token"]}


def _append(client: TestClient, role: str, key: str, event_type: str, payload: dict, **extra) -> dict:
    """Record one event through the same endpoint the phone's outbox replays to."""
    headers = _session(client, role)
    body = {
        "type": event_type,
        "payload": with_photo(event_type, payload),
        "client_event_id": key,
        "idempotency_key": key,
        "client_at": datetime.now(TEHRAN).isoformat(),
        **extra,
    }
    response = client.post(EVENTS_URL, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _open(client: TestClient, role: str) -> list[dict]:
    _session(client, role)
    response = client.get(OPEN_URL)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_fault_is_offered_to_the_manager_and_to_its_assignee_only(staff_client: TestClient) -> None:
    """The manager assigns; the assignee may close it; the reporter is offered nothing."""
    nurse = ROLES["nurse"]
    reported = _append(
        staff_client,
        "reception",
        "01J00000000000000000000001",
        "equipment.fault_reported",
        {"device": "مانیتور اتاق CPR", "description": "روشن نمی‌شود"},
    )

    manager_view = _open(staff_client, "manager")
    assert [(entry["event_id"], entry["actions"]) for entry in manager_view] == [
        (reported["event_id"], ["equipment.fault_assigned", "equipment.fault_fixed"])
    ]

    _append(
        staff_client,
        "manager",
        "01J00000000000000000000002",
        "equipment.fault_assigned",
        {"assignee_id": nurse.staff_id, "due_on": TODAY},
        ref_event_id=reported["event_id"],
    )
    nurse_view = _open(staff_client, "nurse")
    assert [(entry["event_id"], entry["actions"]) for entry in nurse_view] == [
        (reported["event_id"], ["equipment.fault_fixed"])
    ], "the fix rule admits the assignee, so the list must not offer it to anybody else"
    assert _open(staff_client, "reception") == [], "the reporter may not close somebody else's fault"

    _append(
        staff_client,
        "nurse",
        "01J00000000000000000000003",
        "equipment.fault_fixed",
        {"note": "کابل تعویض شد"},
        ref_event_id=reported["event_id"],
    )
    assert _open(staff_client, "nurse") == [], "a fixed fault is no longer open"
    assert _open(staff_client, "manager") == []


def test_a_kept_belonging_is_offered_to_the_desk_that_can_hand_it_back(staff_client: TestClient) -> None:
    """The belonging waits until somebody holding it returns it; nobody else even sees the row."""
    held = _append(
        staff_client,
        "reception",
        "01J00000000000000000000011",
        "patient.item_held",
        {"item_name": "عینک", "patient_name": "بیمار آزمایشی", "admission_no": "A-2001"},
    )

    view = _open(staff_client, "reception")
    assert [(entry["event_id"], entry["actions"], entry["summary"]["item_name"]) for entry in view] == [
        (held["event_id"], ["patient.item_delivered"], "عینک")
    ]
    assert _open(staff_client, "purchaser") == [], "a role with no patient-item work gets no list"

    _append(
        staff_client,
        "reception",
        "01J00000000000000000000012",
        "patient.item_delivered",
        {"note": "تحویل شد"},
        ref_event_id=held["event_id"],
    )
    assert _open(staff_client, "reception") == []


def test_the_list_carries_no_cash_figure(staff_client: TestClient) -> None:
    """A withdrawal of the same shift never leaks into the open-record list (R-01)."""
    _append(
        staff_client,
        "reception",
        "01J00000000000000000000021",
        "cash.withdrawn",
        {"amount": 250_000, "recipient_id": ROLES["nurse"].staff_id, "reason": "خرید فوری"},
    )
    _append(
        staff_client,
        "reception",
        "01J00000000000000000000022",
        "stock.waste_reported",
        {
            "item_id": "para_500_amp",
            "location_id": "shelf_reception",
            "quantity": 1,
            "waste_kind": "broken",
            "culprit_id": ROLES["reception"].staff_id,
            "confirmer_id": ROLES["nurse"].staff_id,
        },
    )

    body = staff_client.get(OPEN_URL, headers={"Accept": "application/json"})
    assert body.status_code == 200
    assert "250000" not in body.text and "250,000" not in body.text
    assert all("amount" not in entry["summary"] for entry in body.json())


def test_a_vital_reading_of_the_running_shift_is_offered_until_its_follow_up(
    staff_client: TestClient,
) -> None:
    """The nurse's follow-up points at a reading of this shift; once opened, the reading is closed."""
    reading = _append(
        staff_client,
        "nurse",
        "01J00000000000000000000031",
        "vitals.recorded",
        {"systolic": 150, "diastolic": 95},
    )

    view = _open(staff_client, "nurse")
    assert [(entry["event_id"], entry["actions"]) for entry in view] == [
        (reading["event_id"], ["vitals.followup_opened"])
    ]
    assert _open(staff_client, "reception") == [], "a receptionist records no vital sign"

    _append(
        staff_client,
        "nurse",
        "01J00000000000000000000032",
        "vitals.followup_opened",
        {"note": "کنترل دوبارهٔ فشار"},
        ref_event_id=reading["event_id"],
    )
    assert _open(staff_client, "nurse") == [], "a reading with its follow-up is no longer open"
