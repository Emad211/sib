"""The main store's own actions: its opening count, the weekly full count, the monthly sample, and the
two-sided hand-overs of a parcel and of a shelf charge (S01 §4, owner decisions 1 and 2, G3).

The rules this module carries, in the owner's words:

* The storekeeper counts the whole main store once before anything is compared with it (`store.baseline`).
* Vendor goods join the store's stock on the **storekeeper's own count** at delivery (`delivery.confirm`):
  reception wrote what it counted at the door, and the receiver counts blind. A difference never blocks the
  count; it flags the parcel so the manager decides and the payment waits.
* A shelf charge needs both sides: the storekeeper offers it, the person named for the shelf confirms it by
  counting what arrived, and only the confirmation moves stock (`restock.record`, `restock.confirm`).
* The weekly count covers every item of the store: one counted item does not close the week
  (`weekly.count`), and a near-expiry mark rides on the item's count.
* The accountant counts five items a month blind (`sample.count`); the sample compares and never sets.

Every handler runs under `PurchaseService.perform`'s single lock, so a rule that reads the folded log and
then appends cannot interleave with a second request. No answer carries a figure of the store: a blind
count answers with event ids and a yes/no, never a difference.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from shift.adapters.db import events_repo, projections_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, parse_iso, to_tehran
from shift.domain.event_types import EventType
from shift.domain.handover_formulas import difference
from shift.domain.handover_payloads import DiscrepancyRecorded
from shift.domain.handover_steps import FLOW_LOCATIONS, FLOW_OWNER_ROLES
from shift.domain.on_shift import held_roles
from shift.domain.payloads import WeeklyCount
from shift.domain.permissions import Permission, Role
from shift.domain.policy import SettingKey, ShiftKey
from shift.domain.purchase_deliveries import DeliveryState, delivery_states
from shift.domain.purchase_payloads import DeliveryFlagged, ExpiryFlagged, RestockConfirmed
from shift.domain.rewards import month_of
from shift.domain.stock_checks import WeeklyStatus, weekly_status, weekly_window
from shift.domain.store_payloads import (
    DeliveryConfirmed,
    RestockOffered,
    SampleCounted,
    ShelfReturnConfirmed,
    ShelfReturnOffered,
    StoreBaselined,
)
from shift.domain.store_sample import monthly_sample
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, ItemKind
from shift.services.event_store import EventStore
from shift.services.purchase_rules import (
    BASELINE_DONE,
    BASELINE_ITEMS,
    BASELINE_ROLE,
    CONFIRM_LINES,
    CONFIRM_OWN,
    CONFIRM_RETURNED,
    CONFIRMER_IS_GIVER,
    ITEM_NOT_ON_SHELF,
    ITEM_NOT_THERE,
    MAIN_STORE,
    NEEDS_BASELINE,
    NOT_A_COUNTED_ITEM,
    NOT_AWAITING,
    NOT_IN_SAMPLE,
    NOT_THE_CONFIRMER,
    NOT_THE_RECEIVER,
    NOT_THE_STORE,
    OFFER_CLOSED,
    OFFER_COUNT,
    RESTOCK_FROM_STORE,
    RETURN_COUNT,
    RETURN_OWN_SHELF,
    RETURN_TO_STORE,
    RETURN_TO_STOREKEEPER,
    SAME_LOCATION,
    SAMPLE_DONE,
    SAMPLE_ROLE,
    UNKNOWN_CONFIRMER,
    UNKNOWN_LOCATION,
    UNKNOWN_OFFER,
    WEEKLY_ROLE,
    PurchaseRuleError,
)
from shift.services.purchase_support import body, new_event
from shift.services.settings import SettingsService

# Action name -> (permission, handler on `StoreActions`); `PurchaseService` merges these into its own table.
ACTIONS: Mapping[str, tuple[Permission, str]] = {
    "delivery.confirm": (Permission.PARCEL_HANDLE, "delivery_confirm"),
    # The giver's half needs the same permission the one-step charge needed; the receiver can be anybody
    # of the clinic's staff the giver names (reception owns a shelf and holds no restock permission).
    "restock.record": (Permission.STOCK_RESTOCK_CONFIRM, "restock_offer"),
    "restock.confirm": (Permission.EVENTS_APPEND, "restock_confirm"),
    # A2: goods back from a shelf. The store is opened by the storekeeper, so the return happens with both
    # present: the person who answers for that shelf gives, the storekeeper counts what arrived, blind, and
    # only that count moves stock (the shelf down and the store up together).
    "shelf_return.record": (Permission.EVENTS_APPEND, "shelf_return_offer"),
    "shelf_return.confirm": (Permission.EVENTS_APPEND, "shelf_return_confirm"),
    "weekly.count": (Permission.STOCK_COUNT_SUBMIT, "weekly_count"),
    "store.baseline": (Permission.STOCK_COUNT_SUBMIT, "store_baseline"),
    "sample.count": (Permission.STOCK_COUNT_SUBMIT, "sample_count"),
}
# (offered, confirmed) event types of the two-sided shelf movements, keyed by "is it a return".
_OFFER_TYPES = {
    False: (EventType.RESTOCK_OFFERED, EventType.RESTOCK_CONFIRMED),
    True: (EventType.SHELF_RETURN_OFFERED, EventType.SHELF_RETURN_CONFIRMED),
}
_COUNTERS = frozenset({Role.STOREKEEPER.value, Role.MANAGER.value})
SAMPLERS = frozenset({Role.ACCOUNTANT.value, Role.MANAGER.value})
_MISMATCH_NOTE = "شمارش انباردار با شمارش پذیرش نمی‌خواند."


class StoreActions:
    """Writes the main store's records and reads the pieces of its state the purchase page needs."""

    def __init__(
        self,
        database: Database,
        events: EventStore,
        catalog: CatalogService,
        settings: SettingsService,
        clock: Clock,
    ) -> None:
        self._database = database
        self._events = events
        self._catalog = catalog
        self._settings = settings
        self._clock = clock

    # --- reading ---------------------------------------------------------------------------

    def store_item_ids(self, shift: ShiftKey) -> list[str]:
        """Every counted item of the clinic: the store holds them all and every shelf is charged from it.

        The list is the catalog's, never the projected stock's: one code is counted in one place and the
        main store keeps its own figure beside it (owner document §4), so on a fresh install no stock row
        exists yet and the storekeeper's opening count is exactly what fills them (decision 1).
        """
        return [item.item_id for item in self._catalog.items(shift) if item.kind is ItemKind.COUNTED]

    def weekly(self, shift: ShiftKey, log: list[EventRecord]) -> tuple[WeeklyStatus, tuple[str, ...]]:
        """The week's status and the items counted in its window; one counted item does not close it."""
        now = self._clock.now_utc()
        weekday = self._settings.number(SettingKey.WEEKLY_COUNT_WEEKDAY, shift)
        start, _end = weekly_window(to_tehran(now).date(), due_weekday=weekday)
        last: datetime | None = None
        done: set[str] = set()
        for event in log:
            if event.type != EventType.STOCK_WEEKLY_COUNT or event.payload.get("location_id") != MAIN_STORE:
                continue
            counted_at = parse_iso(event.at_tehran)
            last = counted_at
            if to_tehran(counted_at).date() >= start:
                done.add(str(event.payload["item_id"]))
        items = self.store_item_ids(shift)
        status = weekly_status(
            last,
            now,
            due_weekday=weekday,
            due_time=self._settings.clock_time(SettingKey.WEEKLY_COUNT_TIME, shift),
            complete=set(items) <= done,
        )
        return status, tuple(item for item in items if item in done)

    def baseline_done(self, log: list[EventRecord]) -> bool:
        """Whether the store has an opening count: the storekeeper's record, or stock that already exists."""
        if any(event.type == EventType.STOCK_STORE_BASELINED for event in log):
            return True
        return any(location_id == MAIN_STORE for _item, location_id in self._stock())

    def sample(self, shift: ShiftKey, log: list[EventRecord]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        """This month's key, its five items and the ones already counted."""
        month = month_of(self._clock.now_utc())
        items = monthly_sample(month, self.store_item_ids(shift))
        counted = tuple(
            str(event.payload["item_id"])
            for event in log
            if event.type == EventType.STOCK_SAMPLE_COUNT and event.payload.get("month") == month
        )
        return month, items, counted

    def open_offers(self, log: list[EventRecord], *, returns: bool = False) -> list[EventRecord]:
        """The shelf charges (or, with `returns`, the shelf returns) one side recorded and the other has
        not confirmed yet, oldest first."""
        offered, confirmed = _OFFER_TYPES[returns]
        closed = {
            str(event.payload.get("offer_id"))
            for event in log
            if event.type == confirmed and event.payload.get("offer_id")
        }
        return [event for event in log if event.type == offered and event.event_id not in closed]

    # --- the parcel: the receiver's own count ------------------------------------------------

    def delivery_confirm(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """G3: the person the parcel was handed to counts it blind; their count is what joins the store.

        Reception's count is compared server-side with the receiver's. A difference does not stop the
        goods entering — the receiver's count is the truth of what is on the shelf — it flags the parcel
        so its payment locks and the manager decides, and the answer says only that the counts differ.
        """
        confirmed = body(DeliveryConfirmed, data)
        parcel = self._parcel(confirmed.delivery_id)
        if parcel.returned:
            raise PurchaseRuleError(CONFIRM_RETURNED)
        if parcel.awaiting_staff_id is None:
            raise PurchaseRuleError(NOT_AWAITING)
        if parcel.awaiting_staff_id != principal.staff_id:
            raise PurchaseRuleError(NOT_THE_RECEIVER)
        # Both sides confirm (owner document §4, line «هر دو طرف»): the door's count and the store's
        # count are two people's work, so whoever took the parcel in cannot also be the one who counts
        # it into the store — a person holding both roles included.
        if parcel.actor_id == principal.staff_id:
            raise PurchaseRuleError(CONFIRM_OWN)
        arrived: dict[str, int] = {}
        for line in parcel.lines:
            arrived[line.item_id] = arrived.get(line.item_id, 0) + line.quantity
        counted = {line.item_id: line.quantity for line in confirmed.lines}
        if len(counted) != len(confirmed.lines) or set(counted) != set(arrived):
            raise PurchaseRuleError(CONFIRM_LINES)
        batch = [new_event(self._clock, EventType.DELIVERY_CONFIRMED, confirmed, principal, shift)]
        reason = _mismatch(arrived, counted)
        if reason is not None and parcel.flagged_reason is None:
            flag = DeliveryFlagged(delivery_id=confirmed.delivery_id, reason=reason, note=_MISMATCH_NOTE)
            batch.append(
                new_event(
                    self._clock,
                    EventType.DELIVERY_FLAGGED,
                    flag,
                    principal,
                    shift,
                    ref_event_id=batch[0].client_event_id,
                )
            )
        self._events.append_many(batch)
        return {
            "status": "recorded",
            "event_id": batch[0].client_event_id,
            "delivery_id": confirmed.delivery_id,
            "differs": reason is not None,
        }

    # --- the shelf charge: both sides ---------------------------------------------------------

    def restock_offer(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-10, G3: the giver records the charge and names who confirms it; nothing moves yet."""
        offer = body(RestockOffered, data)
        if offer.confirmer_id == principal.staff_id:
            raise PurchaseRuleError(CONFIRMER_IS_GIVER)
        if offer.from_location_id == offer.to_location_id:
            raise PurchaseRuleError(SAME_LOCATION)
        if offer.from_location_id != MAIN_STORE:
            raise PurchaseRuleError(RESTOCK_FROM_STORE)
        places = {place.location_id for place in self._catalog.locations(shift)}
        if offer.from_location_id not in places or offer.to_location_id not in places:
            raise PurchaseRuleError(UNKNOWN_LOCATION)
        if not any(
            item.item_id == offer.item_id and item.location_id == offer.to_location_id
            for item in self._catalog.items(shift)
        ):
            raise PurchaseRuleError(ITEM_NOT_THERE)
        if not self._is_active_staff(offer.confirmer_id):
            raise PurchaseRuleError(UNKNOWN_CONFIRMER)
        record = new_event(self._clock, EventType.RESTOCK_OFFERED, offer, principal, shift)
        self._events.append(record)
        return {"status": "recorded", "event_id": record.client_event_id}

    def restock_confirm(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """G3: the named person counts what arrived on the shelf; a matching count moves both ends once."""
        return self._confirm(principal, shift, data, returns=False)

    def _confirm(self, principal: Principal, shift: ShiftKey, data: dict[str, Any], *, returns: bool) -> dict[str, Any]:
        """Close one open offer with the named person's own count; the shelf charge and the return share it."""
        offered, confirmed = _OFFER_TYPES[returns]
        offer_id = str(data.get("offer_id") or "")
        log = self._log()
        offer = next((event for event in log if event.event_id == offer_id and event.type == offered), None)
        if offer is None:
            raise PurchaseRuleError(UNKNOWN_OFFER)
        if offer not in self.open_offers(log, returns=returns):
            raise PurchaseRuleError(OFFER_CLOSED)
        if str(offer.payload["confirmer_id"]) != principal.staff_id:
            raise PurchaseRuleError(NOT_THE_CONFIRMER)
        quantity = data.get("quantity")
        if isinstance(quantity, bool) or quantity != offer.payload["quantity"]:
            raise PurchaseRuleError(RETURN_COUNT if returns else OFFER_COUNT)
        model = ShelfReturnConfirmed if returns else RestockConfirmed
        payload = body(model, {key: value for key, value in offer.payload.items()} | {"offer_id": offer_id})
        record = new_event(self._clock, confirmed, payload, principal, shift, ref_event_id=offer.event_id)
        self._events.append(record)
        return {"status": "recorded", "event_id": record.client_event_id}

    # --- the return: a shelf's goods back to the main store (A2) ------------------------------

    def shelf_return_offer(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """A2: the giver records the return and names who counts it in the store; nothing moves yet."""
        offer = body(ShelfReturnOffered, data)
        if offer.confirmer_id == principal.staff_id:
            raise PurchaseRuleError(CONFIRMER_IS_GIVER)
        if offer.from_location_id == offer.to_location_id:
            raise PurchaseRuleError(SAME_LOCATION)
        if offer.to_location_id != MAIN_STORE:
            raise PurchaseRuleError(RETURN_TO_STORE)
        shelf_flow = FLOW_LOCATIONS.get(offer.from_location_id)
        if shelf_flow is None or FLOW_OWNER_ROLES[shelf_flow] not in held_roles(principal.roles):
            raise PurchaseRuleError(RETURN_OWN_SHELF)
        places = {place.location_id for place in self._catalog.locations(shift)}
        if offer.from_location_id not in places or offer.to_location_id not in places:
            raise PurchaseRuleError(UNKNOWN_LOCATION)
        if not any(
            item.item_id == offer.item_id and item.location_id == offer.from_location_id
            for item in self._catalog.items(shift)
        ):
            raise PurchaseRuleError(ITEM_NOT_ON_SHELF)
        if not self._is_active_staff(offer.confirmer_id):
            raise PurchaseRuleError(UNKNOWN_CONFIRMER)
        if not self._holds_store(offer.confirmer_id):
            raise PurchaseRuleError(RETURN_TO_STOREKEEPER)
        record = new_event(self._clock, EventType.SHELF_RETURN_OFFERED, offer, principal, shift)
        self._events.append(record)
        return {"status": "recorded", "event_id": record.client_event_id}

    def shelf_return_confirm(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """A2: the named person counts what arrived in the store; a matching count moves both ends once."""
        return self._confirm(principal, shift, data, returns=True)

    # --- the counts of the store --------------------------------------------------------------

    def store_baseline(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """B1: the storekeeper's opening count of the whole store, once, under their own name."""
        if not _COUNTERS & set(principal.roles):
            raise PurchaseRuleError(BASELINE_ROLE)
        if any(event.type == EventType.STOCK_STORE_BASELINED for event in self._log()):
            raise PurchaseRuleError(BASELINE_DONE)
        baseline = body(StoreBaselined, data)
        if {line.item_id for line in baseline.lines} != set(self.store_item_ids(shift)):
            raise PurchaseRuleError(BASELINE_ITEMS)
        record = new_event(self._clock, EventType.STOCK_STORE_BASELINED, baseline, principal, shift)
        self._events.append(record)
        return {"status": "recorded", "event_id": record.client_event_id}

    def weekly_count(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-11: a blind count of one store item, its unnamed discrepancy and the expiry mark beside it.

        The answers carry event ids and nothing else — the difference is `expected − counted`, the
        handover's own sign (`handover_formulas.difference`), so naming it would tell the counter the
        figure he just counted blind. The week closes only when every item of the store has been counted
        in its window (`weekly`), never on one item.
        """
        if not _COUNTERS & set(principal.roles):
            raise PurchaseRuleError(WEEKLY_ROLE)
        near_expiry = bool(data.pop("near_expiry", False))
        counted = body(WeeklyCount, data)
        if counted.location_id != MAIN_STORE:
            raise PurchaseRuleError(NOT_THE_STORE)
        if counted.item_id not in set(self.store_item_ids(shift)):
            raise PurchaseRuleError(NOT_A_COUNTED_ITEM)
        if not self.baseline_done(self._log()):
            raise PurchaseRuleError(NEEDS_BASELINE)
        expected = self._stock().get((counted.item_id, MAIN_STORE), 0)
        batch = [new_event(self._clock, EventType.STOCK_WEEKLY_COUNT, counted, principal, shift)]
        gap = difference(expected, counted.counted)
        batch += self._gap(counted.item_id, gap, batch[0].client_event_id, principal, shift)
        if near_expiry:
            expiry = ExpiryFlagged(item_id=counted.item_id, location_id=MAIN_STORE, note=None)
            batch.append(
                new_event(
                    self._clock,
                    EventType.EXPIRY_FLAGGED,
                    expiry,
                    principal,
                    shift,
                    ref_event_id=batch[0].client_event_id,
                )
            )
        self._events.append_many(batch)
        return {"status": "recorded", "event_id": batch[0].client_event_id}

    def sample_count(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """B1: the accountant's blind check of one of this month's five items; it never sets the stock."""
        if not SAMPLERS & set(principal.roles):
            raise PurchaseRuleError(SAMPLE_ROLE)
        log = self._log()
        month, items, done = self.sample(shift, log)
        counted = body(SampleCounted, {**data, "month": month})
        if counted.location_id != MAIN_STORE:
            raise PurchaseRuleError(NOT_THE_STORE)
        if counted.item_id not in items:
            raise PurchaseRuleError(NOT_IN_SAMPLE)
        if counted.item_id in done:
            raise PurchaseRuleError(SAMPLE_DONE)
        expected = self._stock().get((counted.item_id, MAIN_STORE), 0)
        batch = [new_event(self._clock, EventType.STOCK_SAMPLE_COUNT, counted, principal, shift)]
        gap = difference(expected, counted.counted)
        batch += self._gap(counted.item_id, gap, batch[0].client_event_id, principal, shift)
        self._events.append_many(batch)
        return {"status": "recorded", "event_id": batch[0].client_event_id}

    # --- the little pieces ----------------------------------------------------------------

    def _gap(self, item_id: str, signed: int, ref: str, principal: Principal, shift: ShiftKey) -> list[Any]:
        """The unnamed discrepancy of a store count, or nothing when the count matched (H-06)."""
        if not signed:
            return []
        gap = DiscrepancyRecorded(
            scope="item", item_id=item_id, location_id=MAIN_STORE, difference=signed, culprit_id=None
        )
        return [new_event(self._clock, EventType.DISCREPANCY_RECORDED, gap, principal, shift, ref_event_id=ref)]

    def _log(self) -> list[EventRecord]:
        with self._database.read() as connection:
            return list(events_repo.iter_events(connection))

    def _stock(self) -> dict[tuple[str, str], int]:
        with self._database.read() as connection:
            rows = projections_repo.stock_rows(connection)
        return {(row["item_id"], row["location_id"]): int(row["qty"]) for row in rows}

    def _parcel(self, delivery_id: str) -> DeliveryState:
        state = delivery_states(self._log()).get(delivery_id)
        if state is None:
            raise PurchaseRuleError("این بار پیدا نشد.")
        return state

    def _holds_store(self, staff_id: str) -> bool:
        """The receiver of a return opens the store: an active storekeeper, or the manager as fallback."""
        with self._database.read() as connection:
            people = staff_repo.list_with_roles(connection)
        return any(
            p.staff.staff_id == staff_id
            and p.staff.active
            and {Role.STOREKEEPER.value, Role.MANAGER.value} & set(p.roles)
            for p in people
        )

    def shelf_return_view(self, principal: Principal, shift: ShiftKey) -> dict[str, Any] | None:
        """A2: what the return form needs: the caller's own shelves' items and the people who can receive.

        None for somebody who answers for no shelf. No quantity travels: the storekeeper counts blind.
        """
        held = held_roles(principal.roles)
        places = {place.location_id: place.name for place in self._catalog.locations(shift)}
        # The shelves a handover counts, of the flows this person's role owns: those are the ones they
        # answer for in a blind count, so those are the ones they hand back from.
        own = {
            location
            for location, flow in FLOW_LOCATIONS.items()
            if location in places and FLOW_OWNER_ROLES[flow] in held
        }
        if not own:
            return None
        items = [
            {
                "item_id": item.item_id,
                "name": item.name,
                "unit": item.unit,
                "location_id": item.location_id,
                "location_name": places.get(item.location_id, ""),
            }
            for item in self._catalog.items(shift)
            if item.kind is ItemKind.COUNTED and item.location_id in own
        ]
        with self._database.read() as connection:
            people = staff_repo.list_with_roles(connection)
        receivers = [
            {"staff_id": p.staff.staff_id, "name": p.staff.full_name}
            for p in people
            if p.staff.active and p.staff.staff_id != principal.staff_id and Role.STOREKEEPER.value in p.roles
        ]
        return {"items": items, "receivers": receivers}

    def _is_active_staff(self, staff_id: str) -> bool:
        with self._database.read() as connection:
            person = staff_repo.find(connection, staff_id)
        return person is not None and person.active


def _mismatch(arrived: Mapping[str, int], counted: Mapping[str, int]) -> str | None:
    """Reception's count against the receiver's: short, extra, or both at once (wrong)."""
    fewer = any(counted[item] < arrived[item] for item in arrived)
    more = any(counted[item] > arrived[item] for item in arrived)
    if fewer and more:
        return "wrong"
    if fewer:
        return "short"
    return "extra" if more else None
