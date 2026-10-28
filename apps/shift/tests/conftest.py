"""Shared fixtures: a migrated synthetic database and an app bound to temp paths."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shift.adapters.db.core import Database
from shift.app import create_app
from shift.config.settings import Settings
from shift.services.event_store import EventStore


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Production-shaped settings pointing every file at pytest's temp directory."""
    root = tmp_path / "app"
    return Settings(
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".shift_secret_key",
    )


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    """A fresh, migrated database file under pytest's temporary directory."""
    database = Database(tmp_path / "shift-test.db")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def store(database: Database) -> EventStore:
    """An event store on the synthetic database."""
    return EventStore(database)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A TestClient that runs the app lifespan against the synthetic settings."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
