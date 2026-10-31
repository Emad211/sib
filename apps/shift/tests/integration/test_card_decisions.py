"""The manager's card decides on the card itself and carries the three C1 sections (owner document, S01 §6).

Through the real routes: an over-cap withdrawal arrives with its approve and reject buttons, the
rejection is written through the phone's one decision endpoint and does **not** put the money back
(ADR-019), and once decided the button is gone from the next card. The card also counts the day's
events per code, highlights an assigned repair past its deadline, and draws a seeded sample of patient
registrations for a control call. No page shows a reference figure or a raw id (ADR-011).
Every value is invented (ADR-006).
"""

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from clocks import FixedClock
from fastapi.testclient import TestClient

from event_fixtures import build_event
from shift.adapters.db import mirror_repo, projections_repo
from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.common.ids import new_ulid
from shift.config.settings import Settings
from shift.domain.shift_clock import covering
from shift.services.event_store import EventStore

CARD_DAY = date(2026, 10, 27)  # covers Monday 2026-10-26
MONDAY = datetime(2026, 10, 26, 10, 0, tzinfo=TEHRAN)
OPENING = 5_000_000
WITHDRAWN = 700_000
META_TOKEN = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


@pytest.fixture()
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        state = client.app.state
        seed_roster(state.database, pepper=state.secret_key)
        store = EventStore(state.database, clock=FixedClock(MONDAY))
        shift = covering(MONDAY)
        ids: dict[str, str] = {}

        def put(key: str, event_type: str, payload: dict, actor: str, *, ref: str | None = None) -> None:
            person = ROLES[actor]
            ids[key] = store.append(
                build_event(
                    event_type,
                    payload=payload,
                    shift_id=shift.text,
                    actor_id=person.staff_id,
                    device_id=person.device_id,
                    client_at=MONDAY,
                    work_date=shift.work_date,
                    ref_event_id=ref,
                    idempotency_key=f"c1-{key}",
                )
            ).event.event_id

        put("opening", "handover.started", {"opening_cash": OPENING}, "reception")
        put(
            "withdrawal",
            "cash.withdrawn",
            {
                "amount": WITHDRAWN,
                "recipient_id": ROLES["nurse"].staff_id,
                "reason": "خرید تجهیزات",
                "manager_id": ROLES["manager"].staff_id,
            },
            "reception",
        )
        put("fault", "equipment.fault_reported", {"device": "دستگاه فشار", "description": "کار نمی‌کند"}, "nurse")
        put(
            "assigned",
            "equipment.fault_assigned",
            {"assignee_id": ROLES["storekeeper"].staff_id, "due_on": "2026-10-20"},
            "manager",
            ref=ids["fault"],
        )
        with state.database.write() as connection:
            mirror_repo.insert_registrations(
                connection,
                [
                    {
                        "patient_id": str(700 + number),
                        "created_by": "acc-user",
                        "created_at": "2026-10-26T07:00:00+00:00",
                        "has_valid_nid": 1,
                        "has_valid_phone": 1,
                        "is_foreign": 0,
                        "ingested_at": "2026-10-26T20:00:00+00:00",
                    }
                    for number in range(10)
                ],
            )
        state.projections.sync()
        client.ids = ids  # type: ignore[attr-defined]
        yield client


def _sign_in(client: TestClient, role: str) -> str:
    person = ROLES[role]
    assert login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN).status_code == 200
    token = META_TOKEN.search(client.get("/").text)
    assert token is not None
    return token.group(1)


def _card(client: TestClient) -> str:
    assert client.app.state.daily_card.regenerate(CARD_DAY) is not None
    page = client.get("/manager/card")
    assert page.status_code == 200, page.text
    return page.text


def _box(client: TestClient) -> int:
    with client.app.state.database.read() as connection:
        rows = projections_repo.cash_rows(connection)
    return next(int(row["expected"]) for row in rows if row["cash_box_id"] == "reception")


def _decide(client: TestClient, token: str, event_id: str, event_type: str):
    key = new_ulid()
    return client.post(
        "/api/v1/events",
        json={
            "type": event_type,
            "ref_event_id": event_id,
            "payload": {},
            "client_at": datetime.now(UTC).isoformat(),
            "client_event_id": key,
            "idempotency_key": key,
        },
        headers={"X-CSRFToken": token},
    )


def test_an_over_cap_withdrawal_is_decided_on_the_card_and_rejecting_it_returns_no_money(app: TestClient) -> None:
    token = _sign_in(app, "manager")
    withdrawal = app.ids["withdrawal"]  # type: ignore[attr-defined]
    before = _box(app)
    page = _card(app)
    assert f'data-event-id="{withdrawal}"' in page
    assert "رد کردن پول را به صندوق برنمی‌گرداند" in page

    response = _decide(app, token, withdrawal, "event.rejected")

    assert response.status_code == 200, response.text
    assert _box(app) == before, "ADR-019: a rejection never returns the money to the box"
    assert f'data-event-id="{withdrawal}"' not in _card(app), "a decided withdrawal loses its buttons"
    assert app.app.state.projections.rebuild().drift == ()
    assert _box(app) == before


def test_a_phone_that_is_not_the_manager_cannot_decide_the_withdrawal(app: TestClient) -> None:
    token = _sign_in(app, "reception")

    response = _decide(app, token, app.ids["withdrawal"], "event.approved")  # type: ignore[attr-defined]

    assert response.status_code == 422


def test_the_card_counts_events_per_code_highlights_the_overdue_repair_and_samples_registrations(
    app: TestClient,
) -> None:
    _sign_in(app, "manager")
    page = _card(app)

    assert "رویدادهای ۲۴ ساعت" in page and "<details" in page, "each code opens its details"
    assert "ارجاع‌های مهلت‌گذشته" in page
    assert "دستگاه فشار" in page and "روز دیرتر از مهلت" in page
    assert "نمونهٔ تماس" in page
    shown = re.findall(r"پرونده (\d+)", page)
    assert len(shown) == 3 and len(set(shown)) == 3
    assert _card(app) == page or re.findall(r"پرونده (\d+)", _card(app)) == shown, "the draw is stable per card day"


def test_the_card_page_shows_no_raw_id_and_no_reference(app: TestClient) -> None:
    _sign_in(app, "manager")
    page = _card(app)

    for raw in ("ho-reception", 'mg-id" dir="ltr"', "شناسهٔ سفارش", "شناسهٔ دریافت"):
        assert raw not in page
    assert str(OPENING) not in page and f"{OPENING:,}" not in page
