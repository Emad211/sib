"""The event log is append-only and enforces actor/device/type contracts (R-03, R-04)."""

from dataclasses import replace

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from event_fixtures import build_event
from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.common.hashing import GENESIS_HASH
from shift.domain.events import UnknownEventType
from shift.services.event_store import EventStore, EventValidationError


def test_append_stores_server_authority_and_canonical_payload(store: EventStore, database: Database) -> None:
    result = store.append(build_event("handover.started"))

    assert result.created is True
    assert result.event.seq == 1
    assert result.event.payload == {"opening_cash": 1_000_000}
    assert result.event.client_at.endswith("+00:00")
    assert result.event.server_at.endswith("Z")
    assert result.event.at_tehran.endswith("+03:30")
    assert result.event.prev_hash == GENESIS_HASH
    assert len(result.event.hash) == 64

    second = store.append(build_event("acc.cash_receipt", shift_id="shift-1"))
    assert second.event.seq == 2
    assert second.event.prev_hash == result.event.hash


def test_s3_alias_columns_mirror_the_authoritative_ones(database: Database, store: EventStore) -> None:
    store.append(build_event("handover.started"))
    with database.read() as connection:
        row = (
            connection.exec_driver_sql("SELECT event_id, client_event_id, server_at, at_utc FROM events WHERE seq = 1")
            .mappings()
            .one()
        )

    assert row["client_event_id"] == row["event_id"]
    assert row["at_utc"] == row["server_at"]


def test_update_is_rejected_by_the_trigger(database: Database, store: EventStore) -> None:
    store.append(build_event("handover.started"))

    with pytest.raises(IntegrityError, match="append-only"):
        with database.write() as connection:
            connection.exec_driver_sql("UPDATE events SET actor_id = 'tampered' WHERE seq = 1")


def test_delete_is_rejected_by_the_trigger(database: Database, store: EventStore) -> None:
    store.append(build_event("handover.started"))

    with pytest.raises(IntegrityError, match="append-only"):
        with database.write() as connection:
            connection.exec_driver_sql("DELETE FROM events WHERE seq = 1")


def test_unknown_event_type_is_rejected_without_a_row(store: EventStore, database: Database) -> None:
    unknown = replace(build_event("handover.started"), type="unknown.event")

    with pytest.raises(UnknownEventType):
        store.append(unknown)

    with database.read() as connection:
        assert events_repo.count_events(connection) == 0


def test_actor_and_device_are_required(store: EventStore) -> None:
    with pytest.raises(EventValidationError, match="actor_id"):
        store.append(build_event("handover.started", actor_id="  "))
    with pytest.raises(EventValidationError, match="actor_id"):
        store.append(build_event("handover.started", device_id=""))


def test_cash_events_require_a_shift(store: EventStore) -> None:
    with pytest.raises(EventValidationError, match="requires shift_id"):
        store.append(build_event("handover.started", shift_id=None))


def test_invalid_payload_is_rejected(store: EventStore, database: Database) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        store.append(build_event("cash.withdrawn", payload={"amount": -1}))

    with database.read() as connection:
        assert events_repo.count_events(connection) == 0


def test_triggers_and_indexes_exist(database: Database) -> None:
    with database.read() as connection:
        triggers = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")}
        indexes = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")}

    assert {"events_no_update", "events_no_delete"} <= triggers
    assert {"idx_events_shift_seq", "idx_events_type_seq"} <= indexes
