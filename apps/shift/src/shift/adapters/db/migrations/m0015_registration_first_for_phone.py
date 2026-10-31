"""v15 — the duplicate-mobile flag the accounting mirror carries (owner decision 5, S01 §5).

A registration reward is earned once per mobile, so the mirror has to know which registration was the
first for its phone. Accounting answers that in `v_shift_patient_registrations_v2` (accounting
migration 8, `first_for_phone`). The bridge keeps the answer beside the registration it belongs to,
because the reward ledger reads the mirror and never accounting.

The column defaults to 1: an accounting install that has not been upgraded has no v2 view, the bridge
reads v1, and every registration there counts as the first for its mobile — the old behaviour, which
is the only one that install can support.

Additive only, one nullable-free column with a default, so an installed v14 database keeps every row
it had (ADR-002).
"""

from sqlalchemy import Connection

_STATEMENTS = ("ALTER TABLE acc_registration ADD COLUMN first_for_phone INTEGER NOT NULL DEFAULT 1",)


def up(connection: Connection) -> None:
    """Add the one flag column; every existing registration keeps its reward eligibility."""
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
