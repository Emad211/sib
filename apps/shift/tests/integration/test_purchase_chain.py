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

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, FixedClock, Seeded, create_staff
from seed_reference_data import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.event_types import EventType
from shift.domain.permissions import permissions_for
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, ItemKind
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission
from shift.services.projections import ProjectionEngine
from shift.services.purchase import PurchaseRuleError, PurchaseService
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventService

# The clinic clock the tests move by hand; the shift is the one every catalog change is visible from.
FIXED_NOW = datetime(2026, 10, 28, 6, 30, tzinfo=UTC)
SHIFT = ShiftKey(date(2026, 10, 28), 2)
# The synthetic catalog: paracetamol on the reception shelf and the main store, a counted item that
# lives in the main store, and the store's own location (S03 §1, tools/seed_reference_data.py).
ITEM = "para_500_amp"
# A second shelf item the store never held: a charge is what leaves a store row behind for it (P-10).
OTHER = "diclofenac_75_amp"
LOCATION = "shelf_reception"
STORE = "main_store"
SYRINGE = "syringe_5cc"
SHELF_BEFORE = 3
STORE_BEFORE = 12


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
    for item, location, counted in (
        (ITEM, STORE, STORE_BEFORE),
        (ITEM, LOCATION, SHELF_BEFORE),
        (SYRINGE, STORE, STORE_BEFORE),
    ):
        store.append(
            build_event(
                EventType.STOCK_WEEKLY_COUNT.value,
                payload={"item_id": item, "location_id": location, "counted": counted},
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


def test_a_parcel_goes_to_the_storekeeper_and_only_a_storekeeper_takes_custody(desk: Desk) -> None:
    """P-12: the parcel is handed on by its real delivery id, and custody names a storekeeper."""
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
        to_staff_id=desk.storekeeper.staff_id,
        to_kind="storekeeper",
        packages=1,
    )

    assert desk.service.pending(desk.shift).undelivered == ()
    assert _state(desk, desk.reception)["deliveries"][0]["custody"] == "storekeeper"


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
    assert (invoice["returned"], invoice["credit_total"], invoice["remaining"]) == (True, 25_000, 100_000)
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE, "a return is a purchasing document (P-09)"
    with pytest.raises(PurchaseRuleError, match="مرجوع"):
        _act(desk, desk.accountant, "payment.record", delivery_id=delivery_id, amount_toman=1_000, method="cash")


# --- the shelf charge -----------------------------------------------------------------------


def test_a_shelf_charge_moves_the_store_down_and_the_shelf_up_once(desk: Desk) -> None:
    """P-10: one record, both ends; the rebuild has to land on the same shelves as the live run."""
    _act(
        desk,
        desk.storekeeper,
        "restock.record",
        item_id=ITEM,
        from_location_id=STORE,
        to_location_id=LOCATION,
        quantity=5,
    )

    assert (_shelf(desk, ITEM, STORE), _shelf(desk, ITEM, LOCATION)) == (
        STORE_BEFORE - 5,
        SHELF_BEFORE + 5,
    )
    report = desk.projections.rebuild()
    assert report.applied
    assert report.drift == ()
    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE + 5


def test_a_charge_to_the_same_place_or_into_a_shelf_without_the_item_is_refused(desk: Desk) -> None:
    """P-10: the two ends must be two places, and the item has to belong to the shelf it lands on."""
    with pytest.raises(PurchaseRuleError, match="یکی"):
        _act(
            desk,
            desk.storekeeper,
            "restock.record",
            item_id=ITEM,
            from_location_id=LOCATION,
            to_location_id=LOCATION,
            quantity=1,
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
        )

    assert _shelf(desk, ITEM, LOCATION) == SHELF_BEFORE


def test_the_charge_names_the_person_who_confirmed_the_count(desk: Desk) -> None:
    """P-10: the shift lead who counted is the confirmer, and it must be somebody the clinic still has."""
    _act(
        desk,
        desk.storekeeper,
        "restock.record",
        item_id=ITEM,
        from_location_id=STORE,
        to_location_id=LOCATION,
        quantity=1,
        confirmer_id=desk.nurse.staff_id,
    )
    with pytest.raises(PurchaseRuleError, match="تأییدکننده"):
        _act(
            desk,
            desk.storekeeper,
            "restock.record",
            item_id=ITEM,
            from_location_id=STORE,
            to_location_id=LOCATION,
            quantity=1,
            confirmer_id="staff-ghost",
        )
    _act(
        desk,
        desk.storekeeper,
        "restock.record",
        item_id=ITEM,
        from_location_id=STORE,
        to_location_id=LOCATION,
        quantity=1,
    )

    recorded = [event.payload["confirmer_id"] for event in _log(desk, EventType.RESTOCK_CONFIRMED)]
    assert recorded == [desk.nurse.staff_id, desk.storekeeper.staff_id], "named first, then the caller"


# --- the weekly count -----------------------------------------------------------------------


def test_a_weekly_count_writes_an_unnamed_discrepancy_and_an_expiry_flag(desk: Desk) -> None:
    """P-11: the gap is `counted − expected`, it carries no culprit, and it hangs off the count."""
    answer = _act(
        desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=9, near_expiry=True
    )

    count = _log(desk, EventType.STOCK_WEEKLY_COUNT)[-1]
    gap = _log(desk, EventType.DISCREPANCY_RECORDED)[-1]
    flag = _log(desk, EventType.EXPIRY_FLAGGED)[-1]
    assert answer == {"status": "recorded", "event_id": count.event_id}, "a blind count answers no figure"
    assert (gap.payload["scope"], gap.payload["item_id"], gap.payload["location_id"]) == ("item", SYRINGE, STORE)
    assert (gap.payload["difference"], gap.payload["culprit_id"]) == (-3, None), "counted fewer than expected"
    assert gap.ref_event_id == count.event_id
    assert flag.ref_event_id == count.event_id
    assert _shelf(desk, SYRINGE, STORE) == 9


def test_a_count_that_matches_writes_no_discrepancy(desk: Desk) -> None:
    """A count equal to the store's own figure is a count and nothing else: no gap, no expiry flag."""
    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=STORE_BEFORE)

    assert _log(desk, EventType.DISCREPANCY_RECORDED) == []
    assert _log(desk, EventType.EXPIRY_FLAGGED) == []


def test_the_weekly_status_turns_done_once_the_window_has_a_count(desk: Desk) -> None:
    """A count closes the week it lands in — even a late one — and the next window opens due again."""
    state = _state(desk, desk.storekeeper)["warehouse"]["weekly"]
    assert state["state"] == "done", "the desk's own store counts settle the week they landed in"

    desk.clock.now = datetime(2026, 11, 2, 6, 30, tzinfo=UTC)  # Monday 10:00 in Tehran
    weekly = _state(desk, desk.storekeeper)["warehouse"]["weekly"]
    assert weekly["state"] == "overdue", "the new window is uncounted and its 07:30 deadline has passed"

    _act(desk, desk.storekeeper, "weekly.count", item_id=SYRINGE, location_id=STORE, counted=STORE_BEFORE)

    weekly = _state(desk, desk.storekeeper)["warehouse"]["weekly"]
    assert weekly["state"] == "done"
    assert (weekly["window_start"], weekly["window_end"]) == ("2026-10-31", "2026-11-02")


def test_a_count_of_an_item_that_is_not_counted_there_is_refused(desk: Desk) -> None:
    with pytest.raises(PurchaseRuleError, match="شمارشی"):
        _act(desk, desk.storekeeper, "weekly.count", item_id="store_lock_ok", location_id=STORE, counted=1)


def test_a_shelf_count_never_marks_the_week_done(desk: Desk) -> None:
    """The weekly status is about the main store, so a shelf count is not a weekly count at all."""
    with pytest.raises(PurchaseRuleError, match="انبار اصلی"):
        _act(desk, desk.storekeeper, "weekly.count", item_id=ITEM, location_id=LOCATION, counted=SHELF_BEFORE)

    assert _log(desk, EventType.STOCK_WEEKLY_COUNT)[-1].payload["location_id"] == STORE, "only the store counts land"


def test_the_store_list_holds_what_the_store_holds(desk: Desk) -> None:
    """A shelf charge leaves the item at the store, so the storekeeper can count and re-charge it."""
    before = {row["item_id"] for row in _state(desk, desk.storekeeper)["warehouse"]["items"]}
    _act(
        desk,
        desk.storekeeper,
        "restock.record",
        item_id=OTHER,
        from_location_id=STORE,
        to_location_id=LOCATION,
        quantity=2,
    )
    after = {row["item_id"] for row in _state(desk, desk.storekeeper)["warehouse"]["items"]}

    assert SYRINGE in before, "a counted item whose catalog home is the store"
    assert OTHER not in before, "its catalog home is the reception shelf, and the store never held it"
    assert OTHER in after
    assert {row["location_id"] for row in _state(desk, desk.storekeeper)["warehouse"]["items"]} == {STORE}


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
    assert (purchaser["orders"], purchaser["invoices"], purchaser["warehouse"]) == ([], [], None)


def test_the_warehouse_section_carries_no_projected_figure(desk: Desk) -> None:
    """P-11: the page the counter opens must not hand him the number he is counting blind."""
    warehouse = _state(desk, desk.storekeeper)["warehouse"]

    assert warehouse["items"]
    assert all(set(item) == {"item_id", "name", "location_id", "unit", "minimum"} for item in warehouse["items"])
    assert set(warehouse) == {"items", "locations", "weekly", "restock"}


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
    assert pending.weekly.state in {"done", "due", "overdue"}


def test_the_card_carries_the_waiting_order_the_locked_parcel_and_the_warehouse_work(desk: Desk) -> None:
    """M-14..M-16: the manager's stored card is where the chain's decisions and gaps actually land."""
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

    assert desk.service.in_transit(desk.shift) == ()


def test_the_chain_never_moves_the_cash_box(desk: Desk) -> None:
    """P-08 and S03 §5: invoices, payments and credits are accounting figures, not box movements."""
    before = _cash_rows(desk)
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
        with pytest.raises(PurchaseRuleError, match="انباردار"):
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
        )
