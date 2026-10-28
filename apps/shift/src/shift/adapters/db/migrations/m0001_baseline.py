"""v1 — projection bookkeeping tables; the SQLite event log arrives with step 4."""

from importlib.resources import files

from sqlalchemy import Connection

from shift.adapters.db.sql_script import execute_script


def up(connection: Connection) -> None:
    schema = files("shift.adapters.db").joinpath("schema.sql").read_text(encoding="utf-8")
    execute_script(connection, schema)
