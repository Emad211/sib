"""Walk an event log in `seq` order and report the first broken hash link."""

from collections.abc import Iterable
from dataclasses import dataclass

from shift.adapters.db.events_repo import EventRecord
from shift.common.hashing import GENESIS_HASH
from shift.domain.event_hash import event_hash


@dataclass(frozen=True, slots=True)
class ChainBreak:
    seq: int
    reason: str


@dataclass(frozen=True, slots=True)
class ChainReport:
    checked: int
    broken: ChainBreak | None

    @property
    def ok(self) -> bool:
        return self.broken is None


def verify_chain(records: Iterable[EventRecord], *, require_links: bool = True) -> ChainReport:
    """Verify every event hash and, when enabled, the link to its predecessor."""
    previous: EventRecord | None = None
    checked = 0
    for record in records:
        checked += 1
        if require_links:
            expected = GENESIS_HASH if previous is None else previous.hash
            if record.prev_hash != expected:
                return ChainReport(checked, ChainBreak(record.seq, "prev_hash does not link to the previous event"))
        if event_hash(record.prev_hash, record) != record.hash:
            return ChainReport(checked, ChainBreak(record.seq, "hash does not match the event content"))
        previous = record
    return ChainReport(checked, None)
