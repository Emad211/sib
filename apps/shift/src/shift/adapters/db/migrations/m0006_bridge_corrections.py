"""v6 — cash corrections: one acc_cash version per close, and voids remember their compensation.

An invoice that accounting reopens and closes again is a new accounting fact, not a duplicate: the
cash view returns it with a new `closed_at` (and possibly a new amount). `acc_cash` therefore keys on
`(invoice_id, closed_at)`; the version that was superseded keeps its row and gains `superseded_at`,
because R-03 forbids deleting or quietly overwriting a recorded fact.

`acc_void.compensated_at` remembers which voids were already turned into an `acc_cash_reversal`
event. Existing voids stay NULL on purpose: the first poll after this upgrade compensates the ones
whose cash this app had already ingested (that state is in `acc_cash`).

The cash watermark is rewound to its seed so the first poll after the upgrade re-reads every cash
row once. The mirror insert ignores rows it already has, so the re-read cannot double count; it is
what turns a re-close that the previous version silently dropped into a correction.
"""

from sqlalchemy import Connection

CASH_RECEIPTS_VIEW = "v_shift_cash_receipts_v1"


def up(connection: Connection) -> None:
    """Rebuild `acc_cash` on its new key, then mark the two correction bookkeeping columns."""
    connection.exec_driver_sql("ALTER TABLE acc_cash RENAME TO acc_cash_legacy")
    connection.exec_driver_sql(
        """CREATE TABLE acc_cash (
        invoice_id    INTEGER NOT NULL,
        closed_at     TEXT NOT NULL,
        amount        INTEGER NOT NULL,
        amount_raw    REAL NOT NULL,
        ingested_at   TEXT NOT NULL,
        superseded_at TEXT,
        PRIMARY KEY (invoice_id, closed_at)
    )"""
    )
    connection.exec_driver_sql(
        "INSERT INTO acc_cash (invoice_id, closed_at, amount, amount_raw, ingested_at) "
        "SELECT invoice_id, closed_at, amount, amount_raw, ingested_at FROM acc_cash_legacy"
    )
    connection.exec_driver_sql("DROP TABLE acc_cash_legacy")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_acc_cash_closed_at ON acc_cash (closed_at)")
    connection.exec_driver_sql("ALTER TABLE acc_void ADD COLUMN compensated_at TEXT")
    connection.exec_driver_sql(
        "UPDATE acc_watermark SET last_key_at = '', last_key_id = -1 WHERE view_name = ?",
        (CASH_RECEIPTS_VIEW,),
    )
