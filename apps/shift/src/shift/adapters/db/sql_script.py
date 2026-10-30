"""Split and execute multi-statement SQLite DDL through SQLAlchemy Core.

`exec_driver_sql` accepts one statement at a time, while `executescript` would commit
the enclosing migration transaction; this splitter keeps trigger bodies intact.
"""

import sqlite3

from sqlalchemy import Connection


def split_statements(script: str) -> list[str]:
    """Return complete SQL statements, keeping trigger BEGIN ... END bodies whole."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    tail = buffer.strip()
    if tail:
        statements.append(tail)
    return statements


def execute_script(connection: Connection, script: str) -> None:
    """Execute every statement in `script` inside the caller's transaction."""
    for statement in split_statements(script):
        connection.exec_driver_sql(statement)
