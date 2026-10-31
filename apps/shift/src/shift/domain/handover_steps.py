"""Which catalog location each item step counts, next to the flows' step order (H-04, H-12).

The names are the clinic's seeded location ids (`tools/seed_reference_data.py`): the reception shelf,
the CPR room and the trolley. A step without a location counts no shelf quantity — the cash box, the
checklists, the parcels and the signatures — so its items come from somewhere else (or nowhere).
"""

from collections.abc import Mapping

from shift.domain.handover_fsm import HandoverStep

STEP_LOCATIONS: Mapping[HandoverStep, str | None] = {
    HandoverStep.CASH: None,
    HandoverStep.SHELF: "shelf_reception",
    HandoverStep.CPR_ROOM: "cpr_room",
    HandoverStep.TROLLEY: "trolley",
    HandoverStep.CHECKLISTS: None,
    HandoverStep.PARCELS: None,
    HandoverStep.DUAL_SIGN: None,
}
