"""The DDL splitter must keep trigger bodies together and drop empty tails."""

from shift.adapters.db.sql_script import split_statements


def test_split_statements_keeps_trigger_body_whole() -> None:
    script = """
-- comment before a statement
CREATE TABLE IF NOT EXISTS demo (id INTEGER PRIMARY KEY);

CREATE TRIGGER IF NOT EXISTS demo_no_update BEFORE UPDATE ON demo
BEGIN
    SELECT RAISE(ABORT, 'append-only');
END;

CREATE INDEX IF NOT EXISTS idx_demo_id ON demo (id);
"""

    statements = split_statements(script)

    assert len(statements) == 3
    assert statements[0].startswith("-- comment before a statement")
    assert statements[1].startswith("CREATE TRIGGER")
    assert "RAISE(ABORT" in statements[1]
    assert statements[2].startswith("CREATE INDEX")


def test_split_statements_ignores_blank_script() -> None:
    assert split_statements("\n\n") == []
