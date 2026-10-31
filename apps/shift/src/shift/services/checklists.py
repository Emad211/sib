"""The checklist lines one handover flow asks, from the catalog and the log (A3, S01 §2).

A flow asks the lines whose responsible role belongs to its own people (the trolley to nursing, the
general space and the doctor's room to reception) and, for a line asked daily or weekly, only when nobody
has answered it yet in the current period. The catalog gives the lines in the order of the shelf.
"""

from datetime import date

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.domain.checklist_rules import Frequency, asked_in_flow, is_due
from shift.domain.events import EventType
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.services.catalog import CatalogService, Item, ItemKind

_ANSWERED = EventType.HANDOVER_CHECKLIST_ANSWERED.value


class ChecklistAsker:
    """Reads which checklist lines a flow owns and which of them are due in a shift."""

    def __init__(self, catalog: CatalogService, database: Database) -> None:
        self._catalog = catalog
        self._database = database

    def owned(self, flow: HandoverFlow, shift: ShiftKey) -> list[Item]:
        """Every checklist line the flow's people answer for, due or not, in shelf order."""
        return [
            item
            for item in self._catalog.items(shift)
            if item.kind is not ItemKind.COUNTED and asked_in_flow(item.responsible_role, flow)
        ]

    def due(self, flow: HandoverFlow, shift: ShiftKey) -> list[Item]:
        """The lines asked in this shift: every-shift ones, and the others once their period has no answer."""
        lines = self.owned(flow, shift)
        with self._database.read() as connection:
            last = {
                item.item_id: events_repo.last_work_date_of_item(connection, _ANSWERED, item.item_id)
                for item in lines
                if item.frequency is not Frequency.EVERY_SHIFT
            }
        return [item for item in lines if is_due(item.frequency, shift.work_date, _day(last.get(item.item_id)))]


def _day(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text)
