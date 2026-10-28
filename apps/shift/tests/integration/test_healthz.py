"""Liveness and startup contract (steps 1 and 3)."""

from pathlib import Path

from fastapi.testclient import TestClient

from shift.__version__ import __version__
from shift.app import create_app
from shift.config.settings import Settings


def test_healthz_reports_ok(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_startup_migrates_the_database_and_creates_the_secret(settings: Settings) -> None:
    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/healthz").status_code == 200

    assert settings.database_path.exists()
    assert settings.secret_file.exists()
    assert Path(settings.log_dir / "shift.log").exists()
