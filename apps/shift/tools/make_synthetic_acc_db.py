"""Build a synthetic accounting database from accounting's own schema and migrations (SHF-1.3).

The point is the contract test: the shift bridge is pinned against the *real* accounting DDL, created
by running `apps/accounting`'s `schema.sql` and its versioned migrations, so a breaking change in
those views breaks this build instead of production. Every row below is invented — no name, national
ID, phone number or amount comes from the clinic's database — and the scenario covers the cases the
bridge must survive: cash paid twice for one invoice, card lines, an invoice closed after the shift
cutoff, a refunded invoice, a cancelled invoice, a reopened invoice and an invoice still open.

Usage:
    python tools/make_synthetic_acc_db.py out.db
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType

APP_ROOT = Path(__file__).resolve().parents[1]
ACCOUNTING_ROOT = APP_ROOT.parent / "accounting"  # holds the `src` package accounting imports as `src.*`
ACCOUNTING_SCHEMA = ACCOUNTING_ROOT / "src" / "adapters" / "sqlite" / "schema.sql"

WORK_DATE = date(2026, 9, 24)
CUTOFF_TIME = "14:00:00"  # the handover of the morning shift; later invoices belong to the next one
VALID_NATIONAL_ID = "2170415981"  # invented, check digit correct
INVALID_NATIONAL_ID = "1234567890"
VALID_PHONE = "09123456789"


@dataclass(frozen=True, slots=True)
class SyntheticAccounting:
    """Where the database is and which invoice plays which role in the scenario."""

    path: Path
    schema_version: int
    cash_invoice: int
    cash_card_invoice: int
    before_cutoff_invoice: int
    after_cutoff_invoice: int
    refunded_invoice: int
    cancelled_invoice: int
    reopened_invoice: int
    open_invoice: int

    @property
    def work_date(self) -> date:
        """The business date every synthetic row belongs to."""
        return WORK_DATE

    @property
    def cutoff_at(self) -> str:
        """The handover instant, in accounting's timestamp format."""
        return f"{WORK_DATE} {CUTOFF_TIME}"


def _load_accounting_migrations() -> ModuleType:
    """Import accounting's real migration registry (its package is `src`, not installed)."""
    if not ACCOUNTING_SCHEMA.is_file():
        raise FileNotFoundError(f"accounting source not found: {ACCOUNTING_ROOT}")
    if str(ACCOUNTING_ROOT) not in sys.path:
        sys.path.insert(0, str(ACCOUNTING_ROOT))
    from src.adapters.sqlite import migrations  # noqa: PLC0415 — only importable with the path above

    return migrations


def _at(hhmmss: str) -> str:
    """A Tehran local timestamp on the scenario's work date."""
    return f"{WORK_DATE} {hhmmss}"


def _staff(connection: sqlite3.Connection) -> tuple[int, int, int]:
    """One doctor and two nurses with fixed ids, so the views' staff columns are predictable."""
    connection.executemany(
        "INSERT INTO medical_staff (id, full_name, staff_type) VALUES (?, ?, ?)",
        [(1, "دکتر آزمون", "doctor"), (7, "پرستار آزمون الف", "nurse"), (8, "پرستار آزمون ب", "nurse")],
    )
    return 1, 7, 8


def _patients(connection: sqlite3.Connection) -> tuple[int, int, int]:
    """Three invented patients: complete identity, missing identity, and a foreign patient."""
    rows = [
        ("آزمون", "یکم", VALID_NATIONAL_ID, VALID_PHONE, 0, "reception1", _at("07:40:00")),
        ("آزمون", "دوم", None, "12345", 0, "reception2", _at("08:20:00")),
        ("آزمون", "سوم", INVALID_NATIONAL_ID, None, 1, "reception1", _at("12:05:00")),
    ]
    identifiers = []
    for row in rows:
        cursor = connection.execute(
            "INSERT INTO patients (name, family_name, national_id, phone_number, is_foreign, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            row,
        )
        identifiers.append(int(cursor.lastrowid or 0))
    return identifiers[0], identifiers[1], identifiers[2]


def _consumable_tariffs(connection: sqlite3.Connection) -> None:
    """Two tariffs mapped to shift stock codes (one with a factor), one unmapped."""
    connection.executemany(
        "INSERT INTO consumable_tariffs (name, category, default_price, acc_code, stock_factor) VALUES (?, ?, ?, ?, ?)",
        [
            ("سرنگ ۵ سیسی", "supply", 5_000, "SYR-5", 1.0),
            ("سرم نیم‌لیتری", "drug", 85_000, "SER-500", 0.5),
            ("گاز استریل", "supply", 10_000, None, 1.0),
        ],
    )


def _invoice(
    connection: sqlite3.Connection,
    *,
    patient: int,
    doctor: int,
    nurse: int,
    shift: str,
    opened_at: str,
    closed_at: str | None,
    total: int,
) -> int:
    """Insert one invoice; `closed_at` None leaves it open."""
    cursor = connection.execute(
        "INSERT INTO invoices (patient_id, doctor_id, nurse_id, status, insurance_type, total_amount, work_date, "
        "shift, opened_at, closed_at, opened_by, closed_by) VALUES (?, ?, ?, ?, 'آزاد', ?, ?, ?, ?, ?, 'reception1', ?)",
        (
            patient,
            doctor,
            nurse,
            "closed" if closed_at else "open",
            total,
            WORK_DATE.isoformat(),
            shift,
            opened_at,
            closed_at,
            "reception1" if closed_at else None,
        ),
    )
    return int(cursor.lastrowid or 0)


def _payment(connection: sqlite3.Connection, invoice: int, item: str, item_id: int, method: str, amount: int) -> None:
    """One payment line, as accounting writes it when the invoice closes."""
    connection.execute(
        "INSERT INTO invoice_payment_lines (invoice_id, item_type, item_id, payment_type, patient_amount, closed_at) "
        "VALUES (?, ?, ?, ?, ?, (SELECT closed_at FROM invoices WHERE id = ?))",
        (invoice, item, item_id, method, amount, invoice),
    )


def _consumption(connection: sqlite3.Connection, invoice: int, item_name: str, quantity: int, shift: str) -> None:
    """One consumables-ledger line (clinic stock, not brought by the patient)."""
    connection.execute(
        "INSERT INTO consumables_ledger (patient_id, item_name, category, quantity, total_cost, patient_provided, "
        "usage_date, shift, work_date, invoice_id) VALUES (1, ?, 'supply', ?, 0, 0, ?, ?, ?, ?)",
        (item_name, quantity, _at("08:05:00"), shift, WORK_DATE.isoformat(), invoice),
    )


def _injection(connection: sqlite3.Connection, invoice: int, kind: str, nurse: int, shift: str) -> None:
    """One nursing service; only «تست قند» and «کنترل فشار» count as vitals (they earn a reward)."""
    connection.execute(
        "INSERT INTO injections (patient_id, injection_type, nurse_id, total_price, injection_date, shift, work_date, "
        "invoice_id) VALUES (1, ?, ?, 0, ?, ?, ?, ?)",
        (kind, nurse, _at("08:00:00"), shift, WORK_DATE.isoformat(), invoice),
    )


def _void(
    connection: sqlite3.Connection,
    invoice: int,
    *,
    kind: str,
    amount: int,
    method: str | None,
    voided_at: str,
) -> None:
    """What the manager's void route writes: the reason, the money, and status='voided'."""
    connection.execute(
        "INSERT INTO invoice_voids (invoice_id, kind, amount, refund_method, reason, voided_by, voided_at) "
        "VALUES (?, ?, ?, ?, 'ثبت آزمایشی', 'manager1', ?)",
        (invoice, kind, amount, method, voided_at),
    )
    connection.execute("UPDATE invoices SET status = 'voided' WHERE id = ?", (invoice,))


def build(path: str | Path) -> SyntheticAccounting:
    """Create the database at `path`, overwriting it, and return the scenario's invoice ids."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    schema_sql = ACCOUNTING_SCHEMA.read_text(encoding="utf-8")
    migrations = _load_accounting_migrations()

    connection = sqlite3.connect(target)
    try:
        connection.executescript(schema_sql)
        applied = migrations.run_migrations(connection)
        doctor, nurse_a, nurse_b = _staff(connection)
        first, second, third = _patients(connection)
        _consumable_tariffs(connection)

        cash = _invoice(
            connection,
            patient=first,
            doctor=doctor,
            nurse=nurse_a,
            shift="morning",
            opened_at=_at("08:00:00"),
            closed_at=_at("08:10:00"),
            total=200_000,
        )
        _payment(connection, cash, "visit", 1, "cash", 120_000)
        _payment(connection, cash, "injection", 1, "cash", 30_000)
        _payment(connection, cash, "procedure", 1, "card", 50_000)
        _consumption(connection, cash, "سرنگ ۵ سیسی", 3, "morning")
        _consumption(connection, cash, "سرم نیم‌لیتری", 4, "morning")
        _consumption(connection, cash, "گاز استریل", 2, "morning")
        _consumption(connection, cash, "دستکش", 5, "morning")  # no tariff yet, so no stock code
        connection.execute(
            "INSERT INTO consumables_ledger (patient_id, item_name, category, quantity, patient_provided, usage_date, "
            "shift, work_date, invoice_id) VALUES (?, 'سرنگ ۵ سیسی', 'supply', 9, 1, ?, 'morning', ?, ?)",
            (first, _at("08:04:00"), WORK_DATE.isoformat(), cash),
        )
        _injection(connection, cash, "تست قند", nurse_a, "morning")
        _injection(connection, cash, "کنترل فشار", nurse_b, "morning")
        _injection(connection, cash, "تزریق عضلانی", nurse_a, "morning")

        cash_card = _invoice(
            connection,
            patient=second,
            doctor=doctor,
            nurse=nurse_b,
            shift="morning",
            opened_at=_at("09:00:00"),
            closed_at=_at("09:05:00"),
            total=50_000,
        )
        _payment(connection, cash_card, "injection", 2, "cash", 40_000)
        _payment(connection, cash_card, "injection", 3, "card", 10_000)

        before_cutoff = _invoice(
            connection,
            patient=third,
            doctor=doctor,
            nurse=nurse_a,
            shift="evening",
            opened_at=_at("13:10:00"),
            closed_at=_at("13:40:00"),
            total=25_000,
        )
        _payment(connection, before_cutoff, "visit", 2, "cash", 25_000)
        _injection(connection, before_cutoff, "تست قند", nurse_a, "evening")

        after_cutoff = _invoice(
            connection,
            patient=first,
            doctor=doctor,
            nurse=nurse_b,
            shift="evening",
            opened_at=_at("14:05:00"),
            closed_at=_at("14:20:00"),
            total=999_000,
        )
        _payment(connection, after_cutoff, "procedure", 2, "cash", 999_000)

        refunded = _invoice(
            connection,
            patient=second,
            doctor=doctor,
            nurse=nurse_a,
            shift="morning",
            opened_at=_at("09:50:00"),
            closed_at=_at("10:00:00"),
            total=60_000,
        )
        _payment(connection, refunded, "visit", 3, "cash", 60_000)
        _consumption(connection, refunded, "سرنگ ۵ سیسی", 4, "morning")
        _void(connection, refunded, kind="refund", amount=60_000, method="cash", voided_at=_at("13:05:00"))

        cancelled = _invoice(
            connection,
            patient=third,
            doctor=doctor,
            nurse=nurse_b,
            shift="morning",
            opened_at=_at("10:50:00"),
            closed_at=_at("11:00:00"),
            total=15_000,
        )
        _payment(connection, cancelled, "injection", 4, "cash", 15_000)
        _void(connection, cancelled, kind="void", amount=15_000, method=None, voided_at=_at("15:30:00"))

        reopened = _invoice(
            connection,
            patient=first,
            doctor=doctor,
            nurse=nurse_a,
            shift="morning",
            opened_at=_at("11:20:00"),
            closed_at=_at("11:30:00"),
            total=70_000,
        )
        _payment(connection, reopened, "visit", 4, "cash", 70_000)
        _consumption(connection, reopened, "سرنگ ۵ سیسی", 6, "morning")
        # Reopen (accounting FB-25): the lines are removed and the invoice goes back to open.
        connection.execute("DELETE FROM invoice_payment_lines WHERE invoice_id = ?", (reopened,))
        connection.execute("UPDATE invoices SET status = 'open', closed_at = NULL WHERE id = ?", (reopened,))

        open_invoice = _invoice(
            connection,
            patient=second,
            doctor=doctor,
            nurse=nurse_a,
            shift="evening",
            opened_at=_at("13:50:00"),
            closed_at=None,
            total=0,
        )
        _injection(connection, open_invoice, "تست قند", nurse_a, "evening")
        connection.commit()
        row = connection.execute("PRAGMA user_version").fetchone()
        version = int(row[0]) if row is not None else applied.to_version
    finally:
        connection.close()

    return SyntheticAccounting(
        path=target,
        schema_version=version,
        cash_invoice=cash,
        cash_card_invoice=cash_card,
        before_cutoff_invoice=before_cutoff,
        after_cutoff_invoice=after_cutoff,
        refunded_invoice=refunded,
        cancelled_invoice=cancelled,
        reopened_invoice=reopened,
        open_invoice=open_invoice,
    )


def main(argv: list[str] | None = None) -> int:
    """Write the synthetic database and check its integrity."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    arguments = parser.parse_args(argv)
    scenario = build(arguments.out)
    connection = sqlite3.connect(scenario.path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if result is None or result[0] != "ok":
        print(f"synthetic accounting database failed integrity_check: {result!r}", file=sys.stderr)
        return 1
    print(
        f"synthetic accounting database written: {scenario.path} "
        f"(schema v{scenario.schema_version}, cutoff {scenario.cutoff_at})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
