"""How far each accounting view has been read; written in the same transaction as its rows."""

from dataclasses import dataclass

from sqlalchemy import Connection

# The seed watermark: an empty timestamp sorts before every accounting timestamp text.
EMPTY_WATERMARK_AT = ""
EMPTY_WATERMARK_ID = 0


@dataclass(frozen=True, slots=True)
class Watermark:
    """The last row already ingested from one view: `(last_key_at, last_key_id)`."""

    view_name: str
    last_key_at: str
    last_key_id: int
    ok: bool
    error: str | None


def get(connection: Connection, view_name: str) -> Watermark:
    """Read one view's watermark, returning the seed when the view was never polled."""
    row = (
        connection.exec_driver_sql(
            "SELECT view_name, last_key_at, last_key_id, ok, error FROM acc_watermark WHERE view_name = ?",
            (view_name,),
        )
        .mappings()
        .first()
    )
    if row is None:
        return Watermark(view_name, EMPTY_WATERMARK_AT, EMPTY_WATERMARK_ID, ok=True, error=None)
    return Watermark(
        view_name=str(row["view_name"]),
        last_key_at=str(row["last_key_at"]),
        last_key_id=int(row["last_key_id"]),
        ok=bool(row["ok"]),
        error=row["error"],
    )


def save(connection: Connection, view_name: str, last_key_at: str, last_key_id: int, updated_at: str) -> None:
    """Record a successful page: clears any earlier error for that view."""
    connection.exec_driver_sql(
        "INSERT INTO acc_watermark (view_name, last_key_at, last_key_id, updated_at, ok, error) "
        "VALUES (?, ?, ?, ?, 1, NULL) "
        "ON CONFLICT(view_name) DO UPDATE SET last_key_at = excluded.last_key_at, "
        "last_key_id = excluded.last_key_id, updated_at = excluded.updated_at, ok = 1, error = NULL",
        (view_name, last_key_at, last_key_id, updated_at),
    )


def record_failure(connection: Connection, view_name: str, error: str, updated_at: str) -> None:
    """Mark a view unreadable without moving its watermark; the next poll resumes where it stopped."""
    connection.exec_driver_sql(
        "INSERT INTO acc_watermark (view_name, last_key_at, last_key_id, updated_at, ok, error) "
        "VALUES (?, ?, ?, ?, 0, ?) "
        "ON CONFLICT(view_name) DO UPDATE SET updated_at = excluded.updated_at, ok = 0, error = excluded.error",
        (view_name, EMPTY_WATERMARK_AT, EMPTY_WATERMARK_ID, updated_at, error),
    )
