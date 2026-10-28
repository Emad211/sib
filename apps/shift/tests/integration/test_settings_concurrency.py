"""Concurrent writers serialize on the single-writer transaction, so retries stay idempotent."""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from clocks import FixedClock, inside_shift

from shift.adapters.db.core import Database
from shift.domain.policy import SettingKey, ShiftKey
from shift.services.settings import SettingChange, SettingsService

DAY = date(2026, 10, 28)
EVENING = ShiftKey(DAY, 2)
NIGHT = ShiftKey(DAY, 3)


def _change_in_parallel(path: Path, value: str) -> list[SettingChange]:
    barrier = threading.Barrier(2)

    def change() -> SettingChange:
        database = Database(path)
        try:
            service = SettingsService(database, clock=FixedClock(inside_shift(DAY, 2)))
            barrier.wait(timeout=10)
            return service.change(SettingKey.SESSION_TTL_HOURS, value, actor_id="staff-manager", running_shift=EVENING)
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return [future.result(timeout=30) for future in [pool.submit(change) for _ in range(2)]]


def test_two_writers_of_the_same_value_create_one_version(database: Database, tmp_path: Path) -> None:
    database.close()

    results = _change_in_parallel(tmp_path / "shift-test.db", "6")

    assert sorted(result.created for result in results) == [False, True]
    reader = Database(tmp_path / "shift-test.db")
    try:
        history = SettingsService(reader).history(SettingKey.SESSION_TTL_HOURS)
    finally:
        reader.close()
    assert [(value.version, value.value) for value in history] == [(2, "6"), (1, "12")]
    assert {result.value.effective_from_shift for result in results} == {NIGHT}


def test_two_writers_of_different_values_both_land(database: Database, tmp_path: Path) -> None:
    database.close()
    barrier = threading.Barrier(2)

    def change(value: str) -> Callable[[], SettingChange]:
        def run() -> SettingChange:
            database = Database(tmp_path / "shift-test.db")
            try:
                service = SettingsService(database, clock=FixedClock(inside_shift(DAY, 2)))
                barrier.wait(timeout=10)
                return service.change(
                    SettingKey.SESSION_TTL_HOURS, value, actor_id="staff-manager", running_shift=EVENING
                )
            finally:
                database.close()

        return run

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=30) for future in [pool.submit(change(value)) for value in ("6", "8")]]

    assert [result.created for result in results] == [True, True]
    reader = Database(tmp_path / "shift-test.db")
    try:
        history = SettingsService(reader).history(SettingKey.SESSION_TTL_HOURS)
    finally:
        reader.close()
    assert [value.version for value in history] == [3, 2, 1]
    assert {value.value for value in history} == {"8", "6", "12"}
    assert {result.value.effective_from_shift for result in results} == {NIGHT}
