"""Stock and cash projections match an independent replay of the same events."""

from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from event_fixtures import random_events
from shift.adapters.db import projections_repo
from shift.adapters.db.core import Database
from shift.domain.effects import CashOperation, StockOperation
from shift.domain.events import effects_for, spec_for
from shift.services.event_store import EventStore
from shift.services.projections import ProjectionEngine


def _replay(events) -> tuple[dict[tuple[str, str], int], dict[str, dict[str, int]]]:
    """Replay every event by hand; the cash side mirrors the per-box rule (ADR-017 item 8)."""
    stock: dict[tuple[str, str], int] = {}
    cash: dict[str, dict[str, int]] = {}
    for event in events:
        spec = spec_for(event.type)
        payload = spec.payload_model.model_validate(event.payload)
        effects = effects_for(event.type, payload, event.shift_id)
        if effects.stock is not None:
            key = (effects.stock.item_id, effects.stock.location_id)
            if effects.stock.operation is StockOperation.SET:
                stock[key] = effects.stock.quantity
            elif effects.stock.operation is StockOperation.ADD:
                stock[key] = stock.get(key, 0) + effects.stock.quantity
            else:
                stock[key] = stock.get(key, 0) - effects.stock.quantity
        if effects.cash is not None:
            box = str(effects.cash.cash_box_id)
            seeded = box in cash
            row = cash.setdefault(box, {"expected": 0, "counted": 0, "variance": 0})
            if effects.cash.operation is CashOperation.OPEN:
                if not seeded:  # the opening only seeds a box that has no figure yet
                    row["expected"] = effects.cash.amount
                    row["counted"] = effects.cash.amount
            elif effects.cash.operation is CashOperation.ADD:
                row["expected"] += effects.cash.amount
            elif effects.cash.operation is CashOperation.SUBTRACT:
                row["expected"] -= effects.cash.amount
            elif effects.cash.operation is CashOperation.COUNT:
                row["counted"] = effects.cash.amount
            elif effects.cash.operation is CashOperation.SETTLE:
                row["expected"] = effects.cash.amount
                row["counted"] = effects.cash.amount
            row["variance"] = row["counted"] - row["expected"]
    return stock, cash


def _stock_dict(rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    return {(row["item_id"], row["location_id"]): row["qty"] for row in rows}


def _cash_dict(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        row["cash_box_id"]: {
            "expected": row["expected"],
            "counted": row["counted"],
            "variance": row["variance"],
        }
        for row in rows
    }


@given(seed=st.integers(min_value=0, max_value=10_000))
@settings(
    max_examples=1,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_stock_and_cash_match_independent_replay(
    seed: int, database: Database, store: EventStore, projections: ProjectionEngine
) -> None:
    events = random_events(seed, 300)
    store.append_many(events)
    projections.sync()

    expected_stock, expected_cash = _replay(events)
    with database.read() as connection:
        assert _stock_dict(projections_repo.stock_rows(connection)) == expected_stock
        assert _cash_dict(projections_repo.cash_rows(connection)) == expected_cash
