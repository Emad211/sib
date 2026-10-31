"""Outbox business-date rules: a night event belongs to the shift that opened it."""

from datetime import UTC, date, datetime

import pytest

from shift.common.clock import parse_iso, to_tehran
from shift.common.ids import new_ulid
from shift.domain.shift_clock import covering
from shift.services.outbox import EventSubmission, OutboxValidationError, business_date_for


def _submission(**overrides: object) -> EventSubmission:
    values: dict[str, object] = {
        "type": "stock.waste_reported",
        "payload": {"item_id": "bandage_roll", "location_id": "emergency_cabinet", "quantity": 1},
        "actor_id": "staff-1",
        "device_id": "device-1",
        "client_at": "2026-10-29T01:00:00+03:30",
        "client_event_id": new_ulid(),
    }
    values.update(overrides)
    return EventSubmission(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        ("2026-10-29T01:00:00+03:30", date(2026, 10, 28)),
        ("2026-10-29T07:29:00+03:30", date(2026, 10, 28)),
        ("2026-10-29T07:30:00+03:30", date(2026, 10, 29)),
        ("2026-10-29T19:29:00+03:30", date(2026, 10, 29)),
    ],
)
def test_a_night_event_belongs_to_the_date_its_shift_opened(moment: str, expected: date) -> None:
    assert business_date_for(parse_iso(moment)) == expected


def test_without_a_session_shift_the_submission_derives_the_business_date() -> None:
    """The service stays usable outside the HTTP route that supplies the shift date."""
    event = _submission().to_new_event()

    assert event.work_date == date(2026, 10, 28)


def test_a_work_date_hint_never_outranks_the_occurred_instant() -> None:
    """F5: a queue that crossed midnight is attributed by when it happened, not by its hint."""
    event = _submission(requested_work_date="2026-10-01").to_new_event()

    assert event.work_date == date(2026, 10, 28)
    assert event.shift_id == covering(to_tehran(parse_iso("2026-10-29T01:00:00+03:30"))).text


def test_a_malformed_work_date_hint_is_still_refused() -> None:
    with pytest.raises(OutboxValidationError, match="work_date"):
        _submission(requested_work_date="not-a-date").to_new_event()


def test_a_naive_client_time_is_rejected() -> None:
    with pytest.raises(OutboxValidationError, match="ISO-8601"):
        _submission(client_at="2026-10-29T01:00:00").to_new_event()


def test_a_client_event_id_is_required() -> None:
    submission = EventSubmission(
        type="stock.waste_reported",
        payload={"item_id": "bandage_roll", "location_id": "emergency_cabinet", "quantity": 1},
        actor_id="staff-1",
        device_id="device-1",
        client_at=datetime(2026, 10, 29, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(OutboxValidationError, match="client_event_id"):
        submission.to_new_event()
