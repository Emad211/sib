"""Take a verified snapshot of the live shift database now (gap S6, O01)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shift.services.backup import BackupRejected, BackupService  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when every asked-for location holds an intact snapshot, 1 when one is rejected."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to the live shift SQLite database")
    parser.add_argument("--to", required=True, type=Path, help="primary backup directory")
    parser.add_argument("--mirror", type=Path, default=None, help="second location: another disk or a network share")
    parser.add_argument("--keep", type=int, default=7, help="how many snapshots to keep per location")
    args = parser.parse_args(argv)

    service = BackupService(args.db, backup_dir=args.to, keep_snapshots=args.keep, mirror_dir=args.mirror)
    try:
        report = service.create()
    except BackupRejected as exc:
        print(f"backup rejected: {exc}")
        return 1

    snapshot = report.snapshot
    print(
        f"snapshot {snapshot.path}: {snapshot.size_bytes} bytes, "
        f"schema v{snapshot.schema_version}, {snapshot.events} events"
    )
    for path in report.pruned:
        print(f"pruned {path}")
    if report.mirror_error is not None:
        print(f"mirror unavailable: {report.mirror_error}")
        return 1
    if report.mirror is not None:
        print(f"mirror {report.mirror}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
