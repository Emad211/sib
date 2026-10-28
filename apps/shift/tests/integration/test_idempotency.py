"""Retries and outbox replays produce exactly one event (gap S3, ADR-010)."""

from event_fixtures import build_event
from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.services.event_store import EventStore


def test_replaying_the_same_event_returns_its_original_seq(store: EventStore, database: Database) -> None:
    event = build_event("handover.started")

    first = store.append(event)
    second = store.append(event)

    assert first.created is True
    assert second.created is False
    assert second.event.seq == first.event.seq
    with database.read() as connection:
        assert events_repo.count_events(connection) == 1


def test_idempotency_key_wins_even_with_a_new_client_event_id(store: EventStore, database: Database) -> None:
    first = store.append(build_event("acc.cash_receipt", idempotency_key="outbox-1"))
    retry = store.append(build_event("acc.cash_receipt", idempotency_key="outbox-1"))

    assert retry.created is False
    assert retry.event.seq == first.event.seq
    with database.read() as connection:
        assert events_repo.count_events(connection) == 1


def test_duplicate_inside_one_batch_inserts_once(store: EventStore, database: Database) -> None:
    event = build_event("handover.item_counted")

    results = store.append_many([event, event])

    assert [result.created for result in results] == [True, False]
    assert results[0].event.seq == results[1].event.seq
    with database.read() as connection:
        assert events_repo.count_events(connection) == 1


def test_retry_after_a_store_restart_still_deduplicates(database: Database) -> None:
    event = build_event("handover.cash_counted")
    first = EventStore(database).append(event)
    second = EventStore(database).append(event)

    assert second.created is False
    assert second.event.seq == first.event.seq
