"""Blind count chains: one item (H-05, Q-09) and the cash box (H-13), plus the emergency variant.

Both chains compare on the server and answer with a `CountStatus` only, never a count, reference
or delta (R-02, ADR-011). `culprit_id` is the outgoing person a named discrepancy lands on; it
is None when the outgoing person has left and the incoming one finishes alone (H-08), which also
shortens the chain to one self-recount.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

from shift.domain.handover_formulas import Discrepancy, HandoverError, difference


class CountStatus(StrEnum):
    """What a count tells its operator — a status, and the only thing it tells them (R-02)."""

    FIRST = "first"
    INCOMING = "incoming"
    RECOUNT = "recount"
    OUTGOING = "outgoing"
    JOINT = "joint"
    MATCHED = "matched"
    SETTLED = "settled"


@dataclass(frozen=True, slots=True)
class _BlindChain:
    """What both chains share: the reference, who a gap lands on, and the settled figure."""

    reference: int
    culprit_id: str | None
    status: CountStatus = CountStatus.FIRST
    agreed: int | None = None
    discrepancy: Discrepancy | None = None

    @property
    def settled(self) -> bool:
        """True once the agreed figure is fixed and the chain needs no more counting."""
        return self.status in {CountStatus.MATCHED, CountStatus.SETTLED}

    def _settled(self, counted: int) -> tuple[Self, CountStatus]:
        gap = difference(self.reference, counted)
        discrepancy = None if gap == 0 else Discrepancy(gap, self.culprit_id)
        status = CountStatus.SETTLED if discrepancy else CountStatus.MATCHED
        return replace(self, status=status, agreed=counted, discrepancy=discrepancy), status

    @staticmethod
    def _refuse_negative(counted: int) -> None:
        """A count is what the shelf or box actually holds, so it is never below zero."""
        if counted < 0:
            raise HandoverError("a count cannot be negative")

    def _count_alone(self, counted: int) -> tuple[Self, CountStatus]:
        """Alone: one count, then one self-recount before an unnamed settlement (H-08)."""
        if counted == self.reference:
            return self._settled(counted)
        if self.status is CountStatus.FIRST:
            return replace(self, status=CountStatus.RECOUNT), CountStatus.RECOUNT
        return self._settled(counted)


@dataclass(frozen=True, slots=True)
class ItemCount(_BlindChain):
    """One item's blind chain (H-05, Q-09); `culprit_id` None means the incoming person counts alone."""

    def count(self, counted: int) -> tuple[Self, CountStatus]:
        """Take the count the chain asks for and say what the operator does next."""
        self._refuse_negative(counted)
        if self.settled:
            raise HandoverError("this item is already settled")
        if self.culprit_id is None:
            return self._count_alone(counted)
        if counted == self.reference or self.status is CountStatus.JOINT:
            return self._settled(counted)
        following = {
            CountStatus.FIRST: (CountStatus.RECOUNT, CountStatus.RECOUNT),
            CountStatus.RECOUNT: (CountStatus.OUTGOING, CountStatus.OUTGOING),
            CountStatus.OUTGOING: (CountStatus.JOINT, CountStatus.JOINT),
        }
        status, told = following[self.status]
        return replace(self, status=status), told


@dataclass(frozen=True, slots=True)
class CashCount(_BlindChain):
    """The cash box's blind chain (H-13): two blind counts, then one joint recount if they differ."""

    first: int | None = None

    def count(self, counted: int) -> tuple[Self, CountStatus]:
        """Take the count the chain asks for and say what the operator does next."""
        self._refuse_negative(counted)
        if self.settled:
            raise HandoverError("the cash box is already settled")
        if self.culprit_id is None:
            return self._count_alone(counted)
        if self.status is CountStatus.FIRST:
            return replace(self, status=CountStatus.INCOMING, first=counted), CountStatus.INCOMING
        if counted == self.first:
            return self._settled(counted)
        if self.status is CountStatus.INCOMING:
            return replace(self, status=CountStatus.JOINT), CountStatus.JOINT
        return self._settled(counted)
