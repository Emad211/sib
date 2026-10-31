"""SQL for the persisted handover flows, their cutoffs and the open-invoice guard (H-03, H-11, H-12).

One row per flow of one shift. `opened_at` is the flow's own frozen cutoff: an accounting fact
stamped at or after it belongs to the shift that follows the flow's own shift (D1, D3), which is H-11
read literally. The unique `(shift_id, flow)` key allows exactly one reception flow and one nursing
flow per shift, and neither waits for the other (H-12).

`handover_custody` holds who holds each flow's cash box and shelves now: the manager's baseline sets it
and a flow's close hands it to the incoming person, which is what lets a flow derive its outgoing
person without staff typing one (H-10, SHF-7.1).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import mirror_repo
from shift.common.clock import parse_iso, to_tehran, utc_iso
from shift.domain.events import EventType
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering

ClockCovering = Callable[[datetime], ShiftKey]

_RECEIPT_EVENT = EventType.ACC_CASH_RECEIPT.value
_BASELINE_EVENT = EventType.HANDOVER_BASELINE_RECORDED.value

_COLUMNS = (
    "handover_id",
    "shift_id",
    "flow",
    "opened_at",
    "work_date",
    "outgoing_id",
    "incoming_id",
    "signed_at",
    "early_left",
    "emergency_by",
    "created_at",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM handover"


@dataclass(frozen=True, slots=True)
class FlowRow:
    """One stored flow; times are aware UTC instants and `flow` is the clinic's flow name."""

    handover_id: str
    shift_id: str
    flow: HandoverFlow
    opened_at: datetime
    work_date: date
    outgoing_id: str
    incoming_id: str
    signed_at: datetime | None
    early_left: bool
    emergency_by: str | None
    created_at: datetime


def _row(row: Mapping[str, Any]) -> FlowRow:
    signed_at = row["signed_at"]
    return FlowRow(
        handover_id=str(row["handover_id"]),
        shift_id=str(row["shift_id"]),
        flow=HandoverFlow(row["flow"]),
        opened_at=parse_iso(str(row["opened_at"])),
        work_date=date.fromisoformat(str(row["work_date"])),
        outgoing_id=str(row["outgoing_id"]),
        incoming_id=str(row["incoming_id"]),
        signed_at=None if signed_at is None else parse_iso(str(signed_at)),
        early_left=bool(row["early_left"]),
        emergency_by=row["emergency_by"],
        created_at=parse_iso(str(row["created_at"])),
    )


def insert(
    connection: Connection,
    *,
    handover_id: str,
    shift_id: str,
    flow: HandoverFlow,
    opened_at: datetime,
    work_date: date,
    outgoing_id: str,
    incoming_id: str,
    created_at: datetime,
) -> None:
    """Record one flow; `(shift_id, flow)` is unique, so a shift never opens two of the same kind."""
    connection.exec_driver_sql(
        "INSERT INTO handover (handover_id, shift_id, flow, opened_at, work_date, outgoing_id, incoming_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            handover_id,
            shift_id,
            flow.value,
            utc_iso(opened_at),
            work_date.isoformat(),
            outgoing_id,
            incoming_id,
            utc_iso(created_at),
        ),
    )


def get(connection: Connection, handover_id: str) -> FlowRow | None:
    """Read one flow by its id."""
    row = connection.exec_driver_sql(f"{_SELECT} WHERE handover_id = ?", (handover_id,)).mappings().first()
    return None if row is None else _row(row)


def find_shift_flow(connection: Connection, shift_id: str, flow: HandoverFlow) -> FlowRow | None:
    """The flow of one kind that already belongs to `shift_id`, signed or not (H-12)."""
    row = (
        connection.exec_driver_sql(f"{_SELECT} WHERE shift_id = ? AND flow = ?", (shift_id, flow.value))
        .mappings()
        .first()
    )
    return None if row is None else _row(row)


def mark_signed(connection: Connection, handover_id: str, signed_at: datetime) -> None:
    """Stamp the moment the flow closed; the cutoff stays `opened_at` and never moves (H-11)."""
    connection.exec_driver_sql(
        "UPDATE handover SET signed_at = ? WHERE handover_id = ?", (utc_iso(signed_at), handover_id)
    )


def mark_early_left(connection: Connection, handover_id: str) -> None:
    """Record that the outgoing person left before handing over (H-08); the flow stays open."""
    connection.exec_driver_sql("UPDATE handover SET early_left = 1 WHERE handover_id = ?", (handover_id,))


def mark_emergency(connection: Connection, handover_id: str, manager_id: str) -> None:
    """Record the manager who confirmed the emergency start by phone (H-08)."""
    connection.exec_driver_sql("UPDATE handover SET emergency_by = ? WHERE handover_id = ?", (manager_id, handover_id))


def open_flows(connection: Connection) -> list[FlowRow]:
    """Every flow still waiting for its last signature, oldest first (H-12)."""
    rows = connection.exec_driver_sql(f"{_SELECT} WHERE signed_at IS NULL ORDER BY opened_at").mappings()
    return [_row(row) for row in rows]


def latest_flow_at_or_before(
    connection: Connection, instant: datetime, flow: HandoverFlow | None = None
) -> FlowRow | None:
    """The newest flow of one kind that had already started at `instant`, or of any kind for None.

    `opened_at` is stored as UTC ISO text, so text order is chronological order.
    """
    if flow is None:
        sql = f"{_SELECT} WHERE opened_at <= ? ORDER BY opened_at DESC, handover_id DESC LIMIT 1"
        parameters: tuple[object, ...] = (utc_iso(instant),)
    else:
        sql = f"{_SELECT} WHERE opened_at <= ? AND flow = ? ORDER BY opened_at DESC, handover_id DESC LIMIT 1"
        parameters = (utc_iso(instant), flow.value)
    row = connection.exec_driver_sql(sql, parameters).mappings().first()
    return None if row is None else _row(row)


def newest_flow(connection: Connection, flow: HandoverFlow) -> FlowRow | None:
    """The newest flow of one kind, signed or not: its incoming person holds the flow now (SHF-7.1)."""
    row = (
        connection.exec_driver_sql(
            f"{_SELECT} WHERE flow = ? ORDER BY opened_at DESC, handover_id DESC LIMIT 1", (flow.value,)
        )
        .mappings()
        .first()
    )
    return None if row is None else _row(row)


def custodian(connection: Connection, flow: HandoverFlow) -> str | None:
    """Who holds this flow's cash box and shelves now, or None before the manager's baseline."""
    row = connection.exec_driver_sql("SELECT custodian_id FROM handover_custody WHERE flow = ?", (flow.value,)).first()
    return None if row is None else str(row[0])


def set_custodian(
    connection: Connection, *, flow: HandoverFlow, custodian_id: str, updated_at: datetime, updated_by: str
) -> None:
    """Record who holds the flow now: the baseline's custodian, then each close's incoming person."""
    connection.exec_driver_sql(
        "INSERT INTO handover_custody (flow, custodian_id, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(flow) DO UPDATE SET custodian_id = excluded.custodian_id, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (flow.value, custodian_id, utc_iso(updated_at), updated_by),
    )


def has_baseline(connection: Connection, flow: HandoverFlow) -> bool:
    """Whether the manager already recorded this flow's opening count; a second one is refused."""
    row = connection.exec_driver_sql(
        "SELECT 1 FROM events WHERE type = ? AND json_extract(payload, '$.flow') = ? LIMIT 1",
        (_BASELINE_EVENT, flow.value),
    ).first()
    return row is not None


def shift_id_at(
    connection: Connection,
    instant: datetime,
    flow: HandoverFlow,
    clock_covering: ClockCovering = covering,
) -> str:
    """D3: the shift an accounting fact stamped `instant` belongs to, decided by its own flow (H-11).

    Each flow kind freezes its own cutoff, so a cash fact looks only at the reception flows and a
    CPR or trolley fact only at the nursing ones: the newest flow of that kind started at or before
    the fact hands it to the shift following that flow's shift. Before any flow of that kind exists
    the clinic clock decides, with the covering clock injected so the rule is testable.
    """
    row = latest_flow_at_or_before(connection, instant, flow)
    if row is None:
        return clock_covering(to_tehran(instant)).text
    return ShiftKey.parse(row.shift_id).following().text


def open_invoice_count(connection: Connection) -> int:
    """How many invoices accounting still shows open; H-03 refuses a handover start while any are."""
    return int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {mirror_repo.OPEN_INVOICE}").scalar_one())


def cash_receipt_total(connection: Connection, *, after: datetime, until: datetime) -> int:
    """The cash receipts accounting stamped inside one flow's window, summed (H-11).

    A receipt stamped after the flow's cutoff is the next shift's money, so one stamped before the
    flow closed cannot happen in the owner's sequence: the incoming person takes nothing until the
    handover ends. The service reports the total to the manager as a handover exception, which is
    the only reason this query exists.

    The stamps are compared through `julianday`, because each event stores `client_at` in the
    writer's own form — the bridge copies accounting's Tehran local text, a handset keeps its clock —
    so plain text order would not be chronological.
    """
    total = connection.exec_driver_sql(
        "SELECT COALESCE(SUM(json_extract(payload, '$.amount')), 0) FROM events "
        "WHERE type = ? AND julianday(client_at) > julianday(?) AND julianday(client_at) <= julianday(?)",
        (_RECEIPT_EVENT, utc_iso(after), utc_iso(until)),
    ).scalar_one()
    return int(total)
