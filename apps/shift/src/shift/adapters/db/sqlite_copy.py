"""Copy a live WAL SQLite database into one self-contained file (ADR-008, O01 §2).

A plain file copy of a database with a write-ahead log can be corrupt, so the copy goes through
the online backup API. That copy still inherits the source's WAL header and its journal mode, so
it is checkpointed and switched to a rollback journal before it is handed back: both callers --
the periodic snapshot and the pre-migration backup -- need one file that restores on its own,
with no `-wal` beside it.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

TIMEOUT_SECONDS = 5.0


class CopyRejected(RuntimeError):
    """The database could not be copied; nothing usable is left at the destination."""


def copy_database(source: str | Path, destination: str | Path, *, timeout: float = TIMEOUT_SECONDS) -> None:
    """Write one consistent, self-contained copy of `source` to `destination`."""
    try:
        with closing(sqlite3.connect(source, timeout=timeout)) as origin:
            with closing(sqlite3.connect(destination, timeout=timeout)) as copy:
                origin.backup(copy)
                copy.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                copy.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.Error as exc:
        raise CopyRejected(f"cannot copy {Path(source).name}: {exc}") from exc
