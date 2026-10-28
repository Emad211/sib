"""v1 — baseline schema: projection tables plus the SQLite event log and triggers."""

from importlib.resources import files

from sqlalchemy import Connection

from shift.adapters.db.sql_script import execute_script


def up(connection: Connection) -> None:
    package = files("shift.adapters.db")
    for resource in ("schema.sql", "sqlite/ddl.sql", "sqlite/triggers.sql"):
        execute_script(connection, package.joinpath(resource).read_text(encoding="utf-8"))
