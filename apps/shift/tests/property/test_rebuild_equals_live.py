"""SHF-1.2 acceptance: 10 000 random events rebuild to the live projection state."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from event_fixtures import random_events
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine


@given(seed=st.integers(min_value=0, max_value=10_000))
@settings(
    max_examples=1,
    deadline=None,
    derandomize=True,
    suppress_health_check=list(HealthCheck),
)
def test_rebuild_equals_live_over_10_000_random_events(
    seed: int, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    events = random_events(seed, 10_000)
    assert len(events) == 10_000

    results = store.append_many(events)
    assert len(results) == 10_000
    assert all(result.created for result in results)
    assert projections.sync().processed == 10_000

    with database.read() as connection:
        live_stock = projections_repo.stock_rows(connection)
        live_cash = projections_repo.cash_rows(connection)
        live_checksums = {
            name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS
        }

    check = projections.rebuild(dry_run=True)

    assert check.events == 10_000
    assert check.drift == ()
    assert check.live_checksums == live_checksums
    assert check.rebuilt_checksums == live_checksums

    applied = projections.rebuild()

    assert applied.events == 10_000
    assert applied.drift == ()
    with database.read() as connection:
        assert projections_repo.stock_rows(connection) == live_stock
        assert projections_repo.cash_rows(connection) == live_cash
        assert {
            name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS
        } == live_checksums
