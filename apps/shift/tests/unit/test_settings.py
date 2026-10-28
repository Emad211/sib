"""Settings come from the environment and the secret is created exactly once."""

from pathlib import Path

import pytest

from shift.config.settings import APP_ROOT, DEFAULT_PORT, Settings


def test_defaults_are_relative_to_the_app_checkout() -> None:
    settings = Settings.from_env({})

    assert settings.database_path == APP_ROOT / "shift.db"
    assert settings.port == DEFAULT_PORT
    assert settings.host == "127.0.0.1"
    assert settings.hash_chain is True
    assert settings.environment == "development"


def test_environment_overrides(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "SHIFT_HOME": str(tmp_path),
            "SHIFT_DB_PATH": str(tmp_path / "db" / "shift.db"),
            "SHIFT_LOG_DIR": str(tmp_path / "log"),
            "SHIFT_AUDIT_LOG": str(tmp_path / "log" / "audit.log"),
            "SHIFT_SECRET_FILE": str(tmp_path / "secret.txt"),
            "SHIFT_HOST": "0.0.0.0",
            "SHIFT_PORT": "9443",
            "SHIFT_HASH_CHAIN": "off",
            "SHIFT_ENV": "production",
        }
    )

    assert settings.database_path == (tmp_path / "db" / "shift.db")
    assert settings.log_dir == tmp_path / "log"
    assert settings.audit_log_path == tmp_path / "log" / "audit.log"
    assert settings.secret_file == tmp_path / "secret.txt"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9443
    assert settings.hash_chain is False
    assert settings.environment == "production"


@pytest.mark.parametrize(
    ("key", "value"), [("SHIFT_PORT", "70000"), ("SHIFT_PORT", "x"), ("SHIFT_HASH_CHAIN", "maybe")]
)
def test_invalid_values_are_rejected(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings.from_env({key: value})


def test_secret_is_created_once_and_reused(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "shift.db",
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        secret_file=tmp_path / "secret.txt",
    )

    first = settings.read_or_create_secret()
    second = settings.read_or_create_secret()

    assert first == second
    assert settings.secret_file.read_text(encoding="ascii").strip() == first
    assert len(first) >= 32


def test_empty_secret_file_is_an_error(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("", encoding="ascii")
    settings = Settings(
        database_path=tmp_path / "shift.db",
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        secret_file=secret,
    )

    with pytest.raises(ValueError, match="empty"):
        settings.read_or_create_secret()
