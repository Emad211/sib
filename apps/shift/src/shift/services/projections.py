"""Projection engine: incremental sync and full rebuild share one apply function."""

from dataclasses import dataclass

from sqlalchemy import Connection

from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, utc_iso
from shift.domain.effects import Effects
from shift.domain.events import UnknownEventType, effects_for, spec_for


class ProjectionError(RuntimeError):
    """A stored event cannot be projected; the rebuild must not silently skip it."""


@dataclass(frozen=True, slots=True)
class SyncReport:
    processed: int
    last_seq: int


@dataclass(frozen=True, slots=True)
class RebuildReport:
    events: int
    last_seq: int
    live_checksums: dict[str, str]
    rebuilt_checksums: dict[str, str]
    applied: bool

    @property
    def drift(self) -> tuple[str, ...]:
        return tuple(
            name for name in projections_repo.PROJECTIONS if self.live_checksums[name] != self.rebuilt_checksums[name]
        )


class ProjectionEngine:
    """Maintains `proj_stock` and `proj_cash` from the event log."""

    def __init__(self, database: Database, *, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or SystemClock()

    def sync(self) -> SyncReport:
        """Apply every event after the last applied seq, atomically and per projection."""
        with self._database.write() as connection:
            projections_repo.ensure_meta(connection)
            last_stock = projections_repo.last_seq(connection, projections_repo.STOCK)
            last_cash = projections_repo.last_seq(connection, projections_repo.CASH)
            start = min(last_stock, last_cash)
            events = list(events_repo.iter_events(connection, start))
            for event in events:
                self._apply(
                    connection,
                    event,
                    projections_repo.STOCK,
                    projections_repo.CASH,
                    include_stock=event.seq > last_stock,
                    include_cash=event.seq > last_cash,
                )
                last_stock = max(last_stock, event.seq)
                last_cash = max(last_cash, event.seq)
            if events:
                projections_repo.set_meta(connection, projections_repo.STOCK, last_stock, checksum=None, built_at=None)
                projections_repo.set_meta(connection, projections_repo.CASH, last_cash, checksum=None, built_at=None)
            return SyncReport(processed=len(events), last_seq=events[-1].seq if events else start)

    def rebuild(self, *, dry_run: bool = False) -> RebuildReport:
        """Replay all events into shadow tables, compare checksums and swap (unless dry run)."""
        with self._database.write(commit=not dry_run) as connection:
            projections_repo.ensure_meta(connection)
            projections_repo.create_shadow_tables(connection)
            last_seq = 0
            count = 0
            for event in events_repo.iter_events(connection):
                self._apply(connection, event, projections_repo.STOCK_SHADOW, projections_repo.CASH_SHADOW)
                last_seq = event.seq
                count += 1
            live = {name: projections_repo.checksum(connection, name, name) for name in projections_repo.PROJECTIONS}
            rebuilt = {
                name: projections_repo.checksum(connection, name, self._shadow_for(name))
                for name in projections_repo.PROJECTIONS
            }
            if not dry_run:
                projections_repo.swap_shadow(connection)
                built_at = utc_iso(self._clock.now_utc())
                for name in projections_repo.PROJECTIONS:
                    projections_repo.set_meta(connection, name, last_seq, rebuilt[name], built_at)
            return RebuildReport(count, last_seq, live, rebuilt, applied=not dry_run)

    def _apply(
        self,
        connection: Connection,
        event: EventRecord,
        stock_table: str,
        cash_table: str,
        *,
        include_stock: bool = True,
        include_cash: bool = True,
    ) -> None:
        try:
            spec = spec_for(event.type)
            payload = spec.payload_model.model_validate(event.payload)
            effects: Effects = effects_for(event.type, payload, event.shift_id)
        except (UnknownEventType, ValueError, TypeError) as exc:
            raise ProjectionError(f"event seq {event.seq} cannot be projected: {exc}") from exc
        if include_stock and effects.stock is not None:
            projections_repo.apply_stock(connection, stock_table, effects.stock)
        if include_cash and effects.cash is not None:
            if not event.shift_id:
                raise ProjectionError(f"event seq {event.seq} has a cash effect without shift_id")
            projections_repo.apply_cash(connection, cash_table, effects.cash, event.shift_id)

    @staticmethod
    def _shadow_for(name: str) -> str:
        return projections_repo.STOCK_SHADOW if name == projections_repo.STOCK else projections_repo.CASH_SHADOW
