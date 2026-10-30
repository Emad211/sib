"""SQL for the persisted handover flows, their cutoffs and the open-invoice guard (H-03, H-11, H-12).

One row per flow of one shift. `opened_at` is the flow's own frozen cutoff: an accounting fact
stamped at or after it belongs to the shift that follows the flow's own shift (D1, D3), which is H-11
read literally. The unique `(shift_id, flow)` key allows exactly one reception flow and one nursing
flow per shift, and neither waits for the other (H-12).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import mirror_repo
from shift.common.clock import parse_iso, to_tehran, utc_iso
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering

ClockCovering = Callable[[datetime], ShiftKey]

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


def latest_flow_at_or_before(connection: Connection, instant: datetime) -> FlowRow | None:
    """The newest flow that had already started at `instant`; `opened_at` is stored as UTC ISO text."""
    row = (
        connection.exec_driver_sql(
            f"{_SELECT} WHERE opened_at <= ? ORDER BY opened_at DESC, handover_id DESC LIMIT 1",
            (utc_iso(instant),),
        )
        .mappings()
        .first()
    )
    return None if row is None else _row(row)


def shift_id_at(connection: Connection, instant: datetime, clock_covering: ClockCovering = covering) -> str:
    """D3: the shift an accounting fact stamped `instant` belongs to (H-11).

    Before any flow has started, the clinic clock decides: the fact belongs to the shift covering
    its own instant. From the newest flow's cutoff on, the fact belongs to the shift that follows
    that flow's shift — the flow was frozen against the shift it closes (D1), so this holds whether
    or not the wall clock has crossed a boundary in between. `clock_covering` is injected, so the
    rule is testable without a running clinic clock.
    """
    flow = latest_flow_at_or_before(connection, instant)
    if flow is None:
        return clock_covering(to_tehran(instant)).text
    return ShiftKey.parse(flow.shift_id).following().text


def open_invoice_count(connection: Connection) -> int:
    """How many invoices accounting still shows open; H-03 refuses a handover start while any are."""
    return int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {mirror_repo.OPEN_INVOICE}").scalar_one())
