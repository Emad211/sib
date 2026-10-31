"""Create one staff member with a PIN you type at a prompt: the way the first manager is made.

    python tools/create_staff.py --name "<full name>" --mobile 09xxxxxxxxx --role manager

The PIN is asked for twice with the keystrokes hidden, checked against the PIN policy (six digits,
nothing guessable) and stored only as an argon2id digest hashed with this install's secret file. It is
never taken from the command line, never printed and never logged. Run it with the same `SHIFT_*`
variables as the service, so the digest and the database are the ones the service reads.

After the first manager exists, the manager adds everybody else from the device screen; this tool is
for the first account and for recovery when no manager can sign in.
"""

import argparse
import getpass
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.exc import IntegrityError  # noqa: E402

from shift.adapters.db import staff_repo  # noqa: E402
from shift.adapters.db.core import Database  # noqa: E402
from shift.common.clock import SystemClock, utc_iso  # noqa: E402
from shift.common.ids import new_ulid  # noqa: E402
from shift.config.settings import Settings  # noqa: E402
from shift.domain.pin_policy import PinPolicyError, validate_new_pin  # noqa: E402
from shift.domain.roles import Role, UnknownRole, parse_role  # noqa: E402
from shift.services.auth import InvalidCredentials, hash_pin, normalise_mobile  # noqa: E402


def _ask_pin() -> str | None:
    """Prompt twice; return the validated PIN, or None after saying why it was refused."""
    first = getpass.getpass("PIN (6 digits, hidden): ")
    try:
        pin = validate_new_pin(first)
    except PinPolicyError as error:
        print(f"PIN refused ({error.problem.value}): choose six digits that are not guessable")
        return None
    if getpass.getpass("PIN again: ") != first:
        print("the two PINs differ; nothing was created")
        return None
    return pin


def main(argv: list[str] | None = None) -> int:
    """Create the staff row and its roles; exit 1 on a refusal, 0 when the person can sign in."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="shift database (default: SHIFT_DB_PATH or ./shift.db)")
    parser.add_argument("--secret-file", type=Path, help="session secret (default: SHIFT_SECRET_FILE)")
    parser.add_argument("--name", required=True, help="full name as the team knows the person")
    parser.add_argument("--mobile", required=True, help="09xxxxxxxxx; this is the login name")
    parser.add_argument("--role", action="append", required=True, help="repeat for several: " + ", ".join(Role))
    parser.add_argument("--staff-id", help="stable id (letters, digits, _ and -); made up when omitted")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    db_path = args.db or settings.database_path
    secret_file = args.secret_file or settings.secret_file
    if not db_path.exists():
        print(f"database not found: {db_path}; run tools/seed_reference_data.py --db <path> first")
        return 1
    try:
        roles = [parse_role(value) for value in args.role]
        mobile = normalise_mobile(args.mobile)
    except (UnknownRole, InvalidCredentials) as error:
        print(f"refused: {error}")
        return 1
    name = args.name.strip()
    staff_id = args.staff_id or f"staff-{new_ulid().lower()[-10:]}"
    if not name:
        print("refused: a name is required")
        return 1

    pin = _ask_pin()
    if pin is None:
        return 1
    pepper = replace(settings, secret_file=secret_file).read_or_create_secret()
    digest = hash_pin(pin, pepper=pepper)
    stamp = utc_iso(SystemClock().now_utc())

    database = Database(db_path)
    try:
        database.migrate()
        with database.write() as connection:
            staff_repo.insert(
                connection, staff_id=staff_id, full_name=name, mobile=mobile, pin_hash=digest, created_at=stamp
            )
            for role in dict.fromkeys(roles):
                staff_repo.insert_role(connection, staff_id=staff_id, role=role.value, granted_at=stamp)
    except (IntegrityError, sqlite3.IntegrityError):
        print("refused: that staff id or mobile already exists")
        return 1
    finally:
        database.close()

    print(f"staff created: {staff_id} ({', '.join(role.value for role in dict.fromkeys(roles))})")
    print("next: tools/print_enroll_qr.py --staff-id " + staff_id + " pairs this person's phone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
