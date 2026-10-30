"""Repository reads go through SQLAlchemy's public mapping API, never private row state."""

import ast
from pathlib import Path

from shift.adapters.db import projections_repo


def test_no_repository_reads_private_row_state() -> None:
    """Every module under `adapters/db` must map rows with `result.mappings()` (F3, ADR-003)."""
    offenders = []
    for module in sorted(Path(projections_repo.__file__).parent.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        offenders += [module.name for node in ast.walk(tree) if _is_private_mapping(node)]

    assert offenders == []


def _is_private_mapping(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "_mapping"
