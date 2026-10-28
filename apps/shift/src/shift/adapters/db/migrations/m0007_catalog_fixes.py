"""v7 — catalog fixes: one accounting code names one item, and its lookup stays indexed.

Additive only: an index for the accounting bridge and the code-uniqueness check. No table
or row changes, and `IF NOT EXISTS` keeps the step idempotent.
"""

from sqlalchemy import Connection

from shift.adapters.db.sql_script import execute_script

SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_catalog_item_versions_acc_code
    ON catalog_item_versions (acc_code) WHERE acc_code IS NOT NULL;
"""


def up(connection: Connection) -> None:
    execute_script(connection, SCHEMA)
