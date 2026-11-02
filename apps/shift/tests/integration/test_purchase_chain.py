"""The purchasing chain end to end through `PurchaseService` (S01 §4, S03 §3–4, SHF-4).

Money and stock are where a quiet bug costs the clinic, so this file runs the real service, the real
`EventStore`, the real `ProjectionEngine` and the real shift-event writer on one migrated synthetic
database: the whole chain from a draft to a paid invoice, the refusals that keep money and custody in
order, a flag that a manager's approval unlocks, a vendor return with its credit cap, a shelf charge
that moves both ends once, and the blind weekly count with its unnamed discrepancy. The rebuild has to
land on the same shelves as the live projection, and no answer may carry the figure being counted.

Every value here is the synthetic reference catalog plus made-up staff; no production database and no
patient data is read (ADR-006).
"""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, FixedClock, Seeded, create_staff
from handover_oracle import shelf_reference
from synthetic_catalog import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.permissions import permissions_for
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, ItemKind
from shift.services.event_store import EventStore, NewEvent
from shift.services.outbox import EventSubmission
from shift.services.projections import ProjectionEngine
from shift.services.purchase import PurchaseRuleError, PurchaseService
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventService

# The clinic clock the tests move by hand; the shift is the one every catalog change is visible from.
FIXED_NOW = datetime(2026, 10, 28, 6, 30, tzinfo=UTC)
SHIFT = ShiftKey(date(2026, 10, 28), 2)
# The synthetic catalog: paracetamol on the reception shelf and the main store, a counted item that
# lives in the main store, and the store's own location (S03 §1, tests/data/synthetic_catalog.py).
ITEM = "para_500_amp"
# A second shelf item the store never held: a charge is what leaves a store row behind for it (P-10).
OTHER = "diclofenac_75_amp"
LOCATION = "shelf_reception"
STORE = "main_store"
SYRINGE = "syringe_5cc"
SHELF_BEFORE = 3
STORE_BEFORE = 12
# A stored invoice photo's content hash: the service only checks the shape when it has no photo folder.
PHOTO = "a" * 64


@dataclass(frozen=True, slots=True)
class Desk:
    """One clinic with a seeded catalog, the real services and one session per role."""

    database: Database
    store: EventStore
    projections: ProjectionEngine
    catalog: CatalogService
    service: PurchaseService
    shift_events: ShiftEventService
    clock: FixedClock
    shift: ShiftKey
    storekeeper: Principal
    manager: Principal
    purchaser: Principal
    reception: Principal
    accountant: Principal
    nurse: Principal


def _principal(person: Seeded) -> Principal:
    """The session every route would hand the service for that person."""
    return Principal(
        staff_id=person.staff_id,
        full_name=person.full_name,
        roles=person.roles,
        permissions=permissions_for(person.roles),
        device_id=person.device_id,
        device_label="گوشی آزمایشی",
        session_hash=f"session-{person.staff_id}",
        csrf_token=f"csrf-{person.staff_id}",
        expires_at=datetime(2026, 10, 28, 23, 0, tzinfo=UTC),
    )


@pytest.fixture()
def desk(database: Database) -> Iterator[Desk]:
    """A settled store and shelf, so a charge or a count starts from a known figure."""
    seed_catalog(database)
    clock = FixedClock(FIXED_NOW)
    # One clock for the whole desk: the catalog's versions, the records' own times and the service's
    # "now" all read the same instant, so a stale count cannot land in a different week (R-09, P-11).
    store = EventStore(database, clock=clock)
    projections = ProjectionEngine(database, clock=clock)
    # The catalog change is visible from the shift after the running one, so the test's own clock has to
    # be what the catalog reads: a real clock would put the new item a week past this shift (R-09).
    catalog = CatalogService(database, clock=clock)
    catalog.save_item(
        SYRINGE,
        name="سرنگ ۵ سی‌سی",
        kind=ItemKind.COUNTED,
        location_id=STORE,
        unit="عدد",
        minimum=20,
        acc_code="10011",
        actor_id="staff-manager",
    )
    for person in (ROLES["storekeeper"], ROLES["manager"], ROLES["purchaser"], ROLES["reception"]):
        create_staff(
            database,
            staff_id=person.staff_id,
            full_name=person.full_name,
            mobile=person.mobile,
            roles=person.roles,
        )
    for person in (ROLES["accountant"], ROLES["nurse"]):
        create_staff(
            database,
            staff_id=person.staff_id,
            full_name=person.full_name,
            mobile=person.mobile,
            roles=person.roles,
        )
    # The figures the chain starts from, recorded the way the shift's own events record them.
    # The store's list is the catalog's (decision 2), so the settled week carries a count for every
    # counted item, stock or no stock; only the two the tests work with hold a figure.
    for item in catalog.items(SHIFT):
        if item.kind is not ItemKind.COUNTED:
            continue
        store.append(
            build_event(
                EventType.STOCK_WEEKLY_COUNT.value,
                payload={
                    "item_id": item.item_id,
                    "location_id": STORE,
                    "counted": STORE_BEFORE if item.item_id in {ITEM, SYRINGE} else 0,
                },
                shift_id=SHIFT.text,
            )
        )
    store.append(
        build_event(
            EventType.STOCK_WEEKLY_COUNT.value,
            payload={"item_id": ITEM, "location_id": LOCATION, "counted": SHELF_BEFORE},
            shift_id=SHIFT.text,
        )
    )
    projections.sync()
    settings = SettingsService(database)
    yield Desk(
        database=database,
        store=store,
        projections=projections,
        catalog=catalog,
        service=PurchaseService(database, store, projections, catalog, settings, clock=clock),
        shift_events=ShiftEventService(database, store, projections, catalog, settings, clock=clock),
        clock=clock,
        shift=SHIFT,
        storekeeper=_principal(ROLES["storekeeper"]),
        manager=_principal(ROLES["manager"]),
        purchaser=_principal(ROLES["purchaser"]),
        reception=_principal(ROLES["reception"]),
        accountant=_principal(ROLES["accountant"]),
        nurse=_principal(ROLES["nurse"]),
    )


def _act(desk: Desk, principal: Principal, action: str, **data: Any) -> dict[str, Any]:
    """One action the way the route runs it: the caller's principal and the running shift."""
    return desk.service.perform(principal, desk.shift, action, data)


def _state(desk: Desk, principal: Principal) -> dict[str, Any]:
    return desk.service.state(principal, desk.shift).to_payload()


def _shelf(desk: Desk, item_id: str, location_id: str) -> int | None:
    """The projected quantity of one shelf, or None when no event ever moved it."""
    with desk.database.read() as connection:
        rows = projections_repo.stock_rows(connection)
    return next(
        (row["qty"] for row in rows if (row["item_id"], row["location_id"]) == (item_id, location_id)),
        None,
    )


def _log(desk: Desk, event_type: EventType) -> list[Any]:
    with desk.database.read() as connection:
        return [event for event in events_repo.iter_events(connection) if event.type == event_type]


def _written(desk: Desk) -> int:
    with desk.database.read() as connection:
        return events_repo.count_events(connection)


def _cash_rows(desk: Desk) -> list[dict[str, Any]]:
    """The cash projection as it stands: a purchase record must leave it exactly as it found it."""
    with desk.database.read() as connection:
        return projections_repo.cash_rows(connection)


def _drafted(desk: Desk, quantity: int = 4) -> str:
    return _act(
        desk,
        desk.storekeeper,
        "order.draft",
        lines=[{"item_id": ITEM, "location_id": LOCATION, "quantity": quantity}],
        note="قفسه خالی شد",
    )["order_id"]


def _approved_order(desk: Desk) -> str:
    order_id = _drafted(desk)
    _act(desk, desk.manager, "order.decide", order_id=order_id, decision="approved", note=None)
    return order_id


def _placed_order(desk: Desk) -> str:
    order_id = _approved_order(desk)
    _act(desk, desk.purchaser, "order.place", order_id=order_id, vendor="داروخانهٔ نمونه", reference="۷")
    return order_id


def _received_delivery(desk: Desk, order_id: str | None = None) -> str:
    return _act(
        desk,
        desk.reception,
        "delivery.receive",
        order_id=order_id,
        invoice_reference="فاکتور ۱۲",
        lines=[{"item_id": ITEM, "location_id": LOCATION, "quantity": 4}],
        packages=1,
        invoice_photo_sha256=PHOTO,
    )["delivery_id"]


def _digitized_delivery(desk: Desk, unit_price: int = 25_000, quantity: int = 4) -> str:
    delivery_id = _received_delivery(desk, _placed_order(desk))
    _act(
        desk,
        desk.accountant,
        "invoice.digitize",
        delivery_id=delivery_id,
        lines=[{"item_id": ITEM, "quantity": quantity, "unit_price_toman": unit_price}],
        reference=None,
    )
    return delivery_id


def _invoice(desk: Desk, delivery_id: str) -> dict[str, Any]:
    invoices = _state(desk, desk.accountant)["invoices"]
    assert invoices is not None
    found = next(invoice for invoice in invoices if invoice["delivery_id"] == delivery_id)
    return found


def _charge(desk: Desk, *, item: str = ITEM, to: str = LOCATION, quantity: int = 5) -> str:
    """A shelf charge the way the clinic does it: the storekeeper offers, the named nurse confirms (G3)."""
    offered = _act(
        desk,
        desk.storekeeper,
        "restock.record",
        item_id=item,
        from_location_id=STORE,
        to_location_id=to,
        quantity=quantity,
        confirmer_id=desk.nurse.staff_id,
    )
    _act(desk, desk.nurse, "restock.confirm", offer_id=offered["event_id"], quantity=quantity)
    return offered["event_id"]


def _approve(desk: Desk, event_id: str) -> None:
    """The manager's decision through the phone's own approval flow, exactly as the card sends it."""
    minted = new_ulid(desk.clock.now_utc())
    desk.shift_events.record(
        EventSubmission(
            type=EventType.EVENT_APPROVED.value,
            payload={"note": None},
            actor_id=desk.manager.staff_id,
            device_id=desk.manager.device_id,
            client_at=desk.clock.now_utc(),
            client_event_id=minted,
            idempotency_key=minted,
            shift_id=desk.shift.text,
            shift_work_date=desk.shift.work_date,
            ref_event_id=event_id,
        ),
        principal=desk.manager,
        shift=desk.shift,
    )


# --- the chain ------------------------------------------------------------------------------


def test_the_whole_chain_runs_from_a_draft_to_a_paid_invoice(desk: Desk) -> None:
    """P-02…P-08 in one pass: the statuses and the server's own invoice total, never a client's."""
    order_id = _drafted(desk)
    _act(desk, desk.manager, "order.decide", order_id=order_id, decision="approved", note=None)
    _act(desk, desk.purchaser, "order.place", order_id=order_id, vendor="داروخانهٔ نمونه", reference="۷")
    delivery_id = _received_delivery(desk, order_id)
    _act(
        desk,
        desk.accountant,
        "invoice.digitize",
        delivery_id=delivery_id,
        lines=[{"item_id": ITEM, "quantity": 4, "unit_price_toman": 25_000}],
        reference=None,
    )
    _act(
        desk,
        desk.accountant,
        "payment.record",
        delivery_id=delivery_id,
        amount_toman=100_000,
        method="transfer",
        paid_on=None,
    )

    invoice = _invoice(desk, delivery_id)
    assert (invoice["invoice_total"], invoice["paid_total"], invoice["remaining"]) == (100_000, 100_000, 0)
    assert invoice["status"] == "paid"
    assert _state(desk, desk.storekeeper)["orders"][0]["status"] == "placed"
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE, "a delivery never moves a shelf (P-05)"


def test_the_server_sums_the_invoice_and_refuses_a_second_digitizing(desk: Desk) -> None:
    """P-07: a wrong total the client sends is not trusted, and a second invoice is refused."""
    delivery_id = _received_delivery(desk)
    _act(
        desk,
        desk.accountant,
        "invoice.digitize",
        delivery_id=delivery_id,
        lines=[
            {"item_id": ITEM, "quantity": 2, "unit_price_toman": 10_000},
            {"item_id": ITEM, "quantity": 3, "unit_price_toman": 20_000},
        ],
        reference=None,
    )

    assert _invoice(desk, delivery_id)["invoice_total"] == 80_000
    with pytest.raises(PurchaseRuleError):
        _act(
            desk,
            desk.accountant,
            "invoice.digitize",
            delivery_id=delivery_id,
            lines=[{"item_id": ITEM, "quantity": 1, "unit_price_toman": 1}],
            reference=None,
        )


def test_two_partial_payments_close_the_invoice_and_one_toman_more_is_refused(desk: Desk) -> None:
    delivery_id = _digitized_delivery(desk, unit_price=25_000, quantity=4)

    _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=60_000, method="cash")
    assert _invoice(desk, delivery_id)["status"] == "digitized"
    with pytest.raises(PurchaseRuleError, match="بیشتر"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=40_001, method="cash")
    _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=40_000, method="card")

    assert _invoice(desk, delivery_id)["status"] == "paid"


# --- what the rules refuse ------------------------------------------------------------------


def test_a_payment_needs_an_invoice_first(desk: Desk) -> None:
    delivery_id = _received_delivery(desk)

    with pytest.raises(PurchaseRuleError, match="دیجیتال"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=1_000, method="cash")


def test_place_before_approval_and_a_second_decision_are_refused(desk: Desk) -> None:
    """P-02, P-03: nothing is written by a refusal, so the order waits exactly where it was."""
    order_id = _drafted(desk)
    written = _written(desk)

    with pytest.raises(PurchaseRuleError, match="تأییدشده"):
        _act(desk, desk.purchaser, "order.place", order_id=order_id, vendor="فروشنده", reference=None)
    _act(desk, desk.manager, "order.decide", order_id=order_id, decision="approved", note=None)
    with pytest.raises(PurchaseRuleError, match="منتظر تصمیم"):
        _act(desk, desk.manager, "order.decide", order_id=order_id, decision="rejected", note=None)

    assert _written(desk) == written + 1, "only the approval landed"


def test_change_returns_the_order_and_the_fresh_draft_waits_again(desk: Desk) -> None:
    """P-02: «تغییر» is a decision; the re-draft keeps the id and comes back to the manager."""
    order_id = _drafted(desk)
    _act(desk, desk.manager, "order.decide", order_id=order_id, decision="changed", note="تعداد را کم کنید")

    redrafted = _act(
        desk,
        desk.storekeeper,
        "order.draft",
        order_id=order_id,
        lines=[{"item_id": ITEM, "location_id": LOCATION, "quantity": 2}],
        note=None,
    )
    order = _state(desk, desk.storekeeper)["orders"][0]

    assert redrafted["order_id"] == order_id
    assert (order["status"], [line["quantity"] for line in order["lines"]]) == ("submitted", [2])


def test_a_delivery_cannot_hang_off_an_unplaced_order(desk: Desk) -> None:
    """P-04: an order number is optional, but if given it has to be a placed order."""
    order_id = _approved_order(desk)

    with pytest.raises(PurchaseRuleError, match="فروشنده"):
        _received_delivery(desk, order_id)


def test_a_return_needs_a_delivery_and_a_credit_needs_the_return(desk: Desk) -> None:
    """P-09: neither a return nor a credit can name a delivery the log does not hold."""
    with pytest.raises(PurchaseRuleError, match="پیدا نشد"):
        _act(
            desk,
            desk.storekeeper,
            "return.record",
            delivery_id="01J00000000000000000000000",
            lines=[{"item_id": ITEM, "quantity": 1}],
            reason="بار شکسته بود",
        )


def test_every_action_checks_its_own_permission(desk: Desk) -> None:
    """R-07: the route's `events.append` is not the permission; the action's own role is."""
    order_id = _drafted(desk)

    with pytest.raises(PurchaseRuleError, match="اجازه"):
        _act(desk, desk.purchaser, "order.decide", order_id=order_id, decision="approved", note=None)
    with pytest.raises(PurchaseRuleError, match="اجازه"):
        _act(desk, desk.reception, "order.draft", lines=[])
    with pytest.raises(PurchaseRuleError, match="اجازه"):
        _act(desk, desk.nurse, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=1)
    with pytest.raises(PurchaseRuleError, match="شناخته"):
        _act(desk, desk.manager, "order.explode")


# --- a flagged parcel -----------------------------------------------------------------------


def test_a_flag_locks_the_payment_until_the_manager_approves_it(desk: Desk) -> None:
    """P-06: the label stops the money, and the manager's own decision is what lets it through."""
    delivery_id = _digitized_delivery(desk)
    flag = _act(desk, desk.reception, "delivery.flag", delivery_id=delivery_id, reason="short", note="یک جعبه کم")

    with pytest.raises(PurchaseRuleError, match="قفل"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=10_000, method="cash")
    with pytest.raises(PurchaseRuleError, match="برچسب"):
        _act(desk, desk.reception, "delivery.flag", delivery_id=delivery_id, reason="broken")

    pending = desk.shift_events.pending_for(desk.manager)
    assert [entry.event_id for entry in pending] == [flag["event_id"]]
    assert (pending[0].code, pending[0].summary["reason"]) == ("خ", "کم")

    _approve(desk, flag["event_id"])
    _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=10_000, method="cash")

    invoice = _invoice(desk, delivery_id)
    assert (invoice["payment_locked"], invoice["paid_total"]) == (False, 10_000)


def test_a_flagged_parcel_is_still_digitized_and_only_its_payment_waits(desk: Desk) -> None:
    """P-06: the label locks the money, not the accountant's price entry (the card's own chain)."""
    delivery_id = _received_delivery(desk, _placed_order(desk))
    _act(desk, desk.reception, "delivery.flag", delivery_id=delivery_id, reason="short", note=None)

    _act(
        desk,
        desk.accountant,
        "invoice.digitize",
        delivery_id=delivery_id,
        lines=[{"item_id": ITEM, "quantity": 4, "unit_price_toman": 25_000}],
        reference=None,
    )

    invoice = _invoice(desk, delivery_id)
    assert (invoice["invoice_total"], invoice["payment_locked"]) == (100_000, True)
    with pytest.raises(PurchaseRuleError, match="قفل"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=100_000, method="cash")


def test_a_rejected_flag_keeps_the_payment_locked(desk: Desk) -> None:
    """The owner's rule: a refused parcel stays on the return path instead of being paid for."""
    delivery_id = _digitized_delivery(desk)
    flag = _act(desk, desk.reception, "delivery.flag", delivery_id=delivery_id, reason="wrong", note=None)
    minted = new_ulid(desk.clock.now_utc())
    desk.shift_events.record(
        EventSubmission(
            type=EventType.EVENT_REJECTED.value,
            payload={"note": None},
            actor_id=desk.manager.staff_id,
            device_id=desk.manager.device_id,
            client_at=desk.clock.now_utc(),
            client_event_id=minted,
            idempotency_key=minted,
            shift_id=desk.shift.text,
            shift_work_date=desk.shift.work_date,
            ref_event_id=flag["event_id"],
        ),
        principal=desk.manager,
        shift=desk.shift,
    )

    assert _invoice(desk, delivery_id)["payment_locked"] is True
    with pytest.raises(PurchaseRuleError, match="قفل"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=1_000, method="cash")


def test_a_parcel_goes_to_the_storekeeper_who_counts_it_before_it_joins_the_store(desk: Desk) -> None:
    """P-12, G3: the parcel is handed on by its real id; custody and stock move on the receiver's own count."""
    delivery_id = _received_delivery(desk)

    with pytest.raises(PurchaseRuleError, match="انباردار"):
        _act(
            desk,
            desk.reception,
            "delivery.transfer",
            delivery_id=delivery_id,
            to_staff_id=desk.nurse.staff_id,
            to_kind="storekeeper",
            packages=1,
        )
    # Both sides confirm (owner document §4, line «هر دو طرف»): a person cannot name themselves as the
    # receiver of the parcel they are handing over.
    with pytest.raises(PurchaseRuleError, match="خودتان"):
        _act(
            desk,
            desk.reception,
            "delivery.transfer",
            delivery_id=delivery_id,
            to_staff_id=desk.reception.staff_id,
            to_kind="shift",
            packages=1,
        )
    _act(
        desk,
        desk.reception,
        "delivery.transfer",
        delivery_id=delivery_id,
        to_staff_id=desk.nurse.staff_id,
        to_kind="shift",
        packages=1,
    )
    _act(
        desk,
        desk.nurse,
        "delivery.transfer",
        delivery_id=delivery_id,
        to_staff_id=desk.storekeeper.staff_id,
        to_kind="storekeeper",
        packages=1,
    )

    # One side has spoken: the parcel is still on its way, and the store has not grown.
    assert len(desk.service.pending(desk.shift).undelivered) == 1
    assert _state(desk, desk.reception)["deliveries"][0]["custody"] == "shift"
    assert _shelf(desk, ITEM, STORE) == STORE_BEFORE

    mine = _state(desk, desk.storekeeper)
    assert mine["inbox"]["parcels_to_count"][0]["delivery_id"] == delivery_id
    assert mine["deliveries"][0]["lines"][0]["quantity"] is None, "the receiver counts blind"
    with pytest.raises(PurchaseRuleError, match="سپرده"):
        _act(desk, desk.nurse, "delivery.confirm", delivery_id=delivery_id, lines=[{"item_id": ITEM, "quantity": 4}])

    answer = _act(
        desk, desk.storekeeper, "delivery.confirm", delivery_id=delivery_id, lines=[{"item_id": ITEM, "quantity": 4}]
    )

    assert answer["differs"] is False
    assert desk.service.pending(desk.shift).undelivered == ()
    assert _state(desk, desk.reception)["deliveries"][0]["custody"] == "storekeeper"
    assert _shelf(desk, ITEM, STORE) == STORE_BEFORE + 4, "vendor goods join the store on the receiver's count"
    # The emptied projection above is a test device, so the log is replayed rather than compared to it:
    # a replay has to rebuild the store's figures from the records alone.
    desk.projections.rebuild()
    assert _shelf(desk, ITEM, STORE) == STORE_BEFORE + 4
    with pytest.raises(PurchaseRuleError, match="منتظر"):
        _act(
            desk,
            desk.storekeeper,
            "delivery.confirm",
            delivery_id=delivery_id,
            lines=[{"item_id": ITEM, "quantity": 4}],
        )


def test_the_door_receiver_cannot_confirm_the_count_of_their_own_receipt(desk: Desk) -> None:
    """Owner document §4, line «هر دو طرف»: one person holding both roles still cannot be both sides."""
    both = Seeded("staff-both", "پذیرش و انبار", "09120000010", "device-both", ("reception", "storekeeper"))
    create_staff(desk.database, staff_id=both.staff_id, full_name=both.full_name, mobile=both.mobile, roles=both.roles)
    me = _principal(both)
    delivery_id = _act(
        desk,
        me,
        "delivery.receive",
        order_id=None,
        invoice_reference="فاکتور ۱۲",
        lines=[{"item_id": ITEM, "location_id": LOCATION, "quantity": 4}],
        packages=1,
        invoice_photo_sha256=PHOTO,
    )["delivery_id"]
    with pytest.raises(PurchaseRuleError, match="خودتان"):
        _act(
            desk,
            me,
            "delivery.transfer",
            delivery_id=delivery_id,
            to_staff_id=me.staff_id,
            to_kind="storekeeper",
        )
    # Somebody else may hand it back, but the person who counted it at the door still cannot count it in.
    _act(
        desk,
        desk.reception,
        "delivery.transfer",
        delivery_id=delivery_id,
        to_staff_id=me.staff_id,
        to_kind="storekeeper",
    )

    with pytest.raises(PurchaseRuleError, match="خودتان"):
        _act(
            desk,
            me,
            "delivery.confirm",
            delivery_id=delivery_id,
            lines=[{"item_id": ITEM, "quantity": 4}],
        )
    assert _shelf(desk, ITEM, STORE) == STORE_BEFORE


def test_a_receivers_count_that_differs_enters_the_store_and_locks_the_payment(desk: Desk) -> None:
    """G3: the receiver's count is what is on the shelf; a difference flags the parcel, it does not hide it."""
    delivery_id = _received_delivery(desk)
    _act(
        desk,
        desk.reception,
        "delivery.transfer",
        delivery_id=delivery_id,
        to_staff_id=desk.storekeeper.staff_id,
        to_kind="storekeeper",
    )

    with pytest.raises(PurchaseRuleError, match="همهٔ اقلام"):
        _act(
            desk,
            desk.storekeeper,
            "delivery.confirm",
            delivery_id=delivery_id,
            lines=[{"item_id": SYRINGE, "quantity": 1}],
        )
    answer = _act(
        desk, desk.storekeeper, "delivery.confirm", delivery_id=delivery_id, lines=[{"item_id": ITEM, "quantity": 3}]
    )

    assert answer["differs"] is True
    assert _shelf(desk, ITEM, STORE) == STORE_BEFORE + 3
    parcel = _state(desk, desk.reception)["deliveries"][0]
    assert (parcel["flagged_reason"], parcel["custody"]) == ("short", "storekeeper")
    assert [row.delivery_id for row in desk.service.pending(desk.shift).locked_deliveries] == [delivery_id]


# --- a vendor return ------------------------------------------------------------------------


def test_a_vendor_return_lets_the_accountant_credit_up_to_the_invoice(desk: Desk) -> None:
    """P-09: the credit hangs off the return and never goes past the invoice, and no shelf moves."""
    delivery_id = _digitized_delivery(desk, unit_price=25_000, quantity=4)

    with pytest.raises(PurchaseRuleError, match="مرجوعی"):
        _act(desk, desk.accountant, "credit.record", delivery_id=delivery_id, amount_toman=1_000, note=None)
    _act(
        desk,
        desk.storekeeper,
        "return.record",
        delivery_id=delivery_id,
        lines=[{"item_id": ITEM, "quantity": 1}],
        reason="بار شکسته بود",
    )
    with pytest.raises(PurchaseRuleError, match="سقف"):
        _act(desk, desk.accountant, "credit.record", delivery_id=delivery_id, amount_toman=100_001, note=None)
    _act(desk, desk.accountant, "credit.record", delivery_id=delivery_id, amount_toman=25_000, note=None)

    invoice = _invoice(desk, delivery_id)
    # The credit sits on the original invoice and lowers what is still owed (S01 §4).
    assert (invoice["returned"], invoice["credit_total"], invoice["remaining"]) == (True, 25_000, 75_000)
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE, "a return is a purchasing document (P-09)"
    with pytest.raises(PurchaseRuleError, match="بیشتر"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=75_001, method="cash")
    _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=75_000, method="cash")
    assert _invoice(desk, delivery_id)["remaining"] == 0


def test_a_returned_parcel_is_paid_only_after_its_credit_is_recorded(desk: Desk) -> None:
    """The part that went back is never paid by mistake while the credit note is still on its way."""
    delivery_id = _digitized_delivery(desk, unit_price=25_000, quantity=4)
    _act(
        desk,
        desk.storekeeper,
        "return.record",
        delivery_id=delivery_id,
        lines=[{"item_id": ITEM, "quantity": 1}],
        reason="بار شکسته بود",
    )
    with pytest.raises(PurchaseRuleError, match="اول بستانکاری"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=1_000, method="cash")


# --- the shelf charge -----------------------------------------------------------------------


def test_a_shelf_charge_needs_both_sides_and_then_moves_the_store_down_and_the_shelf_up_once(desk: Desk) -> None:
    """P-10, G3: the offer moves nothing; the named person's count moves both ends, once, and rebuild agrees."""
    offered = _act(
        desk,
        desk.storekeeper,
        "restock.record",
        item_id=ITEM,
        from_location_id=STORE,
        to_location_id=LOCATION,
        quantity=5,
        confirmer_id=desk.nurse.staff_id,
    )
    assert (_shelf(desk, ITEM, STORE), _shelf(desk, ITEM, LOCATION)) == (STORE_BEFORE, SHELF_BEFORE)
    waiting = _state(desk, desk.nurse)["inbox"]["restock_to_confirm"]
    assert [row["offer_id"] for row in waiting] == [offered["event_id"]]
    assert "quantity" not in waiting[0], "the person counting the shelf does not read the offered figure"

    with pytest.raises(PurchaseRuleError, match="کسی که"):
        _act(desk, desk.reception, "restock.confirm", offer_id=offered["event_id"], quantity=5)
    with pytest.raises(PurchaseRuleError, match="نمی‌خواند"):
        _act(desk, desk.nurse, "restock.confirm", offer_id=offered["event_id"], quantity=4)
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE

    _act(desk, desk.nurse, "restock.confirm", offer_id=offered["event_id"], quantity=5)
    with pytest.raises(PurchaseRuleError, match="قبلاً"):
        _act(desk, desk.nurse, "restock.confirm", offer_id=offered["event_id"], quantity=5)

    assert (_shelf(desk, ITEM, STORE), _shelf(desk, ITEM, LOCATION)) == (STORE_BEFORE - 5, SHELF_BEFORE + 5)
    report = desk.projections.rebuild()
    assert report.applied
    assert report.drift == ()
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE + 5


def test_a_charge_to_the_same_place_or_into_a_shelf_without_the_item_is_refused(desk: Desk) -> None:
    """P-10: the two ends must be two places, and the item has to belong to the shelf it lands on."""
    named = {"confirmer_id": desk.nurse.staff_id}
    with pytest.raises(PurchaseRuleError, match="یکی"):
        _act(
            desk,
            desk.storekeeper,
            "restock.record",
            item_id=ITEM,
            from_location_id=LOCATION,
            to_location_id=LOCATION,
            quantity=1,
            **named,
        )
    with pytest.raises(PurchaseRuleError, match="کاتالوگ"):
        _act(
            desk,
            desk.storekeeper,
            "restock.record",
            item_id=ITEM,
            from_location_id=STORE,
            to_location_id="cpr_room",
            quantity=1,
            **named,
        )
    # A source that is not the store is refused before the catalog is even asked.
    with pytest.raises(PurchaseRuleError, match="انبار اصلی"):
        _act(
            desk,
            desk.storekeeper,
            "restock.record",
            item_id=ITEM,
            from_location_id="no_such_place",
            to_location_id=LOCATION,
            quantity=1,
            **named,
        )

    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE


def test_the_confirmer_is_somebody_else_and_somebody_the_clinic_still_has(desk: Desk) -> None:
    """G3: nobody confirms their own charge, and the named person must be active staff."""
    offer = {
        "item_id": ITEM,
        "from_location_id": STORE,
        "to_location_id": LOCATION,
        "quantity": 1,
    }
    with pytest.raises(PurchaseRuleError, match="کس دیگری"):
        _act(desk, desk.storekeeper, "restock.record", confirmer_id=desk.storekeeper.staff_id, **offer)
    with pytest.raises(PurchaseRuleError, match="تأییدکننده"):
        _act(desk, desk.storekeeper, "restock.record", confirmer_id="staff-ghost", **offer)
    _act(desk, desk.storekeeper, "restock.record", confirmer_id=desk.nurse.staff_id, **offer)

    recorded = [event.payload["confirmer_id"] for event in _log(desk, EventType.RESTOCK_OFFERED)]
    assert recorded == [desk.nurse.staff_id]


# --- the weekly count -----------------------------------------------------------------------


def test_a_weekly_count_writes_an_unnamed_discrepancy_and_an_expiry_flag(desk: Desk) -> None:
    """P-11: the gap is `expected − counted` (positive a shortage), carries no culprit, hangs off the count."""
    answer = _act(
        desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=9, near_expiry=True
    )

    count = _log(desk, EventType.STOCK_WEEKLY_COUNT)[-1]
    gap = _log(desk, EventType.DISCREPANCY_RECORDED)[-1]
    flag = _log(desk, EventType.EXPIRY_FLAGGED)[-1]
    assert answer == {"status": "recorded", "event_id": count.event_id}, "a blind count answers no figure"
    assert (gap.payload["scope"], gap.payload["item_id"], gap.payload["location_id"]) == ("item", SYRINGE, STORE)
    assert (gap.payload["difference"], gap.payload["culprit_id"]) == (3, None), "3 short of 12 is a shortage of 3"
    assert gap.ref_event_id == count.event_id
    assert flag.ref_event_id == count.event_id
    assert _shelf(desk, SYRINGE, STORE) == 9


def test_a_count_above_the_store_target_reads_as_a_surplus_and_a_short_one_as_a_shortage(desk: Desk) -> None:
    """H-01: expected 10, counted 8 is a shortage of 2; counted 2 more than expected is a surplus of 2."""
    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=STORE_BEFORE - 2)
    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=STORE_BEFORE)

    assert [event.payload["difference"] for event in _log(desk, EventType.DISCREPANCY_RECORDED)] == [2, -2]


def test_a_count_that_matches_writes_no_discrepancy(desk: Desk) -> None:
    """A count equal to the store's own figure is a count and nothing else: no gap, no expiry flag."""
    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=STORE_BEFORE)

    assert _log(desk, EventType.DISCREPANCY_RECORDED) == []
    assert _log(desk, EventType.EXPIRY_FLAGGED) == []


def test_the_weekly_status_turns_done_once_the_window_has_a_count(desk: Desk) -> None:
    """A count closes the week it lands in — even a late one — and only when every item is counted."""
    state = _state(desk, desk.storekeeper)["warehouse"]["weekly"]
    assert state["state"] == "done", "the desk's own store counts settle the week they landed in"

    desk.clock.now = datetime(2026, 11, 2, 6, 30, tzinfo=UTC)  # Monday 10:00 in Tehran
    weekly = _state(desk, desk.storekeeper)["warehouse"]["weekly"]
    assert weekly["state"] == "overdue", "the new window is uncounted and its 07:30 deadline has passed"

    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=STORE_BEFORE)
    weekly = _state(desk, desk.storekeeper)["warehouse"]["weekly"]
    assert weekly["state"] == "overdue", "one counted item does not close the week (decision 2)"

    # Every counted item of the catalog closes it, including the ones holding no stock.
    for row in _state(desk, desk.storekeeper)["warehouse"]["items"]:
        if row["item_id"] != SYRINGE:
            _act(desk, desk.storekeeper, "weekly.count", item_id=row["item_id"], location_id=STORE, counted=0)

    weekly = _state(desk, desk.storekeeper)["warehouse"]["weekly"]
    assert weekly["state"] == "done"
    assert (weekly["window_start"], weekly["window_end"]) == ("2026-10-31", "2026-11-02")


def test_a_count_of_an_item_that_is_not_counted_there_is_refused(desk: Desk) -> None:
    with pytest.raises(PurchaseRuleError, match="شمارشی"):
        _act(desk, desk.storekeeper, "weekly.count", item_id="store_lock_ok", location_id=STORE, counted=1)


def test_a_shelf_count_never_marks_the_week_done(desk: Desk) -> None:
    """The weekly status is about the main store, so a shelf count is not a weekly count at all."""
    # A window the desk's own store counts do not cover: Saturday opens it, so nothing has counted yet.
    desk.clock.now = datetime(2026, 10, 31, 6, 30, tzinfo=UTC)  # Saturday 10:00 in Tehran
    assert _state(desk, desk.storekeeper)["warehouse"]["weekly"]["state"] == "due"
    written = len(_log(desk, EventType.STOCK_WEEKLY_COUNT))

    with pytest.raises(PurchaseRuleError, match="انبار اصلی"):
        _act(desk, desk.storekeeper, "weekly.count", item_id=ITEM, location_id=LOCATION, counted=SHELF_BEFORE)

    assert len(_log(desk, EventType.STOCK_WEEKLY_COUNT)) == written, "a refused shelf count writes nothing"
    assert _state(desk, desk.storekeeper)["warehouse"]["weekly"]["state"] == "due", "a shelf count leaves the week open"


def test_the_store_list_is_the_whole_catalog_with_or_without_stock(desk: Desk) -> None:
    """Decision 2: the store counts every counted item of the catalog, and a charge adds no second row."""
    before = [row["item_id"] for row in _state(desk, desk.storekeeper)["warehouse"]["items"]]
    counted = sorted(item.item_id for item in desk.catalog.items(desk.shift) if item.kind is ItemKind.COUNTED)
    _charge(desk, item=OTHER, quantity=2)
    after = [row["item_id"] for row in _state(desk, desk.storekeeper)["warehouse"]["items"]]

    assert before == counted, "the list is the catalog's, never the projected stock's"
    assert OTHER in before, "a counted item whose catalog home is the reception shelf is still the store's"
    assert after == before, "a charge leaves no second row behind"
    homes = {item.item_id: item.location_id for item in desk.catalog.items(desk.shift) if item.kind is ItemKind.COUNTED}
    rows = _state(desk, desk.storekeeper)["warehouse"]["items"]
    assert {row["item_id"]: row["location_id"] for row in rows} == homes, "each row keeps its catalog place"


# --- what each role may see ------------------------------------------------------------------


def test_the_state_is_cut_down_to_what_the_callers_permissions_open(desk: Desk) -> None:
    """R-01, R-07: a section this person may not see is `None`, and the buttons are only its own."""
    accountant = _state(desk, desk.accountant)
    reception = _state(desk, desk.reception)
    purchaser = _state(desk, desk.purchaser)

    assert (accountant["orders"], accountant["deliveries"], accountant["warehouse"]) == (None, None, None)
    assert accountant["invoices"] == []
    # The accountant counts the store's items too (S03 §1), so the count button comes with the invoice.
    assert accountant["permissions"] == ["purchase.payment.record", "stock.count.submit"]
    assert (reception["orders"], reception["invoices"], reception["warehouse"]) == (None, None, None)
    assert reception["deliveries"] == []
    assert (purchaser["orders"], purchaser["invoices"], purchaser["warehouse"]) == ([], None, None)
    assert purchaser["below_minimum"] is None and purchaser["price_history"] is None, "no stock, no price"


def test_the_warehouse_section_carries_no_projected_figure(desk: Desk) -> None:
    """P-11: the page the counter opens must not hand him the number he is counting blind."""
    warehouse = _state(desk, desk.storekeeper)["warehouse"]

    assert warehouse["items"]
    assert all(set(item) == {"item_id", "name", "location_id", "unit", "minimum"} for item in warehouse["items"])
    assert set(warehouse) == {"items", "locations", "weekly", "restock", "counted_ids", "baseline_done"}


def test_below_minimum_lists_the_items_under_their_catalog_minimum(desk: Desk) -> None:
    """P-01: a store below its own minimum is suggested, and it clears once the count refills it."""
    below = {row["item_id"] for row in _state(desk, desk.storekeeper)["below_minimum"]}

    assert {ITEM, SYRINGE} <= below, "3 on a shelf of 10, and 12 in the store against a floor of 20"

    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=20)

    assert SYRINGE not in {row["item_id"] for row in _state(desk, desk.storekeeper)["below_minimum"]}
    assert ITEM in {row["item_id"] for row in _state(desk, desk.storekeeper)["below_minimum"]}


def test_the_manager_card_sees_the_open_items_and_no_figure(desk: Desk) -> None:
    """M-07, M-09: the card carries items, times and statuses — no shelf figure and no price."""
    order_id = _drafted(desk)
    delivery_id = _received_delivery(desk)

    pending = desk.service.pending(desk.shift)

    assert [order.order_id for order in pending.orders] == [order_id]
    assert [parcel.delivery_id for parcel in pending.undelivered] == [delivery_id]
    assert pending.locked_deliveries == ()
    assert {row.item_id for row in pending.below_minimum} >= {ITEM}
    assert pending.weekly.state == "done", "the desk's store counts settle the week they landed in"


def test_the_card_carries_the_waiting_order_the_locked_parcel_and_the_warehouse_work(desk: Desk) -> None:
    """M-19..M-21: the manager's stored card is where the chain's decisions and gaps actually land."""
    from shift.services.daily_card import DailyCardService

    order_id = _act(
        desk,
        desk.storekeeper,
        "order.draft",
        lines=[{"item_id": ITEM, "location_id": LOCATION, "quantity": 4}],
        note="کمبود قفسه",
    )["order_id"]
    delivery_id = _received_delivery(desk, _placed_order(desk))
    _act(desk, desk.reception, "delivery.flag", delivery_id=delivery_id, reason="short", note=None)

    card = DailyCardService(desk.database, clock=desk.clock).build(date(2026, 10, 28))

    assert card is not None
    assert [row.order_id for row in card.purchase_orders] == [order_id]
    assert card.purchase_orders[0].lines[0].endswith(" × 4"), "the line names the item and its quantity, never a price"
    assert [row.delivery_id for row in card.flagged_deliveries] == [delivery_id]
    assert card.flagged_deliveries[0].reason_fa == "کم"
    assert delivery_id in {row.delivery_id for row in card.parcels_in_transit}
    assert card.weekly_count[0].state in {"due", "overdue", "done"}
    below = {row.item_id: row for row in card.below_minimum}
    assert below[ITEM].quantity == SHELF_BEFORE and below[ITEM].minimum == 10
    assert all(row.quantity < row.minimum for row in card.below_minimum)
    # The stored document round-trips the new sections, and none of them carries a figure of the box.
    assert card.to_payload()["sections"]["purchase_orders"][0]["order_id"] == order_id
    assert "amount" not in card.to_payload()["sections"]["purchase_orders"][0]


def test_the_handover_hands_a_real_parcel_and_stops_listing_it_once_the_store_takes_it(desk: Desk) -> None:
    """P-12: the parcel the handover offers is the delivery itself, not an id somebody typed."""
    delivery_id = _received_delivery(desk, _placed_order(desk))
    assert delivery_id in {parcel.delivery_id for parcel in desk.service.in_transit(desk.shift)}

    _act(
        desk,
        desk.reception,
        "delivery.transfer",
        delivery_id=delivery_id,
        to_staff_id=desk.storekeeper.staff_id,
        to_kind="storekeeper",
        packages=1,
    )
    assert delivery_id in {parcel.delivery_id for parcel in desk.service.in_transit(desk.shift)}, "not counted yet"

    _act(desk, desk.storekeeper, "delivery.confirm", delivery_id=delivery_id, lines=[{"item_id": ITEM, "quantity": 4}])

    assert desk.service.in_transit(desk.shift) == ()


def test_the_chain_never_moves_the_cash_box(desk: Desk) -> None:
    """P-08 and S03 §5: invoices, payments and credits are accounting figures, not box movements."""
    desk.store.append(
        build_event(EventType.HANDOVER_STARTED.value, payload={"opening_cash": 1_000_000}, shift_id=SHIFT.text)
    )
    desk.projections.sync()
    before = _cash_rows(desk)
    assert [(row["expected"], row["counted"]) for row in before] == [(1_000_000, 1_000_000)], "a real box to keep"
    delivery_id = _digitized_delivery(desk)
    _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=10_000, method="cash")
    _act(
        desk,
        desk.storekeeper,
        "return.record",
        delivery_id=delivery_id,
        lines=[{"item_id": ITEM, "quantity": 1}],
        reason="بار شکسته بود",
    )
    _act(desk, desk.accountant, "credit.record", delivery_id=delivery_id, amount_toman=5_000, note=None)

    assert _cash_rows(desk) == before, "a purchase record never touches proj_cash"


def test_only_the_storekeeper_records_a_vendor_return(desk: Desk) -> None:
    """P-09: the storekeeper lets the goods out, so reception and the purchaser cannot record the exit."""
    delivery_id = _received_delivery(desk, _placed_order(desk))
    for person in (desk.reception, desk.purchaser):
        with pytest.raises(PurchaseRuleError, match="انباردار|نقش شما"):
            _act(
                desk,
                person,
                "return.record",
                delivery_id=delivery_id,
                lines=[{"item_id": ITEM, "quantity": 1}],
                reason="بار شکسته بود",
            )


def test_a_shelf_charge_moves_stock_only_from_the_main_store(desk: Desk) -> None:
    """P-10: the store charges a shelf, so a shelf-to-shelf move is refused."""
    with pytest.raises(PurchaseRuleError, match="انبار اصلی"):
        _act(
            desk,
            desk.storekeeper,
            "restock.record",
            item_id=ITEM,
            from_location_id=LOCATION,
            to_location_id="cpr_room",
            quantity=1,
            confirmer_id=desk.nurse.staff_id,
        )


def test_two_payments_at_once_cannot_pass_the_invoice_balance(desk: Desk) -> None:
    """P-08: the balance check and its append are one critical section, so a double-click cannot double-pay.

    The lock lives on the service module, so two service instances in one process are enough to show it:
    without it both threads fold the pre-payment log, both pass the check and the log keeps 120,000.
    """
    delivery_id = _digitized_delivery(desk, unit_price=25_000, quantity=4)  # a 100,000 Toman invoice
    barrier = threading.Barrier(2)

    def pay() -> str:
        service = PurchaseService(
            desk.database,
            desk.store,
            desk.projections,
            desk.catalog,
            SettingsService(desk.database),
            clock=desk.clock,
        )
        barrier.wait(timeout=10)
        try:
            service.perform(
                desk.accountant,
                desk.shift,
                "payment.record",
                {"delivery_id": delivery_id, "amount_toman": 60_000, "method": "cash"},
            )
        except PurchaseRuleError:
            return "refused"
        return "recorded"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result(timeout=30) for future in [pool.submit(pay) for _ in range(2)]]

    assert sorted(outcomes) == ["recorded", "refused"], "one payment lands and the other sees the new balance"
    assert len(_log(desk, EventType.PAYMENT_RECORDED)) == 1


# --- the owner's photo rule, the price history and the main store's own counts ---------------


def test_the_invoice_photo_is_mandatory_at_the_door_and_the_payment_receipt_photo_is_optional(desk: Desk) -> None:
    """Decision 6 (F1): a receipt with no invoice photo is refused; a payment needs none."""
    lines = [{"item_id": ITEM, "location_id": LOCATION, "quantity": 4}]
    with pytest.raises(PurchaseRuleError, match="عکس فاکتور"):
        _act(desk, desk.reception, "delivery.receive", invoice_reference="۱", lines=lines)
    with pytest.raises(PurchaseRuleError, match="ذخیره"):
        _act(desk, desk.reception, "delivery.receive", invoice_reference="۱", lines=lines, invoice_photo_sha256="../x")

    delivery_id = _digitized_delivery(desk)
    _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=40_000, method="cash")
    _act(
        desk,
        desk.accountant,
        "payment.record",
        delivery_id=delivery_id,
        amount_toman=60_000,
        method="transfer",
        receipt_photo_sha256="b" * 64,
    )

    invoice = _invoice(desk, delivery_id)
    assert invoice["invoice_photo"] == PHOTO
    assert invoice["receipt_photos"] == ["b" * 64]
    # Reception and the storekeeper see no price and no photo hash of the accountant's side.
    assert "invoice_photo" not in _state(desk, desk.reception)["deliveries"][0]


def test_the_price_history_lists_every_price_of_an_item_per_vendor_for_the_accountant_only(desk: Desk) -> None:
    """F2: a read of the log, oldest first, with the change from the price before."""
    _digitized_delivery(desk, unit_price=25_000)
    _digitized_delivery(desk, unit_price=27_500)

    history = _state(desk, desk.accountant)["price_history"]
    series = next(row for row in history if row["item_id"] == ITEM)
    assert series["vendor"] == "داروخانهٔ نمونه"
    assert [point["unit_price_toman"] for point in series["points"]] == [25_000, 27_500]
    assert (series["latest"], series["change"]) == (27_500, 2_500)
    assert _state(desk, desk.storekeeper)["price_history"] is None
    assert _state(desk, desk.reception)["price_history"] is None


def test_the_first_weekly_count_needs_the_storekeepers_opening_count_of_the_whole_store(desk: Desk) -> None:
    """B1, decision 1: on a stockless install the catalog still names every item the opening count covers."""
    with desk.database.write() as connection:
        connection.exec_driver_sql("DELETE FROM proj_stock")
    with pytest.raises(PurchaseRuleError, match="شمارش اولیه"):
        _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=3)

    items = [row["item_id"] for row in _state(desk, desk.storekeeper)["warehouse"]["items"]]
    counted = sorted(item.item_id for item in desk.catalog.items(desk.shift) if item.kind is ItemKind.COUNTED)
    assert items == counted, "with no stock row at all the list still comes from the catalog"
    assert {ITEM, SYRINGE} <= set(items)
    assert _state(desk, desk.storekeeper)["warehouse"]["baseline_done"] is False
    with pytest.raises(PurchaseRuleError, match="همهٔ اقلام"):
        _act(desk, desk.storekeeper, "store.baseline", lines=[{"item_id": ITEM, "quantity": 1}])
    with pytest.raises(PurchaseRuleError, match="انباردار"):
        _act(desk, desk.accountant, "store.baseline", lines=[{"item_id": item, "quantity": 1} for item in items])

    _act(desk, desk.storekeeper, "store.baseline", lines=[{"item_id": item, "quantity": 7} for item in items])

    assert _shelf(desk, SYRINGE, STORE) == 7
    assert _log(desk, EventType.STOCK_STORE_BASELINED)[0].actor_id == desk.storekeeper.staff_id
    assert _log(desk, EventType.DISCREPANCY_RECORDED) == [], "an opening count is compared with nothing"
    with pytest.raises(PurchaseRuleError, match="قبلاً"):
        _act(desk, desk.storekeeper, "store.baseline", lines=[{"item_id": item, "quantity": 7} for item in items])
    # The weekly count works on the fresh install too, on items the opening count has just filled.
    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=7)
    _act(desk, desk.storekeeper, "weekly.count", item_id=ITEM, location_id=STORE, counted=7)
    # The emptied projection above is a test device, so the log is replayed rather than compared to it:
    # a replay has to rebuild the store's figures from the records alone.
    desk.projections.rebuild()
    assert _shelf(desk, SYRINGE, STORE) == 7


def test_only_the_storekeeper_counts_the_week_and_the_accountants_sample_never_sets_the_stock(desk: Desk) -> None:
    """Decision 2: the accountant's count is a monthly blind check of five items; it cannot close the week."""
    with pytest.raises(PurchaseRuleError, match="انباردار"):
        _act(desk, desk.accountant, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=STORE_BEFORE)

    sample = _state(desk, desk.accountant)["sample"]
    picked = [row["item_id"] for row in sample["items"]]
    assert 1 <= len(picked) <= 5 and sample["month"].count("-") == 1
    assert _state(desk, desk.accountant)["sample"] == sample, "the month's pick is stable, never re-rolled"
    assert _state(desk, desk.storekeeper)["sample"] is None
    outside = (
        next(
            row["item_id"]
            for row in _state(desk, desk.storekeeper)["warehouse"]["items"]
            if row["item_id"] not in picked
        )
        if len(picked) < 2
        else None
    )
    if outside is not None:
        with pytest.raises(PurchaseRuleError, match="نمونه"):
            _act(desk, desk.accountant, "sample.count", item_id=outside, location_id=STORE, counted=1)

    before = _shelf(desk, picked[0], STORE)
    answer = _act(
        desk, desk.accountant, "sample.count", item_id=picked[0], location_id=STORE, counted=(before or 0) + 2
    )
    assert set(answer) == {"status", "event_id"}, "the answer carries no difference"
    assert _shelf(desk, picked[0], STORE) == before, "a sample compares and never sets"
    gap = _log(desk, EventType.DISCREPANCY_RECORDED)[-1].payload
    assert (gap["item_id"], gap["difference"], gap["culprit_id"]) == (picked[0], -2, None), "2 more is a surplus"
    with pytest.raises(PurchaseRuleError, match="شمرده"):
        _act(desk, desk.accountant, "sample.count", item_id=picked[0], location_id=STORE, counted=before)
    assert _state(desk, desk.accountant)["sample"]["items"][0]["done"] is True


def test_a_near_expiry_mark_is_stored_with_the_count_that_saw_it(desk: Desk) -> None:
    """B1: the mark rides on the item's count and is kept; the card reads it from the log."""
    _act(
        desk,
        desk.storekeeper,
        "weekly.count",
        item_id=SYRINGE,
        location_id=STORE,
        counted=STORE_BEFORE,
        near_expiry=True,
    )

    flagged = _log(desk, EventType.EXPIRY_FLAGGED)
    assert [(row.payload["item_id"], row.actor_id) for row in flagged] == [(SYRINGE, desk.storekeeper.staff_id)]


# --- the shelf return (A2) -------------------------------------------------------------------


def _return_offer(desk: Desk, **overrides: Any) -> dict[str, Any]:
    """Reception sends goods from its shelf back to the main store and names the storekeeper to count them."""
    data: dict[str, Any] = {
        "item_id": ITEM,
        "from_location_id": LOCATION,
        "to_location_id": STORE,
        "quantity": 3,
        "confirmer_id": desk.storekeeper.staff_id,
    }
    return _act(desk, desk.reception, "shelf_return.record", **(data | overrides))


def test_a_shelf_return_needs_both_sides_and_then_moves_the_shelf_down_and_the_store_up_once(desk: Desk) -> None:
    """A2, U in the shelf reference: the offer moves nothing; the storekeeper's count moves both ends once."""
    offered = _return_offer(desk)
    assert (_shelf(desk, ITEM, STORE), _shelf(desk, ITEM, LOCATION)) == (STORE_BEFORE, SHELF_BEFORE)
    waiting = _state(desk, desk.storekeeper)["inbox"]["return_to_confirm"]
    assert [row["offer_id"] for row in waiting] == [offered["event_id"]]
    assert "quantity" not in waiting[0], "the person counting in the store does not read the offered figure"
    assert [row["offer_id"] for row in _state(desk, desk.reception)["inbox"]["return_waiting"]] == [offered["event_id"]]

    with pytest.raises(PurchaseRuleError, match="کسی که"):
        _act(desk, desk.nurse, "shelf_return.confirm", offer_id=offered["event_id"], quantity=3)
    with pytest.raises(PurchaseRuleError, match="نمی‌خواند"):
        _act(desk, desk.storekeeper, "shelf_return.confirm", offer_id=offered["event_id"], quantity=2)
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE

    _act(desk, desk.storekeeper, "shelf_return.confirm", offer_id=offered["event_id"], quantity=3)
    with pytest.raises(PurchaseRuleError, match="قبلاً"):
        _act(desk, desk.storekeeper, "shelf_return.confirm", offer_id=offered["event_id"], quantity=3)

    assert (_shelf(desk, ITEM, STORE), _shelf(desk, ITEM, LOCATION)) == (STORE_BEFORE + 3, SHELF_BEFORE - 3)
    assert _state(desk, desk.storekeeper)["inbox"]["return_to_confirm"] == []
    assert desk.projections.rebuild().drift == ()
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE - 3


def test_a_shelf_return_is_refused_when_it_is_not_a_return(desk: Desk) -> None:
    with pytest.raises(PurchaseRuleError, match="کس دیگری"):
        _return_offer(desk, confirmer_id=desk.reception.staff_id)
    with pytest.raises(PurchaseRuleError, match="انباردار"):
        _return_offer(desk, confirmer_id=desk.nurse.staff_id)
    with pytest.raises(PurchaseRuleError, match="آن قفسه با اوست"):
        _act(
            desk,
            desk.nurse,
            "shelf_return.record",
            item_id=ITEM,
            from_location_id=LOCATION,
            to_location_id=STORE,
            quantity=3,
            confirmer_id=desk.storekeeper.staff_id,
        )
    with pytest.raises(PurchaseRuleError, match="فقط به انبار اصلی"):
        _return_offer(desk, to_location_id=LOCATION, from_location_id=STORE)
    with pytest.raises(PurchaseRuleError, match="آن قفسه با اوست"):
        _return_offer(desk, from_location_id="nowhere")
    with pytest.raises(PurchaseRuleError, match="کارکنان فعال"):
        _return_offer(desk, confirmer_id="staff-ghost")
    assert _log(desk, EventType.SHELF_RETURN_OFFERED) == []
    assert (_shelf(desk, ITEM, STORE), _shelf(desk, ITEM, LOCATION)) == (STORE_BEFORE, SHELF_BEFORE)


def test_the_shelf_the_projection_holds_is_what_the_h_01_formula_says_of_the_event_log(desk: Desk) -> None:
    """A3: one source for the reference. The projection is it, and `E = C0 + R - D - U - W` read off the
    real events (a charge, a return, an emergency withdrawal, waste, consumption and its reversal) agrees."""

    def record(event_type: EventType, key: str, quantity: int, **extra: Any) -> None:
        payload = {"item_id": ITEM, "location_id": LOCATION, "quantity": quantity, **extra}
        desk.store.append(
            NewEvent(
                client_event_id=new_ulid(),
                type=event_type.value,
                payload=payload,
                actor_id="accounting-bridge",
                device_id="accounting-bridge",
                work_date=date(2026, 10, 28),
                client_at=desk.clock.now_utc(),
                shift_id=desk.shift.text,
                idempotency_key=key,
            )
        )

    _charge(desk, quantity=7)
    offered = _return_offer(desk, quantity=2)
    _act(desk, desk.storekeeper, "shelf_return.confirm", offer_id=offered["event_id"], quantity=2)
    record(EventType.STOCK_EMERGENCY_WITHDRAWAL, "a3-emergency", 1)
    record(EventType.STOCK_WASTE_REPORTED, "a3-waste", 1)
    record(EventType.ACC_CONSUMPTION, "a3-consumption", 3)
    record(EventType.ACC_CONSUMPTION_REVERSAL, "a3-reversal", 1, reason="voided")

    def total(event_type: EventType, side: str = "to_location_id") -> int:
        return sum(
            int(event.payload["quantity"])
            for event in _log(desk, event_type)
            if event.payload.get(side, event.payload.get("location_id")) == LOCATION
        )

    expected = shelf_reference(
        last_count=SHELF_BEFORE,
        restocked=total(EventType.RESTOCK_CONFIRMED),
        consumed=total(EventType.ACC_CONSUMPTION) - total(EventType.ACC_CONSUMPTION_REVERSAL),
        emergency=total(EventType.STOCK_EMERGENCY_WITHDRAWAL)
        + total(EventType.SHELF_RETURN_CONFIRMED, side="from_location_id"),
        waste=total(EventType.STOCK_WASTE_REPORTED),
    )
    desk.projections.sync()
    assert _shelf(desk, ITEM, LOCATION) == expected == SHELF_BEFORE + 7 - 2 - 1 - 1 - 3 + 1


def test_the_return_form_offers_a_shelf_holder_their_own_shelves_and_the_storekeepers(desk: Desk) -> None:
    """A2: reception sees its shelf's items and the storekeeper to hand them to; the storekeeper gets no form."""
    view = _state(desk, desk.reception)["shelf_return"]
    assert view is not None
    assert {row["location_id"] for row in view["items"]} == {LOCATION}
    assert desk.storekeeper.staff_id in {person["staff_id"] for person in view["receivers"]}
    assert all("quantity" not in row for row in view["items"]), "the form carries no figure"
    assert _state(desk, desk.purchaser)["shelf_return"] is None


# --- an offer nobody confirmed (S-9) ---------------------------------------------------------


def test_the_giver_or_the_manager_withdraws_an_offer_nobody_confirmed_and_it_moves_nothing(desk: Desk) -> None:
    """A wrong figure or name cannot be fixed by the confirmer, so the offer is taken back and made again."""
    charge = _act(
        desk,
        desk.storekeeper,
        "restock.record",
        item_id=ITEM,
        from_location_id=STORE,
        to_location_id=LOCATION,
        quantity=5,
        confirmer_id=desk.nurse.staff_id,
    )
    giving_back = _return_offer(desk)

    with pytest.raises(PurchaseRuleError, match="پس بگیرد"):
        _act(desk, desk.nurse, "offer.withdraw", offer_id=charge["event_id"])
    with pytest.raises(PurchaseRuleError, match="پیدا نشد"):
        _act(desk, desk.storekeeper, "offer.withdraw", offer_id="no-such-offer")

    _act(desk, desk.storekeeper, "offer.withdraw", offer_id=charge["event_id"])
    _act(desk, desk.manager, "offer.withdraw", offer_id=giving_back["event_id"])

    for person, key in ((desk.nurse, "restock_to_confirm"), (desk.storekeeper, "return_to_confirm")):
        assert _state(desk, person)["inbox"][key] == []
    assert _state(desk, desk.storekeeper)["inbox"]["restock_waiting"] == []
    assert _state(desk, desk.reception)["inbox"]["return_waiting"] == []
    with pytest.raises(PurchaseRuleError, match="قبلاً"):
        _act(desk, desk.nurse, "restock.confirm", offer_id=charge["event_id"], quantity=5)
    with pytest.raises(PurchaseRuleError, match="قبلاً"):
        _act(desk, desk.storekeeper, "shelf_return.confirm", offer_id=giving_back["event_id"], quantity=3)
    with pytest.raises(PurchaseRuleError, match="قبلاً"):
        _act(desk, desk.storekeeper, "offer.withdraw", offer_id=charge["event_id"])

    assert (_shelf(desk, ITEM, STORE), _shelf(desk, ITEM, LOCATION)) == (STORE_BEFORE, SHELF_BEFORE)
    assert desk.projections.rebuild().drift == ()


def test_a_confirmed_offer_cannot_be_withdrawn(desk: Desk) -> None:
    offered = _return_offer(desk)
    _act(desk, desk.storekeeper, "shelf_return.confirm", offer_id=offered["event_id"], quantity=3)
    with pytest.raises(PurchaseRuleError, match="قبلاً"):
        _act(desk, desk.reception, "offer.withdraw", offer_id=offered["event_id"])
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE - 3


def test_the_manager_does_not_give_back_in_place_of_a_shelf_holder(desk: Desk) -> None:
    """The manager's phone shows manager work only: a return is recorded by whoever answers for the shelf."""
    assert desk.service.state(desk.manager, desk.shift).to_payload()["shelf_return"] is None
    with pytest.raises(PurchaseRuleError, match="قفسه"):
        _act(
            desk,
            desk.manager,
            "shelf_return.record",
            item_id=ITEM,
            from_location_id=LOCATION,
            to_location_id=STORE,
            quantity=2,
            confirmer_id=desk.storekeeper.staff_id,
        )
