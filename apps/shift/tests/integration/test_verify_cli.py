"""The verify CLI exits 0 on a clean log and 1 with the first broken seq on drift."""

from pathlib import Path

from event_fixtures import build_event
from shift.adapters.db.core import Database
from shift.services.event_store import EventStore
from verify_event_chain import main


def test_clean_database_exits_zero(database: Database, store: EventStore, capsys) -> None:
    store.append_many([build_event("handover.started"), build_event("acc.cash_receipt")])

    code = main(["--db", str(database.path)])

    assert code == 0
    assert "chain ok: 2 events" in capsys.readouterr().out


def test_tampered_database_exits_one_with_the_seq(database: Database, store: EventStore, capsys) -> None:
    store.append(build_event("handover.started"))
    with database.write() as connection:
        connection.exec_driver_sql("DROP TRIGGER events_no_update")
        connection.exec_driver_sql("UPDATE events SET actor_id = 'tampered' WHERE seq = 1")

    code = main(["--db", str(database.path)])

    assert code == 1
    assert "broken at seq 1" in capsys.readouterr().out


def test_missing_database_exits_one(tmp_path: Path, capsys) -> None:
    code = main(["--db", str(tmp_path / "missing.db")])

    assert code == 1
    assert "database not found" in capsys.readouterr().out
