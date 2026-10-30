"""SQL for the append-only `audit_log` table; UPDATE and DELETE are blocked by triggers."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

_COLUMNS = ("id", "at_utc", "at_tehran", "actor_id", "action", "outcome", "subject_id", "detail")
_INSERT_COLUMNS = _COLUMNS[1:]
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM audit_log"


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One stored audit record; `detail` is decoded JSON that never carries credentials."""

    id: int
    at_utc: str
    at_tehran: str
    actor_id: str
    action: str
    outcome: str
    subject_id: str | None
    detail: dict[str, Any]


def _row(row: Mapping[str, Any]) -> AuditRow:
    return AuditRow(
        id=int(row["id"]),
        at_utc=str(row["at_utc"]),
        at_tehran=str(row["at_tehran"]),
        actor_id=str(row["actor_id"]),
        action=str(row["action"]),
        outcome=str(row["outcome"]),
        subject_id=row["subject_id"],
        detail=json.loads(row["detail"]),
    )


def insert_entry(connection: Connection, values: Mapping[str, Any]) -> AuditRow:
    """Insert one audit record and return it exactly as stored."""
    placeholders = ", ".join("?" for _ in _INSERT_COLUMNS)
    result = connection.exec_driver_sql(
        f"INSERT INTO audit_log ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
        tuple(values[column] for column in _INSERT_COLUMNS),
    )
    return AuditRow(
        id=int(result.lastrowid or 0),
        at_utc=str(values["at_utc"]),
        at_tehran=str(values["at_tehran"]),
        actor_id=str(values["actor_id"]),
        action=str(values["action"]),
        outcome=str(values["outcome"]),
        subject_id=values["subject_id"],
        detail=json.loads(str(values["detail"])),
    )


def recent(connection: Connection, *, limit: int = 50, action: str | None = None) -> list[AuditRow]:
    """Newest audit records first, optionally only one action, straight from idx_audit_log_action_id."""
    if action is None:
        rows = connection.exec_driver_sql(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).mappings()
    else:
        rows = connection.exec_driver_sql(
            f"{_SELECT} WHERE action = ? ORDER BY id DESC LIMIT ?", (action, limit)
        ).mappings()
    return [_row(row) for row in rows]


def count_entries(connection: Connection, *, action: str | None = None) -> int:
    """Number of audit records, in total or for one action."""
    if action is None:
        return int(connection.exec_driver_sql("SELECT COUNT(*) FROM audit_log").scalar_one())
    return int(connection.exec_driver_sql("SELECT COUNT(*) FROM audit_log WHERE action = ?", (action,)).scalar_one())
