"""Repository reads go through SQLAlchemy's public mapping API, never private row state.

The cash projection is keyed per cash box (ADR-017 item 8): the opening seeds a box once and never
overwrites a live figure, so after the first shift the box's number comes from the counts.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import URL

from shift.adapters.db import projections_repo
from shift.domain.cash_box import CashBox
from shift.domain.effects import CashEffect, CashOperation


@pytest.fixture()
def connection(tmp_path: Path) -> Iterator[Connection]:
    """A database holding the projection tables, borrowed from the rebuild's shadow DDL."""
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(tmp_path / "projections.db")))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        projections_repo.create_shadow_tables(connection)
        yield connection
    engine.dispose()


def test_no_repository_reads_private_row_state() -> None:
    """Every module under `adapters/db` must map rows with `result.mappings()` (F3, ADR-003)."""
    offenders = []
    for module in sorted(Path(projections_repo.__file__).parent.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        offenders += [module.name for node in ast.walk(tree) if _is_private_mapping(node)]

    assert offenders == []


def _is_private_mapping(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "_mapping"


def test_cash_effects_land_on_one_box_row_and_an_open_never_overwrites_it(connection: Connection) -> None:
    table = projections_repo.CASH_SHADOW
    box = CashBox.RECEPTION.value
    projections_repo.apply_cash(connection, table, CashEffect(CashOperation.OPEN, 1_000_000, box))
    projections_repo.apply_cash(connection, table, CashEffect(CashOperation.ADD, 200_000, box))
    projections_repo.apply_cash(connection, table, CashEffect(CashOperation.SUBTRACT, 50_000, box))
    projections_repo.apply_cash(connection, table, CashEffect(CashOperation.COUNT, 900_000, box))
    settled = [{"cash_box_id": box, "expected": 1_150_000, "counted": 900_000, "variance": -250_000}]
    assert projections_repo.cash_rows(connection, table) == settled

    projections_repo.apply_cash(connection, table, CashEffect(CashOperation.OPEN, 500_000, box))

    assert projections_repo.cash_rows(connection, table) == settled


def test_a_cash_effect_without_a_box_is_refused(connection: Connection) -> None:
    with pytest.raises(ValueError, match="cash box"):
        projections_repo.apply_cash(connection, projections_repo.CASH_SHADOW, CashEffect(CashOperation.ADD, 1, None))


def test_the_cash_checksum_covers_the_box_id(connection: Connection) -> None:
    table = projections_repo.CASH_SHADOW
    projections_repo.apply_cash(connection, table, CashEffect(CashOperation.OPEN, 10, CashBox.RECEPTION.value))
    single_box = projections_repo.checksum(connection, projections_repo.CASH, table)

    projections_repo.apply_cash(connection, table, CashEffect(CashOperation.ADD, 1, "second-box"))

    assert projections_repo.checksum(connection, projections_repo.CASH, table) != single_box
