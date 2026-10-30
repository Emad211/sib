"""Prove a backup can be restored and replayed before it replaces a live database (O01 §4)."""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from shift.adapters.accounting.readonly import create_readonly_engine  # noqa: E402
from shift.adapters.db import migrations  # noqa: E402
from shift.adapters.db.events_repo import iter_events  # noqa: E402
from shift.services.backup import BackupRejected, Snapshot, inspect_snapshot  # noqa: E402
from shift.services.event_chain import ChainReport, verify_chain  # noqa: E402


@dataclass(frozen=True, slots=True)
class RestoreCheck:
    """A backup is restorable when the file is intact, the schema is not newer and the chain holds."""

    snapshot: Snapshot | None
    problem: str | None = None
    chain: ChainReport | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None and self.chain is not None and self.chain.ok


def check_backup(path: str | Path, *, require_links: bool = True) -> RestoreCheck:
    """Validate one backup file without writing to it: the connection is `mode=ro`."""
    try:
        snapshot = inspect_snapshot(path)
    except BackupRejected as exc:
        return RestoreCheck(None, problem=str(exc))
    if snapshot.schema_version > migrations.LATEST:
        return RestoreCheck(
            snapshot,
            problem=f"schema v{snapshot.schema_version} is newer than this code v{migrations.LATEST}",
        )

    # The same read-only engine the accounting bridge uses: one place builds a `mode=ro` URI,
    # so a snapshot with a space or a `#` in its name is opened as a file, not as a fragment.
    engine = create_readonly_engine(snapshot.path)
    try:
        with engine.connect() as connection:
            chain = verify_chain(iter_events(connection), require_links=require_links)
    except SQLAlchemyError as exc:
        return RestoreCheck(snapshot, problem=f"cannot read the event log: {exc}")
    finally:
        engine.dispose()
    return RestoreCheck(snapshot, chain=chain)


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when the backup is restorable, 1 with the first problem found otherwise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, type=Path, help="snapshot file to validate")
    parser.add_argument("--no-links", action="store_true", help="skip prev_hash links (hash chain disabled)")
    args = parser.parse_args(argv)

    result = check_backup(args.backup, require_links=not args.no_links)
    snapshot, chain = result.snapshot, result.chain
    if result.problem is not None or snapshot is None or chain is None:
        print(f"not restorable: {result.problem or 'the snapshot could not be read'}")
        return 1
    print(f"snapshot {snapshot.path.name}: {snapshot.events} events, schema v{snapshot.schema_version}")
    if chain.broken is not None:
        print(f"chain broken at seq {chain.broken.seq}: {chain.broken.reason}")
        return 1
    print(f"restorable: integrity_check ok, chain ok, {chain.checked} events linked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
