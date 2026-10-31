"""The event table of the main store's records: payload model and projection effect per type (B1, G3).

Composed into the registry by `events.py`, like the purchase and shift-event tables. Only two of the
records move stock: the storekeeper's opening count sets the store, and a receiver's count adds the
parcel to it; an offer, a sample and a review are documents.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from shift.domain import store_payloads as store
from shift.domain.effects import EffectBuilder, Effects, StockEffect, StockOperation, no_effects
from shift.domain.event_types import EventType


def store_baseline(payload: Any) -> Effects:
    """The opening count sets every store shelf it names, in the order it was recorded."""
    return Effects(
        stock=tuple(
            StockEffect(line.item_id, store.MAIN_STORE, StockOperation.SET, line.quantity) for line in payload.lines
        )
    )


def parcel_joins_store(payload: Any) -> Effects:
    """What the storekeeper counted at delivery is added to the main store (owner decision 1)."""
    return Effects(
        stock=tuple(
            StockEffect(line.item_id, store.MAIN_STORE, StockOperation.ADD, line.quantity)
            for line in payload.lines
            if line.quantity
        )
    )


STORE_EVENT_SPECS: Mapping[EventType, tuple[type[BaseModel], EffectBuilder]] = {
    EventType.DELIVERY_CONFIRMED: (store.DeliveryConfirmed, parcel_joins_store),
    EventType.RESTOCK_OFFERED: (store.RestockOffered, no_effects),
    EventType.STOCK_STORE_BASELINED: (store.StoreBaselined, store_baseline),
    EventType.STOCK_SAMPLE_COUNT: (store.SampleCounted, no_effects),
    EventType.MANAGER_REVIEWED: (store.ManagerReviewed, no_effects),
}
