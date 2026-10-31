"""The late cash notice: a money event that arrived after its flow had already settled (H-11).

A phone works offline for a while, so a withdrawal recorded at 12:10 can reach the server at 14:00,
after the 13:00 handover settled. The cash really left the box at 12:10 and the blind count already
saw an empty box, so applying the record to the box a second time would hand the next shift a false
shortage. The flow therefore gets one internal record whose own effect cancels the late movement,
and the manager reads the late arrival off the daily card instead of a gap that never happened.

Nothing here refuses an event: keeping the record is the whole point (F5). The notice is a normal
append-only event, so a rebuild replays it exactly as the live path wrote it.
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import events_repo, handover_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, parse_iso, utc_iso
from shift.common.ids import new_ulid
from shift.domain.cash_cutoff import signed_cash
from shift.domain.event_types import EventType
from shift.domain.handover_fsm import HandoverFlow
from shift.services.event_store import EventStore, NewEvent

log = logging.getLogger(__name__)

LATE_CASH = EventType.HANDOVER_LATE_CASH_RECORDED.value


def late_cash_payload(connection: Connection, record: EventRecord) -> dict[str, Any] | None:
    """The late-cash record a settled flow deserves for one money event, or None (H-11).

    The flow a money event concerns is the first reception flow whose cutoff had not yet fallen when
    the money moved. If that flow had already settled its box before this record arrived, the count
    it agreed on already had the money physically in or out, and the box must not move again.
    """
    amount = signed_cash(record.type, record.payload, record.shift_id)
    if not amount:
        return None
    occurred = parse_iso(record.client_at)
    row = handover_repo.first_flow_at_or_after(connection, occurred, HandoverFlow.RECEPTION)
    if row is None:
        return None
    settled = events_repo.cash_settle_seq(connection, row.handover_id)
    if settled is None or settled > record.seq:
        return None
    if events_repo.count_ref(connection, record.event_id, (LATE_CASH,)):
        return None
    return {"handover_id": row.handover_id, "amount": amount, "occurred_at": utc_iso(occurred)}


def notice_late_cash(database: Database, store: EventStore, record: EventRecord, *, clock: Clock) -> EventRecord | None:
    """Write the late-cash record a money event that arrived after its flow settled deserves (H-11)."""
    with database.read() as connection:
        payload = late_cash_payload(connection, record)
    if payload is None:
        return None
    moment = clock.now_utc()
    result = store.append(
        NewEvent(
            client_event_id=new_ulid(moment),
            type=LATE_CASH,
            payload=payload,
            actor_id=record.actor_id,
            device_id=record.device_id,
            work_date=date.fromisoformat(record.work_date),
            client_at=moment,
            shift_id=record.shift_id,
            ref_event_id=record.event_id,
        )
    )
    if result.created:
        log.info("a late cash event was recorded against handover %s", payload["handover_id"])
    return result.event
