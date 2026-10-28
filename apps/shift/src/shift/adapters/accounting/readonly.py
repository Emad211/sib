"""Read-only sqlite adapter for the accounting database (R-05, S04 §1)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import URL, Connection, Engine, create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

QUERY_ONLY_PRAGMA = "PRAGMA query_only = 1"
BUSY_TIMEOUT_MS = 2000


class AccountingUnavailable(RuntimeError):
    """The accounting database cannot be read; the shift app keeps working degraded (R-06)."""


def _uri_database(path: str | Path) -> str:
    """The `file:` part of a read-only URI, percent-encoded so `#`, `%` and `?` stay in the name."""
    return "file:" + quote(Path(path).resolve().as_posix(), safe="/:")


def ro_uri(path: str | Path) -> str:
    """Read-only SQLite URI that survives spaces, `#`, `%` and `?` in the path."""
    return _uri_database(path) + "?mode=ro"


def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Second line of defence: the connection refuses writes even if the URI is edited."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(QUERY_ONLY_PRAGMA)
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


def create_readonly_engine(path: str | Path) -> Engine:
    """Build an engine whose every connection is opened with `mode=ro` and `query_only`."""
    # `URL.create` keeps the percent-encoding intact: SQLAlchemy decodes a string URL, and SQLite
    # would then read everything after the decoded `#` as a URI fragment. The dialect appends the
    # remaining query options to the filename, so `uri=true` belongs here, not in `connect_args`.
    url = URL.create("sqlite+pysqlite", database=_uri_database(path), query={"mode": "ro", "uri": "true"})
    engine = create_engine(url, poolclass=NullPool)
    event.listen(engine, "connect", _apply_pragmas)
    return engine


@dataclass(frozen=True, slots=True)
class AccountingDatabase:
    """A verified read-only handle on one accounting database file."""

    path: Path
    engine: Engine

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        """Yield a connection; every write on it raises `OperationalError`."""
        with self.engine.connect() as connection:
            yield connection

    def close(self) -> None:
        """Release the pooled handles (none are kept open between polls)."""
        self.engine.dispose()

    def __enter__(self) -> "AccountingDatabase":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


def open_accounting(path: str | Path) -> AccountingDatabase:
    """Open the accounting database read-only, raising `AccountingUnavailable` when it cannot be read."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise AccountingUnavailable(f"accounting database not found: {resolved}")
    engine = create_readonly_engine(resolved)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master").scalar_one()
    except SQLAlchemyError as exc:
        engine.dispose()
        raise AccountingUnavailable(f"accounting database is not readable: {resolved}: {exc}") from exc
    return AccountingDatabase(resolved, engine)
