"""Security properties of the event log: no mutation SQL, payloads stay data."""

import ast
from pathlib import Path

from event_fixtures import build_event
from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine


def test_event_repo_contains_no_update_or_delete_statements() -> None:
    source = Path(events_repo.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]

    assert not any("DELETE FROM EVENTS" in value.upper() for value in literals)
    assert not any("UPDATE EVENTS" in value.upper() for value in literals)


def test_payload_is_parameterised_data_not_sql(
    store: EventStore, projections: ProjectionEngine, database: Database
) -> None:
    malicious = "x'); DROP TABLE events; --"
    store.append(
        build_event(
            "handover.item_counted",
            payload={"item_id": malicious, "location_id": "loc-1", "counted": 3},
        )
    )

    projections.sync()

    with database.read() as connection:
        assert int(connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar_one()) == 1
        row = connection.exec_driver_sql("SELECT item_id FROM proj_stock").one()
    assert row[0] == malicious
