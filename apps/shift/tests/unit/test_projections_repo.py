"""Projection reads go through SQLAlchemy's public mapping API, never private row state."""

import ast
from pathlib import Path

from shift.adapters.db import projections_repo


def test_projection_rows_are_mapped_through_the_public_api() -> None:
    source = Path(projections_repo.__file__).read_text(encoding="utf-8")
    private_row_accesses = [
        node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute) and node.attr == "_mapping"
    ]

    assert private_row_accesses == []
