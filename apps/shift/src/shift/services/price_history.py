"""F2: the price history of every item and vendor, read from the digitized invoices.

The domain rule is `domain/price_history.py`: the log is the whole answer, so nothing is stored and a
rebuild cannot disagree. This service only reads the log and adds the one thing a screen needs — the
item's own name — so no page ever prints an id. Prices are read by the accountant and the manager, who
together are the roles holding the accountant queue's permission (S01 §3, F2).
"""

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.domain.price_history import PriceSeries, price_history
from shift.services.catalog import CatalogService


class PriceHistoryService:
    """Reads the invoice lines once and hands the screen every (item, vendor) series."""

    def __init__(self, database: Database, catalog: CatalogService) -> None:
        self._database = database
        self._catalog = catalog

    def series(self) -> tuple[PriceSeries, ...]:
        """Every price series of the log, sorted by item and vendor."""
        with self._database.read() as connection:
            events = list(events_repo.iter_events(connection))
        return price_history(events)

    def item_names(self) -> dict[str, str]:
        """Item id to name, retired items included: an old invoice still names a real item."""
        return {item.item_id: item.name for item in self._catalog.items(include_retired=True)}
