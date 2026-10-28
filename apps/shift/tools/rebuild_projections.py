"""Verify a database's projections against a full replay; repair only with --apply."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shift.adapters.db.core import Database  # noqa: E402
from shift.services.projections import ProjectionEngine  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when projections match the log, 1 when drift is found (or on error)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to the shift SQLite database")
    parser.add_argument("--apply", action="store_true", help="replace drifted projections with the rebuilt ones")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1

    database = Database(args.db)
    try:
        report = ProjectionEngine(database).rebuild(dry_run=not args.apply)
    finally:
        database.close()

    for name in sorted(report.live_checksums):
        print(f"{name}: live={report.live_checksums[name]} rebuilt={report.rebuilt_checksums[name]}")
    if not report.drift:
        print(f"projections ok: {report.events} events")
        return 0
    print(f"drift detected: {', '.join(report.drift)}")
    if args.apply:
        print("projections repaired from the event log")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
