"""The manager's baseline tool: one operator command, an explicit database and a JSON item list.

This is the only way to record the app's opening count until the manager screen exists (SHF-7.1), so
the test drives `main()` against a temporary database exactly as an operator would, then reads the
event log back: what it wrote, who wrote it and when.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
import record_handover_baseline
from clocks import FixedClock
from seed_reference_data import seed

from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.common.clock import TEHRAN
from shift.domain.handover_fsm import HandoverFlow
from shift.services.audit import AuditService
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.handover import HandoverService
from shift.services.projections import ProjectionEngine

SHELF = "shelf_reception"
ITEMS = [
    {"item_id": "para_500_amp", "location_id": SHELF, "quantity": 10},
    {"item_id": "diclofenac_75_amp", "location_id": SHELF, "quantity": 4},
]


@pytest.fixture()
def items_file(tmp_path: Path) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(ITEMS), encoding="utf-8")
    return path


def _args(database: Database, items_file: Path, *extra: str) -> list[str]:
    return [
        "--db",
        str(database.path),
        "--flow",
        "reception",
        "--custodian",
        "staff-1",
        "--manager",
        "manager-1",
        "--items",
        str(items_file),
        *extra,
    ]


def test_the_tool_records_a_baseline_and_names_who_and_when(
    database: Database, items_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(database)

    code = record_handover_baseline.main(_args(database, items_file, "--opening-cash", "750000"))

    printed = capsys.readouterr().out
    assert code == 0
    assert "flow=reception custodian=staff-1 manager=manager-1" in printed
    assert "at=" in printed and "+03:30" in printed, "the tool names when the baseline was recorded"
    assert "shelves=2" in printed and "opening cash 750000" in printed
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 750_000, "counted": 750_000, "variance": 0}
        ]
        assert projections_repo.stock_rows(connection) == [
            {"item_id": "diclofenac_75_amp", "location_id": SHELF, "qty": 4},
            {"item_id": "para_500_amp", "location_id": SHELF, "qty": 10},
        ]
        actor_id, payload = connection.exec_driver_sql(
            "SELECT actor_id, payload FROM events WHERE type = 'handover.baseline_recorded'"
        ).one()
    assert actor_id == "manager-1"
    assert json.loads(payload)["opening_cash"] == 750_000


def test_the_tool_refuses_a_second_baseline(
    database: Database, items_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(database)
    assert record_handover_baseline.main(_args(database, items_file, "--opening-cash", "750000")) == 0

    assert record_handover_baseline.main(_args(database, items_file, "--opening-cash", "1")) == 1

    assert "baseline refused" in capsys.readouterr().out
    with database.read() as connection:
        assert projections_repo.cash_rows(connection) == [
            {"cash_box_id": "reception", "expected": 750_000, "counted": 750_000, "variance": 0}
        ]


def test_the_tool_refuses_a_missing_database(
    tmp_path: Path, items_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nowhere.db"

    code = record_handover_baseline.main(
        [
            "--db",
            str(missing),
            "--flow",
            "reception",
            "--custodian",
            "staff-1",
            "--manager",
            "manager-1",
            "--opening-cash",
            "1",
            "--items",
            str(items_file),
        ]
    )

    assert code == 1
    assert "database not found" in capsys.readouterr().out
    assert not missing.exists()


def test_the_service_hands_the_first_flow_to_the_custodian_the_command_recorded(database: Database) -> None:
    """The command and the service agree: after it, a flow starts without anyone typing who hands over."""
    seed(database)
    clock = FixedClock(datetime(2026, 10, 28, 9, 30, tzinfo=TEHRAN))
    service = HandoverService(
        database,
        EventStore(database, clock=clock),
        projections=ProjectionEngine(database, clock=clock),
        catalog=CatalogService(database, clock=clock),
        audit=AuditService(database, clock=clock),
        clock=clock,
    )
    service.record_baseline(
        flow=HandoverFlow.RECEPTION,
        custodian_id="staff-1",
        manager_id="manager-1",
        opening_cash=0,
        items=[{"item_id": "para_500_amp", "location_id": SHELF, "quantity": 0}],
    )

    assert service.custodian(HandoverFlow.RECEPTION) == "staff-1"
