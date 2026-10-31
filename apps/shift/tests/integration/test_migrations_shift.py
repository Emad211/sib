"""Versioned migrations: fresh, idempotent, backed up and rollback-safe (step 2).

The handover slice adds v9 (the persisted flows, `proj_cash` keyed by cash box, `events.handover_id`)
and the baseline slice adds v10 (the `handover_custody` read model), and the campaign slice adds v12
(the manager's campaign calendar). These tests pin both directions — a fresh install and an existing
v8 database whose cash rows and event hashes must survive untouched, a v9 database that gains the
custody table without losing a flow, and a v11 database that gains `campaigns` without touching a
single event.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import URL

from shift.adapters.db import events_repo, handover_repo, migrations, mirror_repo, projections_repo
from shift.adapters.db.core import Database
from shift.adapters.db.migrations import MigrationError, run_migrations
from shift.common.clock import tehran_iso, utc_iso
from shift.common.hashing import GENESIS_HASH, canonical_json
from shift.common.ids import new_ulid
from shift.domain.event_hash import event_hash
from shift.domain.handover_fsm import HandoverFlow
from shift.services.event_chain import verify_chain

CLIENT_AT = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)  # 13:30 Tehran
V8_EVENT_COLUMNS = (
    "event_id",
    "type",
    "schema_v",
    "payload",
    "actor_id",
    "device_id",
    "shift_id",
    "work_date",
    "client_at",
    "server_at",
    "at_tehran",
    "ref_event_id",
    "idempotency_key",
    "prev_hash",
    "hash",
)


def _tables(database: Database) -> set[str]:
    with database.read() as connection:
        rows = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        return {str(row[0]) for row in rows}


def _columns(database: Database, table: str) -> tuple[str, ...]:
    with database.read() as connection:
        return tuple(str(row[1]) for row in connection.exec_driver_sql(f"PRAGMA table_info({table})"))


def _version(database: Database) -> int:
    with database.read() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def _migrate_to(path: Path, version: int) -> None:
    """Build a database the way an installed older version left it."""
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for number, _name, up in migrations.MIGRATIONS:
            if number <= version:
                up(connection)
        connection.exec_driver_sql(f"PRAGMA user_version = {int(version)}")
    engine.dispose()


def _write_v8_event(connection: Connection) -> str:
    """One event exactly as the v8 writer stored it, hash chain included; returns its hash."""
    values = {
        "event_id": new_ulid(CLIENT_AT),
        "type": "handover.started",
        "schema_v": 1,
        "payload": canonical_json({"opening_cash": 1_000_000}),
        "actor_id": "actor-1",
        "device_id": "device-1",
        "shift_id": "2026-09-24#1",
        "work_date": "2026-09-24",
        "client_at": CLIENT_AT.isoformat(timespec="microseconds"),
        "server_at": utc_iso(CLIENT_AT),
        "at_tehran": tehran_iso(CLIENT_AT),
        "ref_event_id": None,
        "idempotency_key": "v8-event-1",
        "prev_hash": GENESIS_HASH,
        "hash": "",
    }
    values["hash"] = event_hash(GENESIS_HASH, values)
    connection.exec_driver_sql(
        f"INSERT INTO events ({', '.join(V8_EVENT_COLUMNS)}) VALUES ({', '.join('?' for _ in V8_EVENT_COLUMNS)})",
        tuple(values[column] for column in V8_EVENT_COLUMNS),
    )
    return str(values["hash"])


def test_fresh_database_is_created_at_latest_version(tmp_path: Path) -> None:
    database = Database(tmp_path / "fresh.db")
    try:
        report = database.migrate()

        assert report.from_version == 0
        assert report.to_version == migrations.LATEST
        assert report.applied == [f"{v:04d}_{name}" for v, name, _ in migrations.MIGRATIONS]
        assert report.backup is None
        assert _version(database) == migrations.LATEST
        assert {"meta_projection", "proj_stock", "proj_cash", "events"} <= _tables(database)
    finally:
        database.close()


def test_migrations_are_idempotent_and_do_not_back_up_again(database: Database, tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    report = database.migrate(backup_dir=backups)

    assert report.applied == []
    assert report.backup is None
    assert not backups.exists() or not any(backups.iterdir())


def test_existing_database_is_backed_up_before_migrating(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
    legacy.execute("INSERT INTO legacy (note) VALUES ('keep me')")
    legacy.commit()
    legacy.close()

    backups = tmp_path / "backups"
    database = Database(path)
    try:
        report = database.migrate(backup_dir=backups)

        assert report.backup is not None
        saved_path = Path(report.backup)
        assert saved_path.exists()
        saved = sqlite3.connect(saved_path)
        try:
            assert saved.execute("PRAGMA user_version").fetchone()[0] == 0
            assert saved.execute("SELECT note FROM legacy").fetchone()[0] == "keep me"
        finally:
            saved.close()
        assert _version(database) == migrations.LATEST
        with database.read() as connection:
            assert connection.exec_driver_sql("SELECT note FROM legacy").scalar_one() == "keep me"
    finally:
        database.close()


def test_the_pre_migration_snapshot_restores_on_its_own(tmp_path: Path) -> None:
    """A live WAL source must not leave a WAL flag in the copy: the snapshot is one file (F6)."""
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.execute("PRAGMA journal_mode = WAL")
    legacy.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
    legacy.execute("INSERT INTO legacy (note) VALUES ('keep me')")
    legacy.commit()
    legacy.close()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
    finally:
        database.close()

    assert report.backup is not None
    saved = Path(report.backup)
    assert not Path(f"{saved}-wal").exists()
    assert not Path(f"{saved}-shm").exists()
    restored = tmp_path / "restored"
    restored.mkdir()
    restored_file = restored / saved.name
    restored_file.write_bytes(saved.read_bytes())
    alone = sqlite3.connect(f"{restored_file.as_uri()}?mode=ro", uri=True)
    try:
        assert alone.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        assert alone.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert alone.execute("SELECT note FROM legacy").fetchone()[0] == "keep me"
    finally:
        alone.close()


def test_failed_migration_rolls_back_and_raises(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(connection: Connection) -> None:
        connection.exec_driver_sql("ALTER TABLE proj_stock ADD COLUMN half_done INTEGER")
        raise RuntimeError("boom")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS, (migrations.LATEST + 1, "broken", broken)])

    with pytest.raises(MigrationError, match="rolled back"):
        database.migrate()

    assert _version(database) == migrations.LATEST
    with database.read() as connection:
        columns = {str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(proj_stock)")}
    assert "half_done" not in columns


def test_backup_failure_stops_the_upgrade(tmp_path: Path) -> None:
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(tmp_path / "empty.db")))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        with pytest.raises(MigrationError, match="backup failed"):
            run_migrations(connection, backup=lambda: (_ for _ in ()).throw(OSError("disk full")))
        assert int(connection.exec_driver_sql("PRAGMA user_version").scalar_one()) == 0
    engine.dispose()


def test_database_newer_than_code_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "newer.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {migrations.LATEST + 5}")
    raw.commit()
    raw.close()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
        assert report.newer_than_code is True
        assert report.applied == []
        assert report.backup is None
        assert _version(database) == migrations.LATEST + 5
    finally:
        database.close()


def test_a_fresh_database_gets_the_handover_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "fresh.db")
    try:
        database.migrate()
        assert _version(database) == migrations.LATEST == 20
        assert {"handover", "handover_custody", "daily_cards", "campaigns", "reward_rates"} <= _tables(database)
        assert _columns(database, "proj_cash") == ("cash_box_id", "expected", "counted", "variance")
        assert _columns(database, "handover_custody") == ("flow", "custodian_id", "updated_at", "updated_by")
        assert "acc_user" in _columns(database, "staff")
        assert _columns(database, "campaigns") == (
            "campaign_id",
            "name",
            "service",
            "amount_toman",
            "starts_on",
            "ends_on",
            "created_by",
            "created_at",
        )
        assert "handover_id" in _columns(database, "events")
        with database.read() as connection:
            indexes = {str(row[0]) for row in connection.exec_driver_sql("SELECT name FROM sqlite_master")}
            assert {
                "idx_handover_signed_at",
                "idx_handover_flow_opened_at",
                "idx_events_handover_seq",
            } <= indexes
            assert projections_repo.cash_rows(connection) == []
    finally:
        database.close()


def test_a_v8_database_upgrades_in_place_keeping_its_cash_figure_and_its_hashes(tmp_path: Path) -> None:
    """v9 copies the newest per-shift cash row onto the single box and never rewrites an event."""
    path = tmp_path / "v8.db"
    _migrate_to(path, 8)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        old_hash = _write_v8_event(connection)
        connection.exec_driver_sql(
            "INSERT INTO proj_cash (shift_id, expected, counted, variance) VALUES "
            "('2026-09-24#1', 1050000, 900000, -150000), ('2026-09-24#2', 500000, 480000, -20000)"
        )
    engine.dispose()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
        assert (report.from_version, report.to_version) == (8, migrations.LATEST)
        assert report.backup is not None
        with database.read() as connection:
            cash = projections_repo.cash_rows(connection)
            events = list(events_repo.iter_events(connection))
            chain = verify_chain(events)
            mirrored = mirror_repo.count(connection, mirror_repo.CASH)
        assert _columns(database, "proj_cash") == ("cash_box_id", "expected", "counted", "variance")
        assert cash == [{"cash_box_id": "reception", "expected": 500_000, "counted": 480_000, "variance": -20_000}]
        assert [event.handover_id for event in events] == [None]
        assert events[0].hash == old_hash
        assert chain.ok is True and chain.checked == 1
        assert mirrored == 0
    finally:
        database.close()


def test_a_v9_database_gains_the_custody_table_and_keeps_its_flows(tmp_path: Path) -> None:
    """v10 is additive: the upgrade leaves every stored flow, its cutoff and its hashes as they were."""
    path = tmp_path / "v9.db"
    _migrate_to(path, 9)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        old_hash = _write_v8_event(connection)
        connection.exec_driver_sql(
            "INSERT INTO handover (handover_id, shift_id, flow, opened_at, work_date, outgoing_id, incoming_id, "
            "created_at) VALUES ('flow-1', '2026-09-24#1', 'reception', ?, '2026-09-24', 'staff-out', 'staff-in', ?)",
            (utc_iso(CLIENT_AT), utc_iso(CLIENT_AT)),
        )
    engine.dispose()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
        assert (report.from_version, report.to_version) == (9, migrations.LATEST)
        with database.read() as connection:
            flows = handover_repo.open_flows(connection)
            events = list(events_repo.iter_events(connection))
            custodian = handover_repo.custodian(connection, HandoverFlow.RECEPTION)
        assert [flow.handover_id for flow in flows] == ["flow-1"]
        assert events[0].hash == old_hash
        assert verify_chain(events).ok is True
        assert custodian is None, "a v9 install has no baseline yet; the manager records the first one"
    finally:
        database.close()


def test_a_v11_database_gains_the_campaign_table_and_keeps_its_events(tmp_path: Path) -> None:
    """v12 is one new table and nothing else: a v11 install keeps every event and every hash."""
    path = tmp_path / "v11.db"
    _migrate_to(path, 11)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        old_hash = _write_v8_event(connection)
    engine.dispose()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
        assert (report.from_version, report.to_version) == (11, migrations.LATEST)
        with database.read() as connection:
            events = list(events_repo.iter_events(connection))
            campaigns = connection.exec_driver_sql("SELECT COUNT(*) FROM campaigns").scalar_one()
        assert [event.hash for event in events] == [old_hash]
        assert verify_chain(events).ok is True
        assert campaigns == 0, "the migration creates the table and never invents a campaign"
    finally:
        database.close()


def test_a_v12_database_gains_the_reward_rate_table_and_keeps_its_events(tmp_path: Path) -> None:
    """v13 is one table plus one nullable `staff` column: a v12 install keeps every event and hash."""
    path = tmp_path / "v12.db"
    _migrate_to(path, 12)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        old_hash = _write_v8_event(connection)
        connection.exec_driver_sql(
            "INSERT INTO staff (staff_id, full_name, mobile, pin_hash, pin_updated_at, created_at) "
            "VALUES ('staff-1', 'پرستار یک', '09120000002', 'digest', ?, ?)",
            (utc_iso(CLIENT_AT), utc_iso(CLIENT_AT)),
        )
    engine.dispose()

    database = Database(path)
    try:
        report = database.migrate(backup_dir=tmp_path / "backups")
        assert (report.from_version, report.to_version) == (12, migrations.LATEST)
        with database.read() as connection:
            events = list(events_repo.iter_events(connection))
            rates = connection.exec_driver_sql("SELECT COUNT(*) FROM reward_rates").scalar_one()
            unmapped = connection.exec_driver_sql("SELECT acc_user FROM staff WHERE staff_id = 'staff-1'").one()[0]
        assert [event.hash for event in events] == [old_hash]
        assert verify_chain(events).ok is True
        assert rates == 0, "the migration creates the table and invents no rate"
        assert unmapped is None, "an unmapped person stays unmapped; the manager maps him himself"
    finally:
        database.close()
