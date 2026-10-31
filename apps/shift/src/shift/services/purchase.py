"""The purchasing chain and the warehouse, as one service (S01 §4, S03 §3–4).

The chain the owner describes: the storekeeper drafts, the manager decides, the purchaser places, the
receptionist receives and counts units without prices, the parcel travels until the storekeeper holds
it, the accountant digitizes the invoice, records the payments, and credits a vendor return. Alongside
it the storekeeper charges a shelf from the main store (one record, both ends) and counts the main
store blind once a week.

Two rules shape this module. First, the log is the truth: every write is an append, and every read is a
fold of what the log holds (`domain.purchase`, `domain.purchase_deliveries`), so a page, the manager's
card and a rebuild cannot disagree. Second, money stays accounting's: the figures the accountant types
are recorded here and no purchasing record touches the cash box or a shelf, except the shelf charge
itself.

The read model is filtered by the caller's own permissions — orders for the storekeeper and the
manager, parcels without prices for reception and the storekeeper, invoices with prices for the
accountant, the warehouse for the storekeeper — and the figure behind a blind count never leaves the
server: `state()` carries no projected quantity at all, and `weekly.count` answers with event ids
only, because the difference would hand the counter the number he is supposed to count (R-02, P-11).
"""

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from shift.adapters.db import events_repo, projections_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, parse_iso, tehran_iso
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.handover_payloads import DiscrepancyRecorded
from shift.domain.payloads import WeeklyCount
from shift.domain.permissions import Permission, Role
from shift.domain.policy import SettingKey, ShiftKey
from shift.domain.purchase import APPROVED, DRAFT, PLACED, SUBMITTED, UNDECIDED, OrderState, order_states
from shift.domain.purchase_deliveries import (
    FLAGGED,
    IN_TRANSIT,
    RECEIVED,
    RECEIVED_STATUSES,
    DeliveryState,
    delivery_states,
)
from shift.domain.purchase_payloads import (
    DeliveryFlagged,
    DeliveryReceived,
    DeliveryTransferred,
    ExpiryFlagged,
    InvoiceDigitized,
    OrderDecided,
    OrderDrafted,
    OrderPlaced,
    PaymentRecorded,
    RestockConfirmed,
    VendorCredit,
    VendorReturn,
)
from shift.domain.stock_checks import WeeklyStatus, weekly_status
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, Item, ItemKind
from shift.services.event_store import EventStore, NewEvent
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService

Model = TypeVar("Model", bound=BaseModel)

# What the signed-in person is told when a rule refuses the action; short, Persian and without a figure.
NO_PERMISSION = "نقش شما اجازهٔ این کار را ندارد."
UNKNOWN_ACTION = "این کار در زنجیرهٔ خرید شناخته نمی‌شود."
BAD_DATA = "دادهٔ این کار کامل یا درست نیست."
UNKNOWN_ORDER = "این سفارش پیدا نشد."
NOT_UNDECIDED = "این سفارش منتظر تصمیم مدیر نیست؛ صفحه را تازه کنید."
NOT_A_DRAFT = "فقط سفارشی که مدیر برای تغییر برگردانده است دوباره پیش‌نویس می‌شود."
NOT_APPROVED = "فقط سفارش تأییدشده به فروشنده داده می‌شود."
ORDER_NOT_PLACED = "این بار به سفارشی وصل است که هنوز به فروشنده داده نشده است."
UNKNOWN_DELIVERY = "این بار پیدا نشد."
CANNOT_FLAG = "برچسب مشکل یک بار و روی باری ثبت می‌شود که پذیرش گرفته است."
CANNOT_TRANSFER = "این بار در وضعیتی نیست که دست‌به‌دست شود."
NOT_A_STOREKEEPER = "تحویل به انبار فقط به کارمند دارای نقش انباردار ثبت می‌شود."
RETURN_STOREKEEPER = "مرجوعی به فروشنده را فقط انباردار ثبت می‌کند."
CANNOT_DIGITIZE = "فاکتور یک بار و برای باری ثبت می‌شود که پذیرش گرفته است."
NO_INVOICE = "اول فاکتور این بار را دیجیتال کنید."
PAYMENT_LOCKED = "پرداخت این فاکتور تا تصمیم مدیر دربارهٔ بار مشکل‌دار قفل است."
OVER_PAYMENT = "مبلغ پرداخت از ماندهٔ فاکتور بیشتر است."
CANNOT_RETURN = "مرجوعی یک بار و برای باری ثبت می‌شود که پذیرش گرفته است."
NO_RETURN = "بستانکاری فقط بعد از ثبت مرجوعی همین بار ثبت می‌شود."
OVER_CREDIT = "مبلغ بستانکاری از سقف فاکتور بیشتر است."
SAME_LOCATION = "مبدأ و مقصد شارژ قفسه نمی‌توانند یکی باشند."
UNKNOWN_LOCATION = "محل مبدأ یا مقصد در کاتالوگ نیست."
ITEM_NOT_THERE = "این قلم در قفسهٔ مقصد در کاتالوگ نیست."
NOT_A_COUNTED_ITEM = "این قلم در این محل قلم شمارشی نیست."
NOT_THE_STORE = "شمارش هفتگی فقط برای انبار اصلی ثبت می‌شود."
RESTOCK_FROM_STORE = "شارژ قفسه فقط از انبار اصلی ثبت می‌شود."
UNKNOWN_CONFIRMER = "تأییدکننده باید از کارکنان فعال درمانگاه باشد."
PAYMENT_RETURNED = "بار این فاکتور به فروشنده مرجوع شده است؛ پرداخت روی آن ثبت نمی‌شود."

# The clinic counts the main store blind once a week (S01 §4, P-11). No catalog field or setting names
# the store's location yet, so its id is pinned here: a shelf count must never mark the week done, and the
# warehouse section lists what the store holds rather than every counted item of the clinic.
MAIN_STORE = "main_store"

# Every action, the permission it needs and the handler that writes it; nothing else may be appended.
ACTIONS: Mapping[str, tuple[Permission, str]] = {
    "order.draft": (Permission.PURCHASE_ORDER_DRAFT, "_order_draft"),
    "order.decide": (Permission.PURCHASE_ORDER_DECIDE, "_order_decide"),
    "order.place": (Permission.PURCHASE_ORDER_PLACE, "_order_place"),
    "delivery.receive": (Permission.PURCHASE_DELIVERY_RECEIVE, "_delivery_receive"),
    "delivery.flag": (Permission.PURCHASE_DELIVERY_RECEIVE, "_delivery_flag"),
    "delivery.transfer": (Permission.PARCEL_HANDLE, "_delivery_transfer"),
    "invoice.digitize": (Permission.PURCHASE_PAYMENT_RECORD, "_invoice_digitize"),
    "payment.record": (Permission.PURCHASE_PAYMENT_RECORD, "_payment_record"),
    # P-09: the storekeeper records the return and holds the same receipt permission the delivery did.
    "return.record": (Permission.PURCHASE_DELIVERY_RECEIVE, "_return_record"),
    "credit.record": (Permission.PURCHASE_PAYMENT_RECORD, "_credit_record"),
    "restock.record": (Permission.STOCK_RESTOCK_CONFIRM, "_restock_record"),
    "weekly.count": (Permission.STOCK_COUNT_SUBMIT, "_weekly_count"),
}
# The purchase page draws a button per permission here and nothing else (piece B reads this list).
PURCHASE_PERMISSIONS: tuple[Permission, ...] = (
    Permission.PURCHASE_ORDER_DRAFT,
    Permission.PURCHASE_ORDER_DECIDE,
    Permission.PURCHASE_ORDER_PLACE,
    Permission.PURCHASE_DELIVERY_RECEIVE,
    Permission.PURCHASE_PAYMENT_RECORD,
    Permission.PARCEL_HANDLE,
    Permission.STOCK_RESTOCK_CONFIRM,
    Permission.STOCK_COUNT_SUBMIT,
)
RESTOCK_HISTORY = 20
# A received parcel may be digitized whatever its flag says: the owner's rule locks the PAYMENT of a
# flagged delivery until the manager decides (P-06), never the accountant's price entry. `RECEIVED_STATUSES`
# already leaves out a parcel that went back to the vendor or is already paid.
_RECEIVABLE = RECEIVED_STATUSES

# One writer at a time: a rule that reads the folded log and then appends must not interleave with a
# second request doing the same, or two payments could each pass the invoice-balance check (P-08).
_WRITE_LOCK = threading.Lock()


class PurchaseRuleError(ValueError):
    """A purchase action the clinic's rules refuse; the route answers 422 with this message."""


@dataclass(frozen=True, slots=True)
class BelowMinimum:
    """One counted item under its catalog minimum; the figure itself stays on the server (P-01)."""

    item_id: str
    name: str
    location_id: str


@dataclass(frozen=True, slots=True)
class WarehouseItem:
    """One counted item of the store's view, without its quantity: the count is blind (P-11)."""

    item_id: str
    name: str
    location_id: str
    unit: str
    minimum: int


@dataclass(frozen=True, slots=True)
class RestockRecord:
    """One shelf charge that already happened, newest first: what moved, from where and by whom."""

    item_id: str
    from_location_id: str
    to_location_id: str
    quantity: int
    actor_id: str
    at_tehran: str


@dataclass(frozen=True, slots=True)
class Warehouse:
    """The warehouse section: what may be charged or counted, and this week's count status."""

    items: tuple[WarehouseItem, ...]
    locations: tuple[tuple[str, str], ...]
    weekly: WeeklyStatus
    restock: tuple[RestockRecord, ...]


@dataclass(frozen=True, slots=True)
class PurchaseState:
    """The purchase page's read model; a `None` section is one this caller may not see at all."""

    orders: tuple[OrderState, ...] | None
    below_minimum: tuple[BelowMinimum, ...] | None
    deliveries: tuple[DeliveryState, ...] | None
    invoices: tuple[DeliveryState, ...] | None
    warehouse: Warehouse | None
    permissions: tuple[str, ...]
    # P-09: only the storekeeper records a vendor return, so the page may draw its form for them alone.
    is_storekeeper: bool = False

    def to_payload(self) -> dict[str, Any]:
        """The JSON the route answers with; prices appear in the invoices section only."""
        return {
            "orders": None if self.orders is None else [_order_payload(order) for order in self.orders],
            "below_minimum": (
                None
                if self.below_minimum is None
                else [
                    {"item_id": item.item_id, "name": item.name, "location_id": item.location_id}
                    for item in self.below_minimum
                ]
            ),
            "deliveries": (
                None if self.deliveries is None else [_delivery_payload(delivery) for delivery in self.deliveries]
            ),
            "invoices": (None if self.invoices is None else [_invoice_payload(delivery) for delivery in self.invoices]),
            "warehouse": None if self.warehouse is None else _warehouse_payload(self.warehouse),
            "permissions": list(self.permissions),
            "is_storekeeper": self.is_storekeeper,
        }


@dataclass(frozen=True, slots=True)
class PendingPurchase:
    """The open purchase items the manager's card reports; every field is a plain fold of the log."""

    orders: tuple[OrderState, ...]
    locked_deliveries: tuple[DeliveryState, ...]
    undelivered: tuple[DeliveryState, ...]
    below_minimum: tuple[BelowMinimum, ...]
    weekly: WeeklyStatus


class PurchaseService:
    """Writes the purchasing chain and reads its state, one validated action at a time."""

    def __init__(
        self,
        database: Database,
        events: EventStore,
        projections: ProjectionEngine,
        catalog: CatalogService,
        settings: SettingsService,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._database = database
        self._events = events
        self._projections = projections
        self._catalog = catalog
        self._settings = settings
        self._clock = clock or SystemClock()

    # --- reading ---------------------------------------------------------------------------

    def state(self, principal: Principal, shift: ShiftKey) -> PurchaseState:
        """The page's read model, cut down to the sections this caller's permissions open.

        The whole log is folded, because a parcel or an order outlives the shift that opened it (P-12)
        and no purchasing record carries another shift's cash figure (R-01). No section carries a
        projected quantity: the storekeeper's own weekly count has to stay blind (R-02, P-11).
        """
        log = self._log()
        orders = order_states(log)
        parcels = delivery_states(log)
        held = principal.permissions
        items = self._counted_items(shift)
        below = self._below_minimum(items)
        can_order = Permission.PURCHASE_ORDER_DRAFT in held or Permission.PURCHASE_ORDER_DECIDE in held
        can_parcel = Permission.PURCHASE_DELIVERY_RECEIVE in held or Permission.PARCEL_HANDLE in held
        return PurchaseState(
            orders=tuple(orders.values()) if can_order else None,
            below_minimum=below if can_order else None,
            deliveries=tuple(parcels.values()) if can_parcel else None,
            invoices=tuple(parcels.values()) if Permission.PURCHASE_PAYMENT_RECORD in held else None,
            warehouse=(self._warehouse(items, shift, log) if Permission.STOCK_RESTOCK_CONFIRM in held else None),
            permissions=tuple(sorted(item.value for item in PURCHASE_PERMISSIONS if item in held)),
            is_storekeeper=Role.STOREKEEPER.value in principal.roles,
        )

    def pending(self, shift: ShiftKey) -> PendingPurchase:
        """The manager's card: the orders waiting for a decision, the locked payments and the parcels.

        The card carries the item itself and its time, like every other open item of the clinic: no
        figure of a shelf and no price of an invoice (§6 M-07, M-09).
        """
        log = self._log()
        parcels = delivery_states(log)
        return PendingPurchase(
            orders=tuple(order for order in order_states(log).values() if order.status in UNDECIDED),
            locked_deliveries=tuple(parcel for parcel in parcels.values() if parcel.payment_locked),
            undelivered=tuple(
                parcel for parcel in parcels.values() if parcel.custody != "storekeeper" and not parcel.returned
            ),
            below_minimum=self._below_minimum(self._counted_items(shift)),
            weekly=self._weekly(shift, log),
        )

    def in_transit(self, shift: ShiftKey) -> tuple[DeliveryState, ...]:
        """The received parcels the storekeeper has not taken yet: the handover's real parcel list (P-12).

        The handover screen hands one of these to the next shift, so it needs the parcel's own id and
        the invoice number the reception wrote, and nothing that belongs to the accountant.
        """
        return tuple(
            parcel
            for parcel in delivery_states(self._log()).values()
            if not parcel.returned and parcel.custody != "storekeeper" and parcel.status != "paid"
        )

    # --- writing ---------------------------------------------------------------------------

    def perform(self, principal: Principal, shift: ShiftKey, action: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Run one action of the chain: its permission, its rule, one append, then the projection.

        The permission is checked here and not on the route, exactly like the phone's event endpoint:
        the route only asks for a session that may append at all, and a refusal writes nothing.

        Every action runs under one process-wide lock. A rule such as «payments never pass the invoice
        balance» reads the folded log and then appends; two requests interleaving between those two
        steps would each pass the check and the log would hold both payments. The clinic's server is one
        process, so one lock makes the pair atomic without inventing a second source of truth.
        """
        entry = ACTIONS.get(action)
        if entry is None:
            raise PurchaseRuleError(UNKNOWN_ACTION)
        permission, handler = entry
        if permission not in principal.permissions:
            raise PurchaseRuleError(NO_PERMISSION)
        with _WRITE_LOCK:
            result: dict[str, Any] = getattr(self, handler)(principal, shift, dict(data))
            self._projections.sync()
        return result

    def _order_draft(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-02: a new draft, or the fresh one a «تغییر» decision asked for under the same order id."""
        order_id = data.pop("order_id", None)
        if order_id is None:
            order_id = self._mint()
        elif self._require_order(order_id).status != DRAFT:
            raise PurchaseRuleError(NOT_A_DRAFT)
        draft = self._body(OrderDrafted, {**data, "order_id": order_id})
        event_id = self._append(EventType.ORDER_DRAFTED, draft, principal, shift)
        return {"status": "recorded", "event_id": event_id, "order_id": draft.order_id}

    def _order_decide(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-02: one decision, on a submitted order, by the manager whose permission this action needs."""
        decided = self._body(OrderDecided, data)
        if self._require_order(decided.order_id).status != SUBMITTED:
            raise PurchaseRuleError(NOT_UNDECIDED)
        event_id = self._append(EventType.ORDER_DECIDED, decided, principal, shift)
        return {"status": "recorded", "event_id": event_id, "order_id": decided.order_id}

    def _order_place(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-03: only an approved order goes to a vendor."""
        placed = self._body(OrderPlaced, data)
        if self._require_order(placed.order_id).status != APPROVED:
            raise PurchaseRuleError(NOT_APPROVED)
        event_id = self._append(EventType.ORDER_PLACED, placed, principal, shift)
        return {"status": "recorded", "event_id": event_id, "order_id": placed.order_id}

    def _delivery_receive(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-04: what reception counted at the door, against an invoice number and with no prices."""
        data["delivery_id"] = self._mint()
        received = self._body(DeliveryReceived, data)
        if received.order_id is not None and self._require_order(received.order_id).status != PLACED:
            raise PurchaseRuleError(ORDER_NOT_PLACED)
        event_id = self._append(EventType.DELIVERY_RECEIVED, received, principal, shift)
        return {"status": "recorded", "event_id": event_id, "delivery_id": received.delivery_id}

    def _delivery_flag(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-06: one label per parcel, and it locks the invoice until the manager decides.

        A problem can be found after the accountant already typed the invoice, so the label is not tied
        to the receipt's own moment: the delivery has to exist, carry no label yet and not be on its way
        back. Locking a payment that has not been made is the whole point, and a late label must still
        be able to stop it.
        """
        flagged = self._body(DeliveryFlagged, data)
        parcel = self._require_delivery(flagged.delivery_id)
        if parcel.flagged_reason is not None or parcel.returned:
            raise PurchaseRuleError(CANNOT_FLAG)
        event_id = self._append(EventType.DELIVERY_FLAGGED, flagged, principal, shift)
        return {"status": "recorded", "event_id": event_id, "delivery_id": flagged.delivery_id}

    def _delivery_transfer(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-12: the real delivery id travels between shifts until the storekeeper takes custody."""
        transferred = self._body(DeliveryTransferred, data)
        parcel = self._require_delivery(transferred.delivery_id)
        if parcel.status not in {RECEIVED, FLAGGED, IN_TRANSIT}:
            raise PurchaseRuleError(CANNOT_TRANSFER)
        if transferred.to_kind == "storekeeper" and not self._is_storekeeper(transferred.to_staff_id):
            raise PurchaseRuleError(NOT_A_STOREKEEPER)
        event_id = self._append(EventType.DELIVERY_TRANSFERRED, transferred, principal, shift)
        return {"status": "recorded", "event_id": event_id, "delivery_id": transferred.delivery_id}

    def _invoice_digitize(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-07: the accountant's lines; the payload has no total, so the server's sum is the only one."""
        digitized = self._body(InvoiceDigitized, data)
        parcel = self._require_delivery(digitized.delivery_id)
        if parcel.status not in _RECEIVABLE or parcel.invoice_lines:
            raise PurchaseRuleError(CANNOT_DIGITIZE)
        event_id = self._append(EventType.INVOICE_DIGITIZED, digitized, principal, shift)
        return {"status": "recorded", "event_id": event_id, "delivery_id": digitized.delivery_id}

    def _payment_record(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-08: an invoice first; no payment on a returned parcel or while a flag is undecided.

        A parcel that went back to the vendor has nothing left to pay for — the credit note is the
        accountant's record of it — so the payment door closes there as well as behind a locked flag.
        """
        payment = self._body(PaymentRecorded, data)
        parcel = self._require_delivery(payment.delivery_id)
        if not parcel.invoice_lines:
            raise PurchaseRuleError(NO_INVOICE)
        if parcel.returned:
            raise PurchaseRuleError(PAYMENT_RETURNED)
        if parcel.payment_locked:
            raise PurchaseRuleError(PAYMENT_LOCKED)
        if payment.amount_toman > parcel.invoice_total - parcel.paid_total:
            raise PurchaseRuleError(OVER_PAYMENT)
        event_id = self._append(EventType.PAYMENT_RECORDED, payment, principal, shift)
        return {"status": "recorded", "event_id": event_id, "delivery_id": payment.delivery_id}

    def _return_record(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-09: one return per delivery, recorded by the storekeeper who lets the goods out."""
        if not self._is_storekeeper(principal.staff_id):
            raise PurchaseRuleError(RETURN_STOREKEEPER)
        returned = self._body(VendorReturn, data)
        if self._require_delivery(returned.delivery_id).returned:
            raise PurchaseRuleError(CANNOT_RETURN)
        event_id = self._append(EventType.VENDOR_RETURN, returned, principal, shift)
        return {"status": "recorded", "event_id": event_id, "delivery_id": returned.delivery_id}

    def _credit_record(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-09: a credit needs a return, an invoice, and stays under the invoice minus earlier credits."""
        credit = self._body(VendorCredit, data)
        parcel = self._require_delivery(credit.delivery_id)
        if not parcel.returned:
            raise PurchaseRuleError(NO_RETURN)
        if credit.amount_toman > parcel.invoice_total - parcel.credit_total:
            raise PurchaseRuleError(OVER_CREDIT)
        event_id = self._append(EventType.VENDOR_CREDIT, credit, principal, shift)
        return {"status": "recorded", "event_id": event_id, "delivery_id": credit.delivery_id}

    def _restock_record(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-10: the store down and the shelf up in one record, confirmed by the person who counted it.

        The shift lead who counted the units on the shelf is the confirmer, so the page may name them;
        with no name it is the caller, who is the one counting at this moment. Either way the name must
        be an active staff member, because the record says who confirmed the count.
        """
        if not data.get("confirmer_id"):
            data["confirmer_id"] = principal.staff_id
        restock = self._body(RestockConfirmed, data)
        if restock.from_location_id == restock.to_location_id:
            raise PurchaseRuleError(SAME_LOCATION)
        if restock.from_location_id != MAIN_STORE:
            raise PurchaseRuleError(RESTOCK_FROM_STORE)
        places = {place.location_id for place in self._catalog.locations(shift)}
        if restock.from_location_id not in places or restock.to_location_id not in places:
            raise PurchaseRuleError(UNKNOWN_LOCATION)
        if not any(
            item.item_id == restock.item_id and item.location_id == restock.to_location_id
            for item in self._catalog.items(shift)
        ):
            raise PurchaseRuleError(ITEM_NOT_THERE)
        if not self._is_active_staff(restock.confirmer_id):
            raise PurchaseRuleError(UNKNOWN_CONFIRMER)
        event_id = self._append(EventType.RESTOCK_CONFIRMED, restock, principal, shift)
        return {"status": "recorded", "event_id": event_id}

    def _weekly_count(self, principal: Principal, shift: ShiftKey, data: dict[str, Any]) -> dict[str, Any]:
        """P-11: a blind count of one store item, its unnamed discrepancy and the expiry flag beside it.

        Only the main store is counted this way: the weekly status the manager's card reads is about the
        store, so a shelf count must never be able to mark the week done. The answers carry event ids and
        nothing else — the difference is `counted − expected`, so naming it would tell the counter the
        figure he just counted blind.
        """
        near_expiry = bool(data.pop("near_expiry", False))
        counted = self._body(WeeklyCount, data)
        location = counted.location_id
        if location != MAIN_STORE:
            raise PurchaseRuleError(NOT_THE_STORE)
        # The same set the warehouse section lists, so the page never offers an item the rule refuses:
        # a counted item that lives in the store, or one a shelf charge left stock at the store for.
        if counted.item_id not in {item.item_id for item in self._store_items(self._counted_items(shift))}:
            raise PurchaseRuleError(NOT_A_COUNTED_ITEM)
        expected = self._stock().get((counted.item_id, location), 0)
        batch = [self._new_event(EventType.STOCK_WEEKLY_COUNT, counted, principal, shift)]
        difference = counted.counted - expected
        if difference:
            gap = DiscrepancyRecorded(
                scope="item",
                item_id=counted.item_id,
                location_id=location,
                difference=difference,
                culprit_id=None,
            )
            batch.append(
                self._new_event(
                    EventType.DISCREPANCY_RECORDED, gap, principal, shift, ref_event_id=batch[0].client_event_id
                )
            )
        if near_expiry:
            expiry = ExpiryFlagged(item_id=counted.item_id, location_id=location, note=None)
            batch.append(
                self._new_event(
                    EventType.EXPIRY_FLAGGED, expiry, principal, shift, ref_event_id=batch[0].client_event_id
                )
            )
        self._events.append_many(batch)
        return {"status": "recorded", "event_id": batch[0].client_event_id}

    # --- the little pieces ----------------------------------------------------------------

    def _append(
        self,
        event_type: EventType,
        payload: BaseModel,
        principal: Principal,
        shift: ShiftKey,
        *,
        ref_event_id: str | None = None,
    ) -> str:
        """Append one record with the session's identity and the server's own time (E-14)."""
        return self._events.append(
            self._new_event(event_type, payload, principal, shift, ref_event_id=ref_event_id)
        ).event.event_id

    def _new_event(
        self,
        event_type: EventType,
        payload: BaseModel,
        principal: Principal,
        shift: ShiftKey,
        *,
        ref_event_id: str | None = None,
    ) -> NewEvent:
        moment = self._clock.now_utc()
        return NewEvent(
            client_event_id=new_ulid(moment),
            type=event_type.value,
            payload=payload.model_dump(mode="json"),
            actor_id=principal.staff_id,
            device_id=principal.device_id,
            work_date=shift.work_date,
            client_at=moment,
            shift_id=shift.text,
            ref_event_id=ref_event_id,
        )

    def _mint(self) -> str:
        return new_ulid(self._clock.now_utc())

    @staticmethod
    def _body(model: type[Model], data: Mapping[str, Any]) -> Model:
        try:
            return model.model_validate(dict(data))
        except ValidationError as exc:
            raise PurchaseRuleError(BAD_DATA) from exc

    def _log(self) -> list[EventRecord]:
        with self._database.read() as connection:
            return list(events_repo.iter_events(connection))

    def _stock(self) -> dict[tuple[str, str], int]:
        with self._database.read() as connection:
            rows = projections_repo.stock_rows(connection)
        return {(row["item_id"], row["location_id"]): int(row["qty"]) for row in rows}

    def _require_order(self, order_id: str) -> OrderState:
        state = order_states(self._log()).get(order_id)
        if state is None:
            raise PurchaseRuleError(UNKNOWN_ORDER)
        return state

    def _require_delivery(self, delivery_id: str) -> DeliveryState:
        state = delivery_states(self._log()).get(delivery_id)
        if state is None:
            raise PurchaseRuleError(UNKNOWN_DELIVERY)
        return state

    def _is_storekeeper(self, staff_id: str) -> bool:
        """The recipient of a custody hand-over must be an active holder of the storekeeper role."""
        with self._database.read() as connection:
            person = staff_repo.find(connection, staff_id)
            roles = staff_repo.roles_for(connection, staff_id) if person is not None else ()
        return person is not None and person.active and Role.STOREKEEPER.value in roles

    def _is_active_staff(self, staff_id: str) -> bool:
        """A record that names a person (the shelf charge's confirmer) names someone the clinic still has."""
        with self._database.read() as connection:
            person = staff_repo.find(connection, staff_id)
        return person is not None and person.active

    def _counted_items(self, shift: ShiftKey) -> list[Item]:
        return [item for item in self._catalog.items(shift) if item.kind is ItemKind.COUNTED]

    def _below_minimum(self, items: list[Item]) -> tuple[BelowMinimum, ...]:
        """P-01: the suggestion list, with a missing projection row read as nothing on the shelf."""
        stock = self._stock()
        return tuple(
            BelowMinimum(item.item_id, item.name, item.location_id)
            for item in items
            if stock.get((item.item_id, item.location_id), 0) < item.minimum
        )

    def _weekly(self, shift: ShiftKey, log: list[EventRecord]) -> WeeklyStatus:
        last: datetime | None = None
        for event in log:
            if event.type == EventType.STOCK_WEEKLY_COUNT:
                last = parse_iso(event.at_tehran)
        return weekly_status(
            last,
            self._clock.now_utc(),
            due_weekday=self._settings.number(SettingKey.WEEKLY_COUNT_WEEKDAY, shift),
            due_time=self._settings.clock_time(SettingKey.WEEKLY_COUNT_TIME, shift),
        )

    def _warehouse(self, items: list[Item], shift: ShiftKey, log: list[EventRecord]) -> Warehouse:
        """The store's own view: what lives in the main store or has stock there, and the week's count.

        A counted item whose catalog home is the store is always listed; an item that a shelf charge
        left a store row behind for is listed too, because that is where the storekeeper has to count
        it and charge it from again. No quantity is published (`_warehouse_payload`): the count is blind.
        """
        restock = [
            RestockRecord(
                item_id=str(event.payload["item_id"]),
                from_location_id=str(event.payload["from_location_id"]),
                to_location_id=str(event.payload["to_location_id"]),
                quantity=int(event.payload["quantity"]),
                actor_id=event.actor_id,
                at_tehran=event.at_tehran,
            )
            for event in log
            if event.type == EventType.RESTOCK_CONFIRMED
        ]
        return Warehouse(
            items=self._store_items(items),
            locations=tuple((place.location_id, place.name) for place in self._catalog.locations(shift)),
            weekly=self._weekly(shift, log),
            restock=tuple(reversed(restock[-RESTOCK_HISTORY:])),
        )

    def _store_items(self, items: list[Item]) -> tuple[WarehouseItem, ...]:
        """Every counted item the store holds: its catalog home is there, or a charge left stock there."""
        catalog = {item.item_id: item for item in items}
        home = [item.item_id for item in items if item.location_id == MAIN_STORE]
        stocked = sorted(
            item_id
            for item_id, location_id in self._stock()
            if location_id == MAIN_STORE and item_id in catalog and item_id not in home
        )
        return tuple(
            WarehouseItem(
                catalog[item_id].item_id,
                catalog[item_id].name,
                MAIN_STORE,
                catalog[item_id].unit,
                catalog[item_id].minimum,
            )
            for item_id in home + stocked
        )


def _order_payload(order: OrderState) -> dict[str, Any]:
    """An order as its own people read it: the lines, the status and who spoke, never a price."""
    return {
        "order_id": order.order_id,
        "status": order.status,
        "lines": [
            {"item_id": line.item_id, "location_id": line.location_id, "quantity": line.quantity}
            for line in order.lines
        ],
        "note": order.note,
        "actor_id": order.actor_id,
        "at_tehran": order.at_tehran,
        "decided_by": order.decided_by,
        "vendor": order.placed_vendor,
        "reference": order.placed_reference,
    }


def _delivery_payload(parcel: DeliveryState) -> dict[str, Any]:
    """A parcel as reception and the storekeeper read it: units and custody, never a price."""
    return {
        "delivery_id": parcel.delivery_id,
        "order_id": parcel.order_id,
        "invoice_reference": parcel.invoice_reference,
        "status": parcel.status,
        "flagged_reason": parcel.flagged_reason,
        "custody": parcel.custody,
        "returned": parcel.returned,
        "packages": parcel.packages,
        "lines": [
            {"item_id": line.item_id, "location_id": line.location_id, "quantity": line.quantity}
            for line in parcel.lines
        ],
        "actor_id": parcel.actor_id,
        "at_tehran": parcel.at_tehran,
    }


def _invoice_payload(parcel: DeliveryState) -> dict[str, Any]:
    """An invoice as the accountant reads it: unit prices, the server's total, what is still owed."""
    return {
        "delivery_id": parcel.delivery_id,
        "invoice_reference": parcel.invoice_reference,
        "status": parcel.status,
        "payment_locked": parcel.payment_locked,
        "returned": parcel.returned,
        "invoice_total": parcel.invoice_total,
        "paid_total": parcel.paid_total,
        "credit_total": parcel.credit_total,
        "remaining": max(parcel.invoice_total - parcel.paid_total, 0),
        "lines": [
            {
                "item_id": line.item_id,
                "quantity": line.quantity,
                "unit_price_toman": line.unit_price_toman,
                "total": line.quantity * line.unit_price_toman,
            }
            for line in parcel.invoice_lines
        ],
    }


def _warehouse_payload(warehouse: Warehouse) -> dict[str, Any]:
    """The warehouse section, and not one projected quantity: this is the counter's own page."""
    weekly = warehouse.weekly
    return {
        "items": [
            {
                "item_id": item.item_id,
                "name": item.name,
                "location_id": item.location_id,
                "unit": item.unit,
                "minimum": item.minimum,
            }
            for item in warehouse.items
        ],
        "locations": [{"id": location_id, "name": name} for location_id, name in warehouse.locations],
        "weekly": {
            "state": weekly.state,
            "window_start": weekly.window_start.isoformat(),
            "window_end": weekly.window_end.isoformat(),
            "last_count_at": None if weekly.last_count_at is None else tehran_iso(weekly.last_count_at),
        },
        "restock": [
            {
                "item_id": record.item_id,
                "from_location_id": record.from_location_id,
                "to_location_id": record.to_location_id,
                "quantity": record.quantity,
                "actor_id": record.actor_id,
                "at_tehran": record.at_tehran,
            }
            for record in warehouse.restock
        ],
    }
