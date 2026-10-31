"""Record the manager's initial handover baseline: the one count the app starts from (SHF-7.1).

There is no manager screen yet (the full UI is SHF-7.1), so this is the operator's way in. The shelves
come from a JSON file — a list of `{"item_id", "location_id", "quantity"}` — because the clinic's item
list is far too long for a command line; everything else is one explicit flag. Nothing here reads
accounting: a baseline is a physical count, and it writes one `handover.baseline_recorded` event that
seeds the cash box, sets every shelf it names and names who holds them.

Usage:
    python tools/record_handover_baseline.py --db shift.db --flow reception --custodian staff-1 \
        --manager manager-1 --opening-cash 1000000 --items baseline.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shift.adapters.db.core import Database  # noqa: E402
from shift.common.clock import SystemClock, tehran_iso  # noqa: E402
from shift.domain.handover_formulas import HandoverError  # noqa: E402
from shift.domain.handover_fsm import HandoverFlow  # noqa: E402
from shift.services.audit import AuditService  # noqa: E402
from shift.services.catalog import CatalogService  # noqa: E402
from shift.services.event_store import EventStore  # noqa: E402
from shift.services.handover import HandoverService  # noqa: E402
from shift.services.projections import ProjectionEngine  # noqa: E402


def _items(path: Path) -> list[dict[str, Any]]:
    """Read the item list file; the payload model refuses anything that is not a real opening count."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("the items file holds a JSON list of {item_id, location_id, quantity}")
    return rows


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when the baseline is recorded, 1 when the database or the request refuses it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to the shift SQLite database")
    parser.add_argument("--flow", required=True, choices=[flow.value for flow in HandoverFlow])
    parser.add_argument("--custodian", required=True, help="the person who holds the box and shelves now")
    parser.add_argument("--manager", required=True, help="the manager recording the baseline")
    parser.add_argument("--opening-cash", type=int, help="what the cash box holds; reception only")
    parser.add_argument("--items", required=True, type=Path, help="JSON list of shelf quantities")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1

    clock = SystemClock()
    database = Database(args.db)
    try:
        service = HandoverService(
            database,
            EventStore(database, clock=clock),
            projections=ProjectionEngine(database, clock=clock),
            catalog=CatalogService(database, clock=clock),
            audit=AuditService(database, clock=clock),
            clock=clock,
        )
        record = service.record_baseline(
            flow=HandoverFlow(args.flow),
            custodian_id=args.custodian,
            manager_id=args.manager,
            opening_cash=args.opening_cash,
            items=_items(args.items),
        )
    except (HandoverError, ValueError, OSError) as exc:
        print(f"baseline refused: {exc}")
        return 1
    finally:
        database.close()

    cash = "no cash box" if record.opening_cash is None else f"opening cash {record.opening_cash}"
    print(
        f"baseline recorded: flow={record.flow.value} custodian={record.custodian_id} "
        f"manager={record.manager_id} at={tehran_iso(record.recorded_at)} "
        f"shelves={record.shelves} {cash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
