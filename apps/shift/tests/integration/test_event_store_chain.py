"""Hash-chain integrity and first-break reporting (R-03)."""

import threading

from event_fixtures import build_event
from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.services.event_chain import verify_chain
from shift.services.event_store import EventStore


def _records(database: Database):
    with database.read() as connection:
        return list(events_repo.iter_events(connection))


def test_clean_chain_verifies(store: EventStore, database: Database) -> None:
    store.append_many(
        [
            build_event("handover.started"),
            build_event("acc.cash_receipt"),
            build_event("handover.item_counted"),
        ]
    )

    report = verify_chain(_records(database))

    assert report.ok
    assert report.checked == 3


def test_tampered_payload_is_reported_at_its_seq(database: Database, store: EventStore) -> None:
    store.append_many([build_event("handover.started"), build_event("acc.cash_receipt")])
    with database.write() as connection:
        connection.exec_driver_sql("DROP TRIGGER events_no_update")
        connection.exec_driver_sql("UPDATE events SET payload = '{\"amount\":1}' WHERE seq = 2")

    report = verify_chain(_records(database))

    assert report.ok is False
    assert report.broken is not None
    assert report.broken.seq == 2
    assert "hash" in report.broken.reason


def test_deleted_middle_event_breaks_the_link_at_the_next_seq(database: Database, store: EventStore) -> None:
    store.append_many(
        [
            build_event("handover.started"),
            build_event("acc.cash_receipt"),
            build_event("handover.cash_counted"),
        ]
    )
    with database.write() as connection:
        connection.exec_driver_sql("DROP TRIGGER events_no_delete")
        connection.exec_driver_sql("DELETE FROM events WHERE seq = 2")

    report = verify_chain(_records(database))

    assert report.ok is False
    assert report.broken is not None
    assert report.broken.seq == 3
    assert "prev_hash" in report.broken.reason


def test_chain_can_be_disabled_for_verification(database: Database) -> None:
    store = EventStore(database, hash_chain=False)
    store.append_many([build_event("handover.started"), build_event("acc.cash_receipt")])

    assert verify_chain(_records(database)).ok is False
    assert verify_chain(_records(database), require_links=False).ok is True
    assert all(record.prev_hash == "" for record in _records(database))


def test_concurrent_appends_keep_the_chain_linear(database: Database) -> None:
    store = EventStore(database)
    errors: list[BaseException] = []

    def append_batch(worker: int) -> None:
        try:
            for index in range(10):
                store.append(build_event("acc.cash_receipt", idempotency_key=f"worker-{worker}-{index}"))
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=append_batch, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    report = verify_chain(_records(database))
    assert report.ok
    assert report.checked == 40
