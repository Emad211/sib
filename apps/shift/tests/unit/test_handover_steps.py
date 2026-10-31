"""Every item step counts one catalog location, and every location follows its own flow (H-04, H-12)."""

from shift.domain.handover_fsm import FLOW_STEPS, HandoverFlow, HandoverStep
from shift.domain.handover_steps import FLOW_LOCATIONS, STEP_LOCATIONS, flow_for_location


def test_the_item_steps_count_the_clinics_seeded_locations() -> None:
    counted = {step: location for step, location in STEP_LOCATIONS.items() if location is not None}

    assert counted == {
        HandoverStep.SHELF: "shelf_reception",
        HandoverStep.CPR_ROOM: "cpr_room",
        HandoverStep.TROLLEY: "trolley",
    }


def test_every_flow_owns_the_locations_its_own_steps_count() -> None:
    locations = {
        STEP_LOCATIONS[step] for flow in FLOW_STEPS for step in FLOW_STEPS[flow] if STEP_LOCATIONS[step] is not None
    }

    assert set(FLOW_LOCATIONS) == locations
    assert dict(FLOW_LOCATIONS) == {
        "shelf_reception": HandoverFlow.RECEPTION,
        "cpr_room": HandoverFlow.NURSING,
        "trolley": HandoverFlow.NURSING,
    }


def test_a_fact_no_step_places_follows_the_reception_flow() -> None:
    """D3: an unattributed fact takes the reception cutoff, never a guess."""
    assert flow_for_location("main_store") is HandoverFlow.RECEPTION
    assert flow_for_location(None) is HandoverFlow.RECEPTION
