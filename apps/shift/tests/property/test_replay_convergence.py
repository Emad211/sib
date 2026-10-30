"""Replaying a batch twice changes nothing; rebuild is idempotent."""

from event_fixtures import random_events
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine


def _checksums(database: Database) -> dict[str, str]:
    with database.read() as connection:
        return {name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS}


def _rows(database: Database):
    with database.read() as connection:
        return projections_repo.stock_rows(connection), projections_repo.cash_rows(connection)


def test_replaying_events_and_rebuilding_converges(
    database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    first = random_events(seed=11, count=250)
    store.append_many(first)
    assert projections.sync().processed == 250
    checksums = _checksums(database)

    replay = store.append_many(first)
    assert all(result.created is False for result in replay)
    assert projections.sync().processed == 0
    assert _checksums(database) == checksums

    second = random_events(seed=12, count=250, key_prefix="second")
    store.append_many(second)
    assert projections.sync().processed == 250
    checksums_after_second = _checksums(database)
    rows_after_second = _rows(database)

    assert projections.rebuild(dry_run=True).drift == ()
    assert projections.rebuild().drift == ()
    assert projections.rebuild().drift == ()
    assert _checksums(database) == checksums_after_second
    assert _rows(database) == rows_after_second


def test_duplicate_batch_does_not_append_rows(
    database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    events = random_events(seed=13, count=100)
    store.append_many(events)
    projections.sync()
    checksums = _checksums(database)

    replay = store.append_many(events)

    assert all(result.created is False for result in replay)
    assert projections.sync().processed == 0
    assert _checksums(database) == checksums
