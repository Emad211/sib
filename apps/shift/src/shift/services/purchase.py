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
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from shift.adapters.db import events_repo, projections_repo, staff_repo
from shift.adapters.db.core import Database
from shift.adapters.db.events_repo import EventRecord
from shift.common.clock import Clock, SystemClock, tehran_iso, to_tehran
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.permissions import Permission, Role
from shift.domain.policy import ShiftKey
from shift.domain.price_history import PriceSeries, price_history
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
    InvoiceDigitized,
    OrderDecided,
    OrderDrafted,
    OrderPlaced,
    PaymentRecorded,
    VendorCredit,
    VendorReturn,
)
from shift.domain.shift_clock import covering
from shift.domain.stock_checks import WeeklyStatus
from shift.services import store_actions
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, Item, ItemKind
from shift.services.event_store import EventStore, NewEvent
from shift.services.photos import PhotoStore, is_photo_hash
from shift.services.projections import ProjectionEngine
from shift.services.purchase_rules import (
    BAD_DATA,
    MAIN_STORE,
    NO_PHOTO,
    PHOTO_NOT_STORED,
    PurchaseRuleError,
)
from shift.services.settings import SettingsService

Model = TypeVar("Model", bound=BaseModel)

# What the signed-in person is told when a rule refuses the action; short, Persian and without a figure.
NO_PERMISSION = "نقش شما اجازهٔ این کار را ندارد."
UNKNOWN_ACTION = "این کار در زنجیرهٔ خرید شناخته نمی‌شود."
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
PAYMENT_RETURNED = "بار این فاکتور به فروشنده مرجوع شده است؛ پرداخت روی آن ثبت نمی‌شود."

STORE_PREFIX = "store:"
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
    # The main store's actions live in `store_actions` (opening count, weekly full count, monthly sample,
    # the two-sided parcel and shelf charge); the prefix routes them to its handlers (B1, G3).
    **{name: (permission, STORE_PREFIX + handler) for name, (permission, handler) in store_actions.ACTIONS.items()},
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
    # B1: the items already counted in this week's window (the week closes only when all are), and whether
    # the storekeeper's opening count exists yet.
    counted_ids: tuple[str, ...] = ()
    baseline_done: bool = True


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
    # F2: every item's price per vendor, for the people who already see invoices (the accountant).
    price_history: tuple[PriceSeries, ...] | None = None
    # B1: this month's five sampled items for the accountant, and what is already counted.
    sample: dict[str, Any] | None = None
    # G3: what waits for this very person — shelf charges to confirm and parcels to count (no figures).
    inbox: dict[str, Any] | None = None
    # G3: who the giver may name to confirm a shelf charge, by name and role; never an id for staff.
    directory: tuple[dict[str, Any], ...] = ()
    viewer_id: str = ""

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
                None
                if self.deliveries is None
                else [_delivery_payload(delivery, self.viewer_id) for delivery in self.deliveries]
            ),
            "invoices": (None if self.invoices is None else [_invoice_payload(delivery) for delivery in self.invoices]),
            "warehouse": None if self.warehouse is None else _warehouse_payload(self.warehouse),
            "permissions": list(self.permissions),
            "is_storekeeper": self.is_storekeeper,
            "price_history": None if self.price_history is None else [_series_payload(s) for s in self.price_history],
            "sample": self.sample,
            "inbox": self.inbox,
            "directory": list(self.directory),
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
        photos: PhotoStore | None = None,
    ) -> None:
        self._database = database
        self._events = events
        self._projections = projections
        self._catalog = catalog
        self._settings = settings
        self._clock = clock or SystemClock()
        # The photo folder, when the app has one: a receipt that names a photo the store lacks is refused (F1).
        self._photos = photos
        self._store = store_actions.StoreActions(database, events, catalog, settings, self._clock)

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
            price_history=price_history(log) if Permission.PURCHASE_PAYMENT_RECORD in held else None,
            sample=self._sample_view(principal, shift, log),
            inbox=self._inbox(principal, shift, log),
            directory=self._directory(principal),
            viewer_id=principal.staff_id,
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
            weekly=self._store.weekly(shift, log)[0],
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
            owner = self._store if handler.startswith(STORE_PREFIX) else self
            result: dict[str, Any] = getattr(owner, handler.removeprefix(STORE_PREFIX))(principal, shift, dict(data))
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
        self._need_photo(data.get("invoice_photo_sha256"), required=True)
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
        self._need_photo(data.get("receipt_photo_sha256"), required=False)
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

    def _need_photo(self, value: object, *, required: bool) -> None:
        """F1: the invoice photo is mandatory at the door; a payment receipt's photo is optional (decision 6).

        A hash is only ever a 64-character digest, never a path; when the app has a photo folder the file
        has to be in it, so a record can never name a photo the accountant could not open.
        """
        if value in (None, ""):
            if required:
                raise PurchaseRuleError(NO_PHOTO)
            return
        if not is_photo_hash(value) or (self._photos is not None and not self._photos.exists(str(value))):
            raise PurchaseRuleError(PHOTO_NOT_STORED)

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
            shift_key = covering(to_tehran(self._clock.now_utc())).text
            roles = staff_repo.roles_for(connection, staff_id, shift_key) if person is not None else ()
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
        weekly, counted_ids = self._store.weekly(shift, log)
        return Warehouse(
            items=self._store_items(items),
            locations=tuple((place.location_id, place.name) for place in self._catalog.locations(shift)),
            weekly=weekly,
            restock=tuple(reversed(restock[-RESTOCK_HISTORY:])),
            counted_ids=counted_ids,
            baseline_done=self._store.baseline_done(log),
        )

    def _store_items(self, items: list[Item]) -> tuple[WarehouseItem, ...]:
        """Every counted item the store holds: its catalog home is there, or a charge left stock there."""
        catalog = {item.item_id: item for item in items}
        ids = self._store.store_item_ids_of(items)
        return tuple(
            WarehouseItem(
                catalog[item_id].item_id,
                catalog[item_id].name,
                MAIN_STORE,
                catalog[item_id].unit,
                catalog[item_id].minimum,
            )
            for item_id in ids
        )

    def _sample_view(self, principal: Principal, shift: ShiftKey, log: list[EventRecord]) -> dict[str, Any] | None:
        """B1: the accountant's five items of the month, blind: names and units, never a quantity."""
        if not store_actions.SAMPLERS & set(principal.roles):
            return None
        month, picked, done = self._store.sample(shift, log)
        names = {item.item_id: item for item in self._counted_items(shift)}
        return {
            "month": month,
            "items": [
                {"item_id": item_id, "name": names[item_id].name, "unit": names[item_id].unit, "done": item_id in done}
                for item_id in picked
                if item_id in names
            ],
        }

    def _directory(self, principal: Principal) -> tuple[dict[str, Any], ...]:
        """G3: the people the giver may name to confirm a shelf charge, by name and role.

        Only somebody who may record a charge needs the list. The giver's own name is left out: a charge
        confirmed by the person who recorded it is not a two-sided record (owner decision, G3).
        """
        if Permission.STOCK_RESTOCK_CONFIRM not in principal.permissions:
            return ()
        with self._database.read() as connection:
            people = staff_repo.list_with_roles(connection)
        return tuple(
            {
                "staff_id": person.staff.staff_id,
                "name": person.staff.full_name,
                "role": "، ".join(person.roles),
            }
            for person in people
            if person.staff.active and person.staff.staff_id != principal.staff_id
        )

    def _inbox(self, principal: Principal, shift: ShiftKey, log: list[EventRecord]) -> dict[str, Any]:
        """G3: what waits for this person's own count; the offered figure stays hidden from the counter."""
        # Names travel with the offers: the counter may hold no warehouse section, and an id is never shown.
        names = {item.item_id: item.name for item in self._counted_items(shift)}
        places = {place.location_id: place.name for place in self._catalog.locations(shift)}
        mine = [
            {
                "offer_id": offer.event_id,
                "item_id": str(offer.payload["item_id"]),
                "item_name": names.get(str(offer.payload["item_id"]), ""),
                "to_location_id": str(offer.payload["to_location_id"]),
                "to_location_name": places.get(str(offer.payload["to_location_id"]), ""),
                "from_location_name": places.get(str(offer.payload["from_location_id"]), ""),
                "giver_id": offer.actor_id,
            }
            for offer in self._store.open_offers(log)
            if str(offer.payload["confirmer_id"]) == principal.staff_id
        ]
        waiting = [
            {"offer_id": offer.event_id, "item_id": str(offer.payload["item_id"])}
            for offer in self._store.open_offers(log)
            if offer.actor_id == principal.staff_id
        ]
        parcels = [
            {
                "delivery_id": parcel.delivery_id,
                "invoice_reference": parcel.invoice_reference,
                "item_ids": sorted({line.item_id for line in parcel.lines}),
                "items": [
                    {"item_id": item_id, "name": names.get(item_id, "")}
                    for item_id in sorted({line.item_id for line in parcel.lines})
                ],
            }
            for parcel in delivery_states(log).values()
            if parcel.awaiting_staff_id == principal.staff_id and not parcel.returned
        ]
        return {"restock_to_confirm": mine, "restock_waiting": waiting, "parcels_to_count": parcels}


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


def _delivery_payload(parcel: DeliveryState, viewer_id: str = "") -> dict[str, Any]:
    """A parcel as reception and the storekeeper read it: units and custody, never a price.

    The person the parcel was handed to counts it blind (G3): their copy of the lines names the items and
    leaves out reception's quantities, so the count they type is their own.
    """
    blind = parcel.awaiting_staff_id is not None and parcel.awaiting_staff_id == viewer_id
    return {
        "delivery_id": parcel.delivery_id,
        "order_id": parcel.order_id,
        "invoice_reference": parcel.invoice_reference,
        "status": parcel.status,
        "flagged_reason": parcel.flagged_reason,
        "custody": parcel.custody,
        "returned": parcel.returned,
        "packages": parcel.packages,
        "awaiting_staff_id": parcel.awaiting_staff_id,
        "blind": blind,
        "lines": [
            {"item_id": line.item_id, "location_id": line.location_id, "quantity": None if blind else line.quantity}
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
        "invoice_photo": parcel.invoice_photo,
        "receipt_photos": list(parcel.receipt_photos),
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


def _series_payload(series: PriceSeries) -> dict[str, Any]:
    """One item's prices from one vendor, oldest first; the accountant's view and nobody else's (F2)."""
    return {
        "item_id": series.item_id,
        "vendor": series.vendor,
        "latest": series.latest,
        "change": series.change_toman,
        "points": [
            {
                "delivery_id": point.delivery_id,
                "invoice_reference": point.invoice_reference,
                "at_tehran": point.at_tehran,
                "unit_price_toman": point.unit_price_toman,
                "quantity": point.quantity,
            }
            for point in series.points
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
        "counted_ids": list(warehouse.counted_ids),
        "baseline_done": warehouse.baseline_done,
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
