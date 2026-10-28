"""Database core: WAL pragmas and one serialized writer (gap S2)."""

import threading

import pytest

from shift.adapters.db.core import BUSY_TIMEOUT_MS, Database


def test_every_connection_gets_the_wal_writer_contract(database: Database) -> None:
    with database.read() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one().lower() == "wal"
        assert int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()) == BUSY_TIMEOUT_MS
        assert int(connection.exec_driver_sql("PRAGMA synchronous").scalar_one()) == 1  # NORMAL
        assert int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()) == 1


def test_write_transaction_rolls_back_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with database.write() as connection:
            connection.exec_driver_sql("UPDATE meta_projection SET last_seq = 42 WHERE name = 'proj_stock'")
            raise RuntimeError("boom")

    with database.read() as connection:
        assert (
            int(connection.exec_driver_sql("SELECT last_seq FROM meta_projection WHERE name='proj_stock'").scalar_one())
            == 0
        )


def test_concurrent_writers_are_serialized_by_the_single_writer_pool(database: Database) -> None:
    with database.write() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE writer_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, worker INTEGER NOT NULL)"
        )

    errors: list[BaseException] = []

    def append(worker: int) -> None:
        try:
            with database.write() as connection:
                connection.exec_driver_sql("INSERT INTO writer_probe (worker) VALUES (?)", (worker,))
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with database.read() as connection:
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM writer_probe").scalar_one()) == 8
