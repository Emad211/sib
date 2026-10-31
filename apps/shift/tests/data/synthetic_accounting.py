"""A synthetic accounting copy with nothing in it, the state a clinic day starts in.

`make_synthetic_acc_db` builds accounting's own schema and `v_shift_*` views through its migrations
and fills a full scenario; these helpers keep that real contract but empty the invoice side, so a
handover starts with no open invoice and no cash receipt. Every row here is invented.
"""

import sqlite3
from pathlib import Path

from make_synthetic_acc_db import build

INVOICE_TABLES = ("invoice_payment_lines", "invoice_voids", "invoices")


def empty_accounting(path: Path) -> Path:
    """Build the real accounting schema, then clear the invoices, their lines and their voids."""
    build(path)
    connection = sqlite3.connect(path)
    try:
        for table in INVOICE_TABLES:
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()
    return path


def close_open_invoices(path: Path, closed_at: str) -> None:
    """Accounting's own close route would do this; here it just clears the H-03 guard."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE invoices SET status = 'closed', closed_at = ? WHERE status = 'open'", (closed_at,))
        connection.commit()
    finally:
        connection.close()
