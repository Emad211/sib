"""Which checklist lines a handover flow asks, and how often (S01 §2, owner decisions 8, 2026-10-31).

Each flow asks only its own lists: the trolley belongs to the nursing flow, and the general space and
the doctor's room belong to reception. An item names the role responsible for it; an item that names
none takes the role of the place it sits in. A line is asked every shift, or only once a day, or only
once a week: «daily» and «weekly» are asked again only when nobody has answered it in the current period.
The Persian week starts on Saturday.
"""

from datetime import date, timedelta
from enum import StrEnum

from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import Role

SATURDAY = 5  # `date.weekday()` counts Monday as 0


class Frequency(StrEnum):
    """How often an item is asked."""

    EVERY_SHIFT = "every_shift"
    DAILY = "daily"
    WEEKLY = "weekly"


# The seeded places and who answers for them; a place the manager adds defaults to reception.
LOCATION_ROLES: dict[str, Role] = {
    "shelf_reception": Role.RECEPTION,
    "cpr_room": Role.NURSE,
    "trolley": Role.NURSE,
    "doctor_room": Role.RECEPTION,
    "public_area": Role.RECEPTION,
    "main_store": Role.STOREKEEPER,
}
FLOW_ROLES: dict[HandoverFlow, Role] = {HandoverFlow.RECEPTION: Role.RECEPTION, HandoverFlow.NURSING: Role.NURSE}
# Only these three roles can own a line of a list (S01 §2); the manager owns none. The storekeeper's lines are
# answered by the incoming person of the reception flow, since the store is handed over with the reception shift.
LIST_ROLES = (Role.RECEPTION, Role.NURSE, Role.STOREKEEPER)


def default_role(location_id: str) -> Role:
    """The role that answers for an item that names none: the role of the place it sits in."""
    return LOCATION_ROLES.get(location_id, Role.RECEPTION)


def asked_in_flow(role: Role, flow: HandoverFlow) -> bool:
    """True when `flow` is the one whose incoming person answers for `role` (ADR-035)."""
    return FLOW_ROLES[flow] is role or (flow is HandoverFlow.RECEPTION and role is Role.STOREKEEPER)


def period_start(frequency: Frequency, work_date: date) -> date | None:
    """The first day of the period `work_date` falls in; None when every shift starts a new period."""
    if frequency is Frequency.DAILY:
        return work_date
    if frequency is Frequency.WEEKLY:
        return work_date - timedelta(days=(work_date.weekday() - SATURDAY) % 7)
    return None


def is_due(frequency: Frequency, work_date: date, last_answered: date | None) -> bool:
    """True when the line is asked in the shift of `work_date`, given the last day anybody answered it."""
    start = period_start(frequency, work_date)
    return start is None or last_answered is None or last_answered < start
