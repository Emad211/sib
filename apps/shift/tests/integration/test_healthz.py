"""Contract of the liveness endpoint used by the deployment wrapper (step 1)."""

from fastapi.testclient import TestClient

from shift.__version__ import __version__
from shift.app import create_app


def test_healthz_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
