"""Who is on shift, read back out of the event log and the handover rows (ADR-021).

Nothing here stores a flag: a receptionist or nurse is on shift while they hold their flow's
custody — the manager's baseline seeds it and each close hands it to the incoming person — and a
second receptionist or the shift doctor is on shift from their own «حضور در شیفت» record until that
shift ends. Every answer is therefore rebuildable.
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Connection

from shift.adapters.db import handover_repo
from shift.common.clock import to_tehran
from shift.domain.event_types import EventType
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.on_shift import BOUND_ROLES
from shift.domain.permissions import Role, parse_role
from shift.domain.policy import ShiftKey
from shift.domain.shift_clock import covering

_PRESENCE = EventType.SHIFT_PRESENCE_DECLARED.value
# The flow whose custody makes a bound role's holder on shift. The doctor holds no box.
FLOW_FOR_ROLE: dict[Role, HandoverFlow] = {
    Role.RECEPTION: HandoverFlow.RECEPTION,
    Role.NURSE: HandoverFlow.NURSING,
}
# Only the two roles the owner gave a «حضور در شیفت» button (ADR-021).
PRESENCE_ROLES: frozenset[Role] = frozenset({Role.RECEPTION, Role.SHIFT_DOCTOR})


def _presence_holds(connection: Connection, *, staff_id: str, role: Role, instant: datetime, shift: ShiftKey) -> bool:
    """Whether the newest presence record of this person and role still covers `instant`."""
    row = (
        connection.exec_driver_sql(
            "SELECT client_at, shift_id FROM events WHERE type = ? AND actor_id = ? "
            "AND json_extract(payload, '$.role') = ? AND julianday(client_at) <= julianday(?) "
            "ORDER BY julianday(client_at) DESC, seq DESC LIMIT 1",
            (_PRESENCE, staff_id, role.value, instant.isoformat()),
        )
        .mappings()
        .first()
    )
    if row is None:
        return False
    if role is Role.SHIFT_DOCTOR:
        # A doctor's presence ends at the shift boundary: the clock's running shift must be the same.
        return str(row["shift_id"] or "") == shift.text
    # Presence never outlives the shift it was declared in (ADR-021 §2, S01 R-12).
    if str(row["shift_id"] or "") != shift.text:
        return False
    # Reception's presence ends when the next handover *closes* after it (ADR-021 §2, H-11): a flow
    # opened before the declaration but signed after it takes over from that declaration, and a close
    # that happened after `instant` says nothing about the record being judged (S01 R-15).
    closed_later = connection.exec_driver_sql(
        "SELECT 1 FROM handover WHERE flow = ? AND signed_at IS NOT NULL "
        "AND julianday(signed_at) > julianday(?) AND julianday(signed_at) <= julianday(?) LIMIT 1",
        (HandoverFlow.RECEPTION.value, str(row["client_at"]), instant.isoformat()),
    ).first()
    return closed_later is None


def on_shift_roles(
    connection: Connection, *, staff_id: str, roles: Iterable[str], instant: datetime
) -> tuple[str, ...]:
    """The bound roles this person is actually on shift for at `instant`, in a stable order."""
    held = {parse_role(role) for role in roles} & BOUND_ROLES
    if not held:
        return ()
    shift = covering(to_tehran(instant))
    working: set[Role] = set()
    for role in held:
        flow = FLOW_FOR_ROLE.get(role)
        if flow is not None:
            if handover_repo.custodian(connection, flow) == staff_id:
                working.add(role)
                continue
            if _emergency_incoming(connection, flow, staff_id):
                working.add(role)
                continue
        if role in PRESENCE_ROLES and _presence_holds(
            connection, staff_id=staff_id, role=role, instant=instant, shift=shift
        ):
            working.add(role)
    return tuple(sorted(role.value for role in working))


def _emergency_incoming(connection: Connection, flow: HandoverFlow, staff_id: str) -> bool:
    """ADR-017: on an emergency start the incoming person is on shift from then, not from the close."""
    row = connection.exec_driver_sql(
        "SELECT 1 FROM handover WHERE flow = ? AND incoming_id = ? AND signed_at IS NULL "
        "AND emergency_by IS NOT NULL LIMIT 1",
        (flow.value, staff_id),
    ).first()
    return row is not None


def custodian_at(connection: Connection, flow: HandoverFlow, instant: datetime) -> str | None:
    """Who held this flow at `instant`, read out of the handover rows (R-15, ADR-021).

    A record is judged by the moment it happened, so "who holds the box" cannot be the row that says
    who holds it now: while the newest flow of its kind was still open its outgoing person held it, and
    from the close the incoming person did. Before the clinic's first flow of that kind the manager's
    baseline names the custodian.
    """
    row = handover_repo.latest_flow_at_or_before(connection, instant, flow)
    if row is None:
        baseline = connection.exec_driver_sql(
            "SELECT json_extract(payload, '$.custodian_id') FROM events WHERE type = ? "
            "AND json_extract(payload, '$.flow') = ? AND julianday(client_at) <= julianday(?) "
            "ORDER BY julianday(client_at) DESC, seq DESC LIMIT 1",
            (EventType.HANDOVER_BASELINE_RECORDED.value, flow.value, instant.isoformat()),
        ).first()
        return None if baseline is None or baseline[0] is None else str(baseline[0])
    if row.signed_at is not None and row.signed_at <= instant:
        return row.incoming_id
    return row.outgoing_id


def on_shift_roles_at(
    connection: Connection, *, staff_id: str, roles: Iterable[str], instant: datetime
) -> tuple[str, ...]:
    """The bound roles this person was on shift for at `instant`, whichever shift was running then.

    R-15's half of ADR-021: a phone that was offline all evening replays a record made while its owner
    still held the box, so the answer must come from that moment's custody and presence, not today's.
    """
    held = {parse_role(role) for role in roles} & BOUND_ROLES
    if not held:
        return ()
    shift = covering(to_tehran(instant))
    working: set[Role] = set()
    for role in held:
        flow = FLOW_FOR_ROLE.get(role)
        if flow is not None and custodian_at(connection, flow, instant) == staff_id:
            working.add(role)
            continue
        if role in PRESENCE_ROLES and _presence_holds(
            connection, staff_id=staff_id, role=role, instant=instant, shift=shift
        ):
            working.add(role)
    return tuple(sorted(role.value for role in working))
