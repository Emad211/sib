"""Audit trail: durable rows in `audit_log` plus the dedicated audit sink (S02 §3.2)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from shift.adapters.db import audit_repo
from shift.adapters.db.audit_repo import AuditRow
from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, tehran_iso, utc_iso
from shift.common.hashing import canonical_json
from shift.config.logging import get_audit_logger

SECRET_FIELDS = frozenset({"authorization", "password", "pin", "pubkey", "secret", "token"})
_LINE = "%s actor=%s subject=%s outcome=%s detail=%s"


class AuditOutcome(StrEnum):
    """How an audited attempt ended; mirrors the `audit_log.outcome` check constraint."""

    OK = "ok"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """What to record: who acted, on what, how it ended, and non-secret detail for the file."""

    actor_id: str
    action: str
    outcome: AuditOutcome = AuditOutcome.OK
    subject_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


class AuditError(ValueError):
    """The entry must not be written: it is malformed or carries a credential."""


class AuditService:
    """Writes every audited action to `audit_log` and to the audit sink, never the public log."""

    def __init__(self, database: Database, *, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or SystemClock()
        self._sink = get_audit_logger()

    def record(self, entry: AuditEntry) -> AuditRow:
        """Persist one audit record, then echo it to the audit sink; a failure reaches the caller."""
        self._validate(entry)
        moment = self._clock.now_utc()
        detail = canonical_json(dict(entry.detail))
        values: dict[str, Any] = {
            "at_utc": utc_iso(moment),
            "at_tehran": tehran_iso(moment),
            "actor_id": entry.actor_id,
            "action": entry.action,
            "outcome": entry.outcome.value,
            "subject_id": entry.subject_id,
            "detail": detail,
        }
        with self._database.write() as connection:
            row = audit_repo.insert_entry(connection, values)
        self._sink.info(_LINE, entry.action, entry.actor_id, entry.subject_id, entry.outcome.value, detail)
        return row

    def history(self, *, limit: int = 50, action: str | None = None) -> list[AuditRow]:
        """Read the newest audit records back for the admin screen and the after-incident review."""
        with self._database.read() as connection:
            return audit_repo.recent(connection, limit=limit, action=action)

    @staticmethod
    def _validate(entry: AuditEntry) -> None:
        if not entry.actor_id.strip():
            raise AuditError("actor_id is required")
        if not entry.action.strip():
            raise AuditError("action is required")
        secrets = SECRET_FIELDS.intersection(entry.detail)
        if secrets:
            raise AuditError(f"audit detail must not carry credentials: {sorted(secrets)}")
