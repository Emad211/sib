"""The first manager is made with a prompted PIN, and that PIN opens a session in the real app."""

import getpass

import pytest
from auth_fixtures import create_device, login
from create_staff import main
from fastapi.testclient import TestClient

from shift.adapters.db.core import Database
from shift.app import create_app
from shift.config.settings import Settings

GOOD_PIN = "836241"


def _answer(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    replies = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(replies))


@pytest.fixture
def installed(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """An empty install: migrated database, no staff, the service's own secret file location."""
    database = Database(settings.database_path)
    database.migrate()
    database.close()
    monkeypatch.setenv("SHIFT_DB_PATH", str(settings.database_path))
    monkeypatch.setenv("SHIFT_SECRET_FILE", str(settings.secret_file))
    return settings


def test_the_prompted_pin_opens_a_session_and_is_never_printed(
    installed: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answer(monkeypatch, GOOD_PIN, GOOD_PIN)

    code = main(["--name", "مدیر درمانگاه", "--mobile", "۰۹۱۲۰۰۰۰۰۰۱", "--role", "manager", "--staff-id", "boss"])

    output = capsys.readouterr()
    assert code == 0
    assert GOOD_PIN not in output.out + output.err
    with TestClient(create_app(installed)) as client:
        create_device(client.app.state.database, device_id="device-boss", staff_id="boss")
        response = login(client, mobile="09120000001", device_id="device-boss", pin=GOOD_PIN)
        assert response.status_code == 200
        assert "manager" in response.json()["roles"]


@pytest.mark.parametrize("answers", [("123456",), ("111111",), (GOOD_PIN, "836242")])
def test_a_guessable_or_mistyped_pin_creates_nobody(
    installed: Settings, monkeypatch: pytest.MonkeyPatch, answers: tuple[str, ...]
) -> None:
    _answer(monkeypatch, *answers)

    assert main(["--name", "x", "--mobile", "09120000002", "--role", "nurse"]) == 1

    database = Database(installed.database_path)
    with database.read() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM staff").scalar() == 0
    database.close()


def test_an_unknown_role_or_a_second_use_of_the_mobile_is_refused(
    installed: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["--name", "x", "--mobile", "09120000003", "--role", "admin"]) == 1
    _answer(monkeypatch, GOOD_PIN, GOOD_PIN, GOOD_PIN, GOOD_PIN)
    assert main(["--name", "x", "--mobile", "09120000003", "--role", "nurse"]) == 0
    assert main(["--name", "y", "--mobile", "09120000003", "--role", "nurse"]) == 1
