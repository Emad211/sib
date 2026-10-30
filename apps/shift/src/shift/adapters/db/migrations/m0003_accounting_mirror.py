"""v3 — mirrors of the accounting facts plus one watermark per read view (SHF-1.3, S04 §2).

The bridge copies accounting rows into `acc_*` tables before they become events: the mirrors are
what the handover screens read (fast, local, no accounting lock), and the events are what
projections replay. `acc_watermark` remembers how far each view has been read, in the same
transaction as the rows it covers, so a crash cannot lose or double count a row.

Money is stored twice on purpose: `amount` is the integer Toman the shift app counts, `amount_raw`
is accounting's REAL value, kept for audit and never used in arithmetic.
"""

from sqlalchemy import Connection

_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS acc_watermark (
        view_name    TEXT PRIMARY KEY,
        last_key_at  TEXT NOT NULL,
        last_key_id  INTEGER NOT NULL,
        updated_at   TEXT NOT NULL,
        ok           INTEGER NOT NULL DEFAULT 1,
        error        TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS acc_cash (
        invoice_id  INTEGER PRIMARY KEY,
        closed_at   TEXT NOT NULL,
        amount      INTEGER NOT NULL,
        amount_raw  REAL NOT NULL,
        ingested_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_acc_cash_closed_at ON acc_cash (closed_at)",
    """CREATE TABLE IF NOT EXISTS acc_consumption (
        ledger_id    INTEGER PRIMARY KEY,
        invoice_id   INTEGER NOT NULL,
        closed_at    TEXT NOT NULL,
        acc_code     TEXT,
        quantity     INTEGER NOT NULL,
        quantity_raw REAL NOT NULL,
        item_name    TEXT NOT NULL,
        ingested_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_acc_consumption_closed_at ON acc_consumption (closed_at)",
    """CREATE TABLE IF NOT EXISTS acc_vital (
        injection_id INTEGER PRIMARY KEY,
        invoice_id   INTEGER NOT NULL,
        nurse_id     INTEGER,
        kind         TEXT NOT NULL,
        closed_at    TEXT NOT NULL,
        ingested_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_acc_vital_closed_at ON acc_vital (closed_at)",
    """CREATE TABLE IF NOT EXISTS acc_registration (
        patient_id      INTEGER PRIMARY KEY,
        created_by      TEXT,
        created_at      TEXT NOT NULL,
        has_valid_nid   INTEGER NOT NULL,
        has_valid_phone INTEGER NOT NULL,
        is_foreign      INTEGER NOT NULL,
        ingested_at     TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_acc_registration_created_at ON acc_registration (created_at)",
    """CREATE TABLE IF NOT EXISTS acc_open_invoice (
        invoice_id   INTEGER PRIMARY KEY,
        opened_at    TEXT,
        opened_by    TEXT,
        shift        TEXT,
        work_date    TEXT,
        refreshed_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS acc_void (
        void_id       INTEGER PRIMARY KEY,
        invoice_id    INTEGER NOT NULL UNIQUE,
        kind          TEXT NOT NULL,
        amount        INTEGER NOT NULL,
        amount_raw    REAL NOT NULL,
        refund_method TEXT,
        reason        TEXT NOT NULL,
        voided_by     TEXT,
        voided_at     TEXT NOT NULL,
        work_date     TEXT,
        shift         TEXT,
        ingested_at   TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_acc_void_voided_at ON acc_void (voided_at)",
)


def up(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
