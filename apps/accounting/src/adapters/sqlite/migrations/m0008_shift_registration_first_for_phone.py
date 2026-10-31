"""v8 — which registration is the first for a mobile, and a call sample for the manager (owner decision 5).

Two new views, additive only (ADR-002); every `_v1` view and every table stays exactly as it was:

* `v_shift_patient_registrations_v2` — the v1 columns in the same order, plus `first_for_phone`.
  It is 1 for the lowest `patients.id` among the patients sharing one phone number (the phone is
  matched as written, and only when it is valid, i.e. `_valid_phone_sql`), and 1 as well when the
  phone is not valid at all: a patient without a mobile can never share somebody else's reward.
  The shift app pays a registration reward once per mobile, so it needs a fact it can trust.
* `v_shift_call_sample_v1` — name and mobile of patients with a valid phone, for the manager's
  «نمونهٔ تماس» card. The shift app reads at most a few random rows a day and never stores them.

Accounting itself keeps only warning about duplicate mobiles; nothing is refused here.
"""

import sqlite3

from .m0004_shift_contract import _valid_national_id_sql, _valid_phone_sql


def _valid_phone(col: str) -> str:
    return _valid_phone_sql(col)


VIEWS_V2: dict[str, str] = {
    "v_shift_patient_registrations_v2": f"""
CREATE VIEW v_shift_patient_registrations_v2 AS
SELECT p.id AS patient_id, p.created_by, p.created_at,
       CASE WHEN {_valid_national_id_sql("p.national_id")} THEN 1 ELSE 0 END AS has_valid_nid,
       CASE WHEN {_valid_phone("p.phone_number")} THEN 1 ELSE 0 END AS has_valid_phone,
       COALESCE(p.is_foreign, 0) AS is_foreign,
       CASE
           WHEN NOT {_valid_phone("p.phone_number")} THEN 1
           WHEN p.id = (SELECT MIN(q.id) FROM patients q
                        WHERE {_valid_phone("q.phone_number")} AND q.phone_number = p.phone_number) THEN 1
           ELSE 0
       END AS first_for_phone
FROM patients p
""",
}

CALL_SAMPLE_VIEWS: dict[str, str] = {
    "v_shift_call_sample_v1": f"""
CREATE VIEW v_shift_call_sample_v1 AS
SELECT p.id AS patient_id, p.full_name, p.phone_number, p.created_at, p.created_by
FROM patients p
WHERE {_valid_phone("p.phone_number")}
""",
}


def _replace(conn: sqlite3.Connection, views: dict[str, str]) -> None:
    """Drop and recreate: `CREATE VIEW IF NOT EXISTS` would silently keep an older body."""
    for name, sql in views.items():
        conn.execute(f"DROP VIEW IF EXISTS {name}")
        conn.execute(sql)


def up(conn: sqlite3.Connection) -> None:
    _replace(conn, VIEWS_V2)
    _replace(conn, CALL_SAMPLE_VIEWS)
