"""Walk the event log and report the first broken hash link (exit 1 on drift)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shift.adapters.db.core import Database  # noqa: E402
from shift.adapters.db.events_repo import iter_events  # noqa: E402
from shift.services.event_chain import verify_chain  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Verify the chain; print the first break with its server sequence number."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to the shift SQLite database")
    parser.add_argument("--no-links", action="store_true", help="skip prev_hash links (chain disabled)")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1

    database = Database(args.db)
    try:
        with database.read() as connection:
            report = verify_chain(iter_events(connection), require_links=not args.no_links)
    finally:
        database.close()

    if report.ok:
        print(f"chain ok: {report.checked} events")
        return 0
    print(f"chain broken at seq {report.broken.seq}: {report.broken.reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
