"""Shared fixtures: a migrated synthetic database per test."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from shift.adapters.db.core import Database


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    """A fresh, migrated database file under pytest's temporary directory."""
    database = Database(tmp_path / "shift-test.db")
    database.migrate()
    try:
        yield database
    finally:
        database.close()
