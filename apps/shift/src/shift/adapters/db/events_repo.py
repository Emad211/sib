"""SQL access for the append-only event log; no UPDATE or DELETE exists here."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_COLUMNS = (
    "seq",
    "event_id",
    "type",
    "schema_v",
    "payload",
    "actor_id",
    "device_id",
    "shift_id",
    "handover_id",
    "work_date",
    "client_at",
    "server_at",
    "at_tehran",
    "ref_event_id",
    "idempotency_key",
    "prev_hash",
    "hash",
)
_INSERT_COLUMNS = _COLUMNS[1:]
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM events"


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One stored event; payload is decoded JSON and every time is ISO text.

    `handover_id` names the flow a handover event belongs to (H-12). It is metadata: the hash chain
    covers `HASH_FIELDS` only, so adding it left every stored hash exactly as it was.
    """

    seq: int
    event_id: str
    type: str
    schema_v: int
    payload: dict[str, Any]
    actor_id: str
    device_id: str
    shift_id: str | None
    handover_id: str | None
    work_date: str
    client_at: str
    server_at: str
    at_tehran: str
    ref_event_id: str | None
    idempotency_key: str
    prev_hash: str
    hash: str


def _record(row: Mapping[str, Any]) -> EventRecord:
    return EventRecord(
        seq=int(row["seq"]),
        event_id=str(row["event_id"]),
        type=str(row["type"]),
        schema_v=int(row["schema_v"]),
        payload=json.loads(row["payload"]),
        actor_id=str(row["actor_id"]),
        device_id=str(row["device_id"]),
        shift_id=row["shift_id"],
        handover_id=row["handover_id"],
        work_date=str(row["work_date"]),
        client_at=str(row["client_at"]),
        server_at=str(row["server_at"]),
        at_tehran=str(row["at_tehran"]),
        ref_event_id=row["ref_event_id"],
        idempotency_key=str(row["idempotency_key"]),
        prev_hash=str(row["prev_hash"]),
        hash=str(row["hash"]),
    )


def insert_event(connection: Connection, values: Mapping[str, Any]) -> int:
    """Insert one event and return its server sequence number."""
    placeholders = ", ".join("?" for _ in _INSERT_COLUMNS)
    result = connection.exec_driver_sql(
        f"INSERT INTO events ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
        tuple(values[column] for column in _INSERT_COLUMNS),
    )
    return int(result.lastrowid or 0)


def find_by_idempotency_key(connection: Connection, key: str) -> EventRecord | None:
    """Find the event already stored for a retried outbox item."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE idempotency_key = ? LIMIT 1", (key,)).mappings().first()
    return _record(row) if row is not None else None


def find_by_event_id(connection: Connection, event_id: str) -> EventRecord | None:
    """Find an event by its client-minted ULID."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE event_id = ? LIMIT 1", (event_id,)).mappings().first()
    return _record(row) if row is not None else None


def last_event(connection: Connection) -> EventRecord | None:
    """Return the highest-sequence event, the current hash-chain head."""
    row = connection.exec_driver_sql(f"{_SELECT} ORDER BY seq DESC LIMIT 1").mappings().first()
    return _record(row) if row is not None else None


def iter_events(connection: Connection, after_seq: int = 0) -> Iterator[EventRecord]:
    """Yield events in server order after `after_seq` (the only valid ordering)."""
    result = connection.exec_driver_sql(f"{_SELECT} WHERE seq > ? ORDER BY seq", (after_seq,))
    for row in result.mappings():
        yield _record(row)


def iter_handover_events(connection: Connection, handover_id: str) -> Iterator[EventRecord]:
    """Yield one flow's events in server order; a handover is rebuilt from these, never from memory."""
    result = connection.exec_driver_sql(f"{_SELECT} WHERE handover_id = ? ORDER BY seq", (handover_id,))
    for row in result.mappings():
        yield _record(row)


def iter_between(connection: Connection, *, after: str, until: str) -> Iterator[EventRecord]:
    """Yield the events whose server stamp falls in the half-open range `[after, until)`, in order.

    The manager's daily card reports one covered day of the clinic's work, and `server_at` is the
    instant the server recorded the event (UTC ISO text, so text order is chronological). Both bounds
    are ISO text on purpose: the caller formats them once through `common.clock.utc_iso`, exactly as
    they are stored, and the range reads the way it is written.
    """
    result = connection.exec_driver_sql(
        f"{_SELECT} WHERE server_at >= ? AND server_at < ? ORDER BY seq", (after, until)
    )
    for row in result.mappings():
        yield _record(row)


def count_events(connection: Connection) -> int:
    """Number of stored events; used by tests and health diagnostics."""
    return int(connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar_one())


def iter_shift(connection: Connection, shift_id: str) -> Iterator[EventRecord]:
    """Yield every event stamped with one shift, in server order."""
    result = connection.exec_driver_sql(f"{_SELECT} WHERE shift_id = ? ORDER BY seq", (shift_id,))
    for row in result.mappings():
        yield _record(row)


def latest_ref(connection: Connection, ref_event_id: str, types: tuple[str, ...]) -> EventRecord | None:
    """The newest event of one of `types` that references `ref_event_id` (a decision or assignment)."""
    if not types:
        return None
    placeholders = ", ".join("?" for _ in types)
    row = (
        connection.exec_driver_sql(
            f"{_SELECT} WHERE ref_event_id = ? AND type IN ({placeholders}) ORDER BY seq DESC LIMIT 1",
            (ref_event_id, *types),
        )
        .mappings()
        .first()
    )
    return _record(row) if row is not None else None


def count_ref(connection: Connection, ref_event_id: str, types: tuple[str, ...]) -> int:
    """How many events of `types` already reference `ref_event_id` (used for one-delivery rules)."""
    if not types:
        return 0
    placeholders = ", ".join("?" for _ in types)
    return int(
        connection.exec_driver_sql(
            f"SELECT COUNT(*) FROM events WHERE ref_event_id = ? AND type IN ({placeholders})",
            (ref_event_id, *types),
        ).scalar_one()
    )
