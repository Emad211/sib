"""Which catalog location each item step counts, next to the flows' step order (H-04, H-12).

The names are the clinic's seeded location ids (`tools/seed_reference_data.py`): the reception shelf,
the CPR room and the trolley. A step without a location counts no shelf quantity — the cash box, the
checklists, the parcels and the signatures — so its items come from somewhere else (or nowhere).
"""

from collections.abc import Mapping

from shift.domain.handover_fsm import FLOW_STEPS, HandoverFlow, HandoverStep
from shift.domain.roles import Role

STEP_LOCATIONS: Mapping[HandoverStep, str | None] = {
    HandoverStep.CASH: None,
    HandoverStep.SHELF: "shelf_reception",
    HandoverStep.CPR_ROOM: "cpr_room",
    HandoverStep.TROLLEY: "trolley",
    HandoverStep.CHECKLISTS: None,
    HandoverStep.PARCELS: None,
    HandoverStep.DUAL_SIGN: None,
}

# Every location a step counts, and the flow that counts it (derived, so the two can never drift).
FLOW_LOCATIONS: Mapping[str, HandoverFlow] = {
    location: flow
    for flow, steps in FLOW_STEPS.items()
    for location in (STEP_LOCATIONS[step] for step in steps)
    if location is not None
}


def flow_for_location(location_id: str | None) -> HandoverFlow:
    """The flow whose cutoff a stock fact at `location_id` follows; an unplaced fact follows reception (D3)."""
    return FLOW_LOCATIONS.get(str(location_id), HandoverFlow.RECEPTION)


# Every location a flow counts, in the order it walks them: derived from the same table as the flow's
# steps, so an initial count can never cover a shelf its own blind count does not ask for.
FLOW_COUNT_LOCATIONS: Mapping[HandoverFlow, tuple[str, ...]] = {
    flow: tuple(location for step in steps if (location := STEP_LOCATIONS[step]) is not None)
    for flow, steps in FLOW_STEPS.items()
}

# Owner decision 9 (ADR-022 item 5): the role that owns each flow records its initial count, on its own
# phone, and becomes that flow's custodian; the manager's tool on /manager/baseline stays the fallback.
FLOW_OWNER_ROLES: Mapping[HandoverFlow, Role] = {
    HandoverFlow.RECEPTION: Role.RECEPTION,
    HandoverFlow.NURSING: Role.NURSE,
}
