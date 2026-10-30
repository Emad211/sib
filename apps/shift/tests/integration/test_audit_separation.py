"""Audit records are durable, append-only and never enter the public log (step 14, S02 §3.2)."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from shift.adapters.db import audit_repo, migrations
from shift.adapters.db.core import Database
from shift.config.logging import AUDIT_LOGGER, PUBLIC_LOGGER, configure_logging
from shift.config.settings import Settings
from shift.services.audit import AuditEntry, AuditError, AuditOutcome, AuditService

REFERENCE = "REF-424242"  # a blind-count reference number: audit-only, never public (R-02)


class FrozenClock:
    """A clock pinned to one instant so Tehran rendering can be asserted exactly."""

    def now_utc(self) -> datetime:
        return datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _flush() -> None:
    for name in (PUBLIC_LOGGER, AUDIT_LOGGER):
        for handler in logging.getLogger(name).handlers:
            handler.flush()


def test_audit_records_reach_the_audit_file_and_never_the_public_log(database: Database, settings: Settings) -> None:
    configure_logging(settings)

    AuditService(database).record(
        AuditEntry(
            actor_id="manager-1", action="handover.count.checked", subject_id="shift-1", detail={"reference": REFERENCE}
        )
    )

    _flush()
    public = (settings.log_dir / "shift.log").read_text(encoding="utf-8")
    audit = settings.audit_log_path.read_text(encoding="utf-8")
    assert REFERENCE in audit
    assert "handover.count.checked" in audit
    assert REFERENCE not in public
    assert "handover.count.checked" not in public


def test_audit_rows_are_stored_apart_from_the_event_log(database: Database) -> None:
    service = AuditService(database, clock=FrozenClock())

    row = service.record(
        AuditEntry(
            actor_id="manager-1", action="backup.created", detail={"snapshot": "shift_20261028T120000000000Z.db"}
        )
    )

    assert row.at_utc == "2026-10-28T12:00:00.000000Z"
    assert row.at_tehran == "2026-10-28T15:30:00.000000+03:30"
    assert row.outcome == "ok"
    with database.read() as connection:
        assert audit_repo.count_entries(connection) == 1
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar_one()) == 0
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM proj_cash").scalar_one()) == 0
        detail = connection.exec_driver_sql("SELECT detail FROM audit_log WHERE id = ?", (row.id,)).scalar_one()
    assert json.loads(detail) == {"snapshot": "shift_20261028T120000000000Z.db"}


def test_audit_rows_cannot_be_updated_or_deleted(database: Database) -> None:
    AuditService(database).record(AuditEntry(actor_id="manager-1", action="audit.probe"))

    with database.write() as connection:
        with pytest.raises(IntegrityError, match="append-only"):
            connection.exec_driver_sql("UPDATE audit_log SET actor_id = 'other' WHERE id = 1")
    with database.write() as connection:
        with pytest.raises(IntegrityError, match="append-only"):
            connection.exec_driver_sql("DELETE FROM audit_log")
    with database.read() as connection:
        assert audit_repo.count_entries(connection) == 1


@pytest.mark.parametrize("field", ["pin", "token", "password", "pubkey"])
def test_audit_never_stores_credentials(database: Database, field: str) -> None:
    service = AuditService(database)

    with pytest.raises(AuditError, match="credentials"):
        service.record(AuditEntry(actor_id="manager-1", action="auth.login", detail={field: "123456"}))

    with database.read() as connection:
        assert audit_repo.count_entries(connection) == 0


@pytest.mark.parametrize("field", ["PIN", "Password", "TOKEN", "PubKey", "Authorization", "Secret"])
def test_audit_never_stores_credentials_whatever_the_key_case(database: Database, field: str) -> None:
    service = AuditService(database)

    with pytest.raises(AuditError, match="credentials"):
        service.record(AuditEntry(actor_id="manager-1", action="auth.login", detail={field: "123456"}))

    with database.read() as connection:
        assert audit_repo.count_entries(connection) == 0


@pytest.mark.parametrize(
    "detail",
    [
        {"user": {"Password": "hunter2"}},
        {"request": {"headers": {"Authorization": "Bearer opaque"}}},
        {"accounts": [{"token": "issued"}]},
    ],
)
def test_audit_never_stores_nested_credentials(database: Database, detail: dict[str, object]) -> None:
    with pytest.raises(AuditError, match="credentials"):
        AuditService(database).record(AuditEntry(actor_id="manager-1", action="auth.login", detail=detail))

    with database.read() as connection:
        assert audit_repo.count_entries(connection) == 0


@pytest.mark.parametrize(("actor_id", "action"), [("", "audit.probe"), ("manager-1", "  ")])
def test_audit_requires_an_actor_and_an_action(database: Database, actor_id: str, action: str) -> None:
    with pytest.raises(AuditError):
        AuditService(database).record(AuditEntry(actor_id=actor_id, action=action))


def test_history_returns_the_newest_records_first_and_can_filter_by_action(database: Database) -> None:
    service = AuditService(database)
    service.record(AuditEntry(actor_id="staff-1", action="backup.created"))
    service.record(
        AuditEntry(
            actor_id="staff-2", action="handover.count.checked", outcome=AuditOutcome.DENIED, subject_id="shift-1"
        )
    )
    service.record(AuditEntry(actor_id="staff-3", action="backup.created"))

    assert [row.actor_id for row in service.history()] == ["staff-3", "staff-2", "staff-1"]
    assert [row.actor_id for row in service.history(action="backup.created", limit=1)] == ["staff-3"]
    denied = service.history(action="handover.count.checked")
    assert [(row.actor_id, row.outcome, row.subject_id) for row in denied] == [("staff-2", "denied", "shift-1")]
    with database.read() as connection:
        assert audit_repo.count_entries(connection, action="backup.created") == 2
        assert audit_repo.count_entries(connection) == 3


def test_migration_five_creates_the_audit_log_and_its_indexes(database: Database) -> None:
    with database.read() as connection:
        version = int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())
        names = {name for _, name, _ in migrations.MIGRATIONS}
        assert (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'audit_log'"
            ).scalar_one()
            == "audit_log"
        )
        indexes = {
            str(row[0]) for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'index'")
        }

    assert version == migrations.LATEST
    assert "audit_log" in names
    assert {"idx_audit_log_at_utc", "idx_audit_log_actor_at", "idx_audit_log_action_id"} <= indexes


def test_the_action_history_query_reads_its_index_without_a_temp_btree(database: Database) -> None:
    """`recent(action=...)` filters and orders through one index, so no sort is built at run time."""
    with database.read() as connection:
        plan = connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN "
            "SELECT id, at_utc, at_tehran, actor_id, action, outcome, subject_id, detail "
            "FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?",
            ("audit.probe", 5),
        ).fetchall()

    steps = " | ".join(str(row[-1]) for row in plan)
    assert "idx_audit_log_action_id" in steps
    assert "TEMP B-TREE" not in steps


def test_two_audit_records_one_second_apart_are_ordered(database: Database) -> None:
    class TickingClock:
        def __init__(self) -> None:
            self._moment = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

        def now_utc(self) -> datetime:
            self._moment += timedelta(seconds=1)
            return self._moment

    service = AuditService(database, clock=TickingClock())
    first = service.record(AuditEntry(actor_id="staff-1", action="audit.probe"))
    second = service.record(AuditEntry(actor_id="staff-2", action="audit.probe"))

    assert first.id < second.id
    assert [row.actor_id for row in service.history(action="audit.probe")] == ["staff-2", "staff-1"]
