"""Cash withdrawal «د» and waste «ض»: the two kinds that move money and stock (SHF-3, S01 §3).

Both are existing event types the phone now records. A bug in either costs the clinic money — a
withdrawal above the cap without a manager, a waste nobody but the culprit confirmed, a replay that
writes twice — so these drive the real `ShiftEventService` on a migrated synthetic database, read the
projection the record leaves behind, and then rebuild the whole log and prove it lands on the same
figures.

The catalog, the roster and the settings are synthetic: `seed_reference_data` (placeholder items and
shelves), `auth_fixtures` (made-up staff) and the cap migration 0004 seeds. No production database and
no patient data are read (ADR-006).
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from auth_fixtures import ROLES, SPARE, seed_roster
from clocks import FixedClock, inside_shift
from seed_reference_data import seed as seed_catalog

from event_fixtures import build_event
from shift.adapters.db import events_repo, projections_repo
from shift.adapters.db.core import Database
from shift.common.ids import new_ulid
from shift.domain.permissions import permissions_for
from shift.domain.policy import Role, ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService
from shift.services.event_store import EventStore
from shift.services.outbox import EventSubmission, SubmissionResult
from shift.services.projections import ProjectionEngine
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventRuleError, ShiftEventService

WORK_DATE = date(2026, 10, 28)
SHIFT = ShiftKey(WORK_DATE, 2)
CLIENT_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
# The cap migration 0004 seeds; a changed figure makes the over-cap test below fail on purpose.
SEEDED_CAP = 300_000
OPENING_CASH = 1_000_000
# A synthetic shelf: paracetamol on reception's shelf, and a room that never holds it.
ITEM = "para_500_amp"
SHELF = "shelf_reception"
OTHER_SHELF = "cpr_room"
SHELF_QUANTITY = 10


def _principal(role: str) -> Principal:
    """The signed-in person of the synthetic roster, with the union of their roles' powers."""
    person = ROLES[role]
    return Principal(
        staff_id=person.staff_id,
        full_name=person.full_name,
        roles=person.roles,
        permissions=permissions_for(person.roles),
        device_id=person.device_id,
        device_label="گوشی آزمایشی",
        session_hash="synthetic",
        csrf_token="synthetic-csrf",
        expires_at=datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
    )


def _submission(
    event_type: str,
    payload: dict[str, Any],
    principal: Principal,
    *,
    key: str | None = None,
) -> EventSubmission:
    """One event as the phone's outbox submits it: its own ULID and the shift it belongs to."""
    event_id = new_ulid(CLIENT_AT)
    return EventSubmission(
        type=event_type,
        payload=payload,
        actor_id=principal.staff_id,
        device_id=principal.device_id,
        client_at=CLIENT_AT,
        client_event_id=event_id,
        shift_id=SHIFT.text,
        idempotency_key=key or event_id,
        shift_work_date=SHIFT.work_date,
    )


class Bench:
    """The real services on one synthetic database, plus the few reads the assertions need."""

    def __init__(self, database: Database) -> None:
        self.database = database
        clock = FixedClock(inside_shift(WORK_DATE, 3))
        self.store = EventStore(database)
        self.projections = ProjectionEngine(database)
        self.catalog = CatalogService(database, clock=clock)
        self.settings = SettingsService(database, clock=clock)
        self.service = ShiftEventService(database, self.store, self.projections, self.catalog, self.settings)

    def record(
        self, event_type: str, payload: dict[str, Any], principal: Principal, *, key: str | None = None
    ) -> SubmissionResult:
        """Record one phone event through the service, exactly as the route does."""
        return self.service.record(
            _submission(event_type, payload, principal, key=key), principal=principal, shift=SHIFT
        )

    def seed(self) -> None:
        """Synthetic roster and catalog, the manager's opening baseline, and the manager's role."""
        seed_roster(self.database)
        seed_catalog(self.database)
        # The cap rule reads the manager's role from the versioned catalog, which only a manager grant
        # fills; a grant recorded during the morning is effective from the evening shift under test.
        self.catalog.set_role(
            ROLES["manager"].staff_id,
            Role.MANAGER,
            granted=True,
            actor_id=ROLES["manager"].staff_id,
            running_shift=ShiftKey(WORK_DATE, 1),
        )
        # SHF-7.1's baseline is the one event that seeds the box and the shelf a handover compares to.
        manager = _principal("manager")
        self.store.append(
            build_event(
                "handover.baseline_recorded",
                payload={
                    "flow": "reception",
                    "custodian_id": manager.staff_id,
                    "manager_id": manager.staff_id,
                    "opening_cash": OPENING_CASH,
                    "items": [{"item_id": ITEM, "location_id": SHELF, "quantity": SHELF_QUANTITY}],
                },
                shift_id=SHIFT.text,
                actor_id=manager.staff_id,
                work_date=WORK_DATE,
            )
        )
        self.projections.sync()

    def cash(self) -> dict[str, Any]:
        """The reception box as the projection shows it."""
        with self.database.read() as connection:
            rows = projections_repo.cash_rows(connection)
        assert len(rows) == 1, "the clinic runs one cash box (D2)"
        return rows[0]

    def quantity(self, item_id: str = ITEM, location_id: str = SHELF) -> int:
        """One shelf's projected quantity."""
        with self.database.read() as connection:
            rows = {(row["item_id"], row["location_id"]): row["qty"] for row in projections_repo.stock_rows(connection)}
        return rows[(item_id, location_id)]

    def event_types(self) -> list[str]:
        """Every type in the log, in append order."""
        with self.database.read() as connection:
            return [event.type for event in events_repo.iter_events(connection)]


@pytest.fixture()
def bench(database: Database) -> Bench:
    """A migrated synthetic database with the roster, the catalog, the baseline and its grant."""
    seeded = Bench(database)
    seeded.seed()
    return seeded


def _withdrawal(**overrides: Any) -> dict[str, Any]:
    """A complete withdrawal: a real recipient, a reason, and no manager at or below the cap."""
    payload: dict[str, Any] = {
        "amount": 100_000,
        "recipient_id": ROLES["nurse"].staff_id,
        "reason": "خرید فوری",
        "manager_id": None,
    }
    payload.update(overrides)
    return payload


def _waste(**overrides: Any) -> dict[str, Any]:
    """A complete waste: the culprit and a second person who is neither the culprit nor the recorder."""
    payload: dict[str, Any] = {
        "item_id": ITEM,
        "location_id": SHELF,
        "quantity": 2,
        "waste_kind": "broken",
        "culprit_id": SPARE.staff_id,
        "confirmer_id": ROLES["nurse"].staff_id,
    }
    payload.update(overrides)
    return payload


# --- cash withdrawal «د» ------------------------------------------------------------------------


def test_a_withdrawal_at_the_cap_is_recorded_and_lowers_the_box(bench: Bench) -> None:
    """E-04: at the cap the recipient's own record carries it; the box loses exactly the amount."""
    reception = _principal("reception")

    result = bench.record("cash.withdrawn", _withdrawal(amount=SEEDED_CAP), reception)

    assert result.created is True
    assert bench.cash()["expected"] == OPENING_CASH - SEEDED_CAP
    assert bench.event_types()[-1] == "cash.withdrawn"


def test_a_withdrawal_above_the_cap_without_a_manager_is_refused(bench: Bench) -> None:
    """E-04: one Toman over the cap without the manager's name never reaches the log."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError, match="بالای سقف"):
        bench.record("cash.withdrawn", _withdrawal(amount=SEEDED_CAP + 1), reception)

    assert bench.cash()["expected"] == OPENING_CASH


def test_a_withdrawal_above_the_cap_with_the_manager_is_recorded(bench: Bench) -> None:
    """The manager's name turns the same over-cap withdrawal into a record the card can show."""
    reception = _principal("reception")

    result = bench.record(
        "cash.withdrawn",
        _withdrawal(amount=SEEDED_CAP + 1, manager_id=ROLES["manager"].staff_id),
        reception,
    )

    assert result.created is True
    assert bench.cash()["expected"] == OPENING_CASH - SEEDED_CAP - 1


def test_a_manager_id_without_the_manager_role_is_refused(bench: Bench) -> None:
    """A name is not an approval: the person named above the cap must hold the manager role."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError, match="بالای سقف"):
        bench.record(
            "cash.withdrawn",
            _withdrawal(amount=SEEDED_CAP + 1, manager_id=ROLES["storekeeper"].staff_id),
            reception,
        )

    assert bench.cash()["expected"] == OPENING_CASH


def test_a_withdrawal_at_the_cap_carries_no_decorative_manager(bench: Bench) -> None:
    """Below the cap a manager's name would read as an approval that never happened."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError, match="نیازی به تأیید مدیر ندارد"):
        bench.record("cash.withdrawn", _withdrawal(manager_id=ROLES["manager"].staff_id), reception)

    assert bench.cash()["expected"] == OPENING_CASH


@pytest.mark.parametrize(
    "payload",
    [
        _withdrawal(recipient_id=None),
        _withdrawal(reason=None),
        _withdrawal(recipient_id="staff-nowhere"),
        _withdrawal(amount=0),
    ],
    ids=["no-recipient", "no-reason", "unknown-recipient", "zero-amount"],
)
def test_an_incomplete_withdrawal_is_refused(bench: Bench, payload: dict[str, Any]) -> None:
    """An anonymous or empty withdrawal is the one record nobody could be held to (E-04)."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError):
        bench.record("cash.withdrawn", payload, reception)

    assert bench.cash()["expected"] == OPENING_CASH


def test_a_fraction_of_a_toman_is_not_a_withdrawal(bench: Bench) -> None:
    """The amount is whole Toman (S03 §1); the payload model refuses anything else."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError):
        bench.record("cash.withdrawn", _withdrawal(amount=100_000.5), reception)

    assert bench.cash()["expected"] == OPENING_CASH


def test_a_replayed_withdrawal_writes_once(bench: Bench) -> None:
    """ADR-016: the phone retries a queued row; the same idempotency key returns the first seq."""
    reception = _principal("reception")
    submission = _submission("cash.withdrawn", _withdrawal(), reception, key="phone-1:withdrawal-1")

    first = bench.service.record(submission, principal=reception, shift=SHIFT)
    second = bench.service.record(submission, principal=reception, shift=SHIFT)

    assert (first.created, second.created) == (True, False)
    assert second.seq == first.seq
    assert bench.cash()["expected"] == OPENING_CASH - 100_000
    assert bench.event_types().count("cash.withdrawn") == 1


# --- waste «ض» ----------------------------------------------------------------------------------


def test_a_confirmed_waste_is_recorded_and_lowers_the_shelf(bench: Bench) -> None:
    """E-05: the item leaves the reference quantity at once; the second person closes it afterwards."""
    reception = _principal("reception")

    result = bench.record("stock.waste_reported", _waste(quantity=2), reception)

    assert result.created is True
    assert bench.quantity() == SHELF_QUANTITY - 2
    assert bench.service.pending_for(_principal("storekeeper")) == [], "a waste waits for its named person"
    assert bench.event_types()[-1] == "stock.waste_reported"


def test_the_culprit_cannot_confirm_their_own_waste(bench: Bench) -> None:
    """Owner document: «مقصر نمی‌تواند تأییدکننده‌ی دوم همان ثبت باشد» — the second person is a second."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError, match="مقصر"):
        bench.record("stock.waste_reported", _waste(confirmer_id=SPARE.staff_id), reception)

    assert bench.quantity() == SHELF_QUANTITY


def test_the_recorder_cannot_be_the_confirmer(bench: Bench) -> None:
    """The recorder already counts as one signature; confirming their own report adds none."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError, match="ثبت‌کننده"):
        bench.record("stock.waste_reported", _waste(confirmer_id=reception.staff_id), reception)

    assert bench.quantity() == SHELF_QUANTITY


def test_an_item_that_is_not_at_that_location_is_refused(bench: Bench) -> None:
    """S03 §1: a shelf holds the items the catalog puts there; waste on another shelf is a typo."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError, match="محل"):
        bench.record("stock.waste_reported", _waste(location_id=OTHER_SHELF), reception)

    assert bench.quantity() == SHELF_QUANTITY


@pytest.mark.parametrize(
    "payload",
    [
        _waste(quantity=0),
        _waste(waste_kind=None),
        _waste(culprit_id=None),
        _waste(confirmer_id=None),
        _waste(culprit_id="staff-nowhere"),
        _waste(confirmer_id="staff-nowhere"),
    ],
    ids=["zero-quantity", "no-kind", "no-culprit", "no-confirmer", "unknown-culprit", "unknown-confirmer"],
)
def test_an_incomplete_waste_is_refused(bench: Bench, payload: dict[str, Any]) -> None:
    """A waste with no quantity, no kind or no two named people could not be held to anyone."""
    reception = _principal("reception")

    with pytest.raises(ShiftEventRuleError):
        bench.record("stock.waste_reported", payload, reception)

    assert bench.quantity() == SHELF_QUANTITY


def test_a_replayed_waste_writes_once(bench: Bench) -> None:
    """A queued waste replayed after the reconnection takes one item's quantity, not two."""
    reception = _principal("reception")
    submission = _submission("stock.waste_reported", _waste(quantity=3), reception, key="phone-1:waste-1")

    first = bench.service.record(submission, principal=reception, shift=SHIFT)
    second = bench.service.record(submission, principal=reception, shift=SHIFT)

    assert (first.created, second.created) == (True, False)
    assert bench.quantity() == SHELF_QUANTITY - 3
    assert bench.event_types().count("stock.waste_reported") == 1


# --- both kinds: the log stays the only truth -----------------------------------------------------


def test_a_rebuild_reproduces_both_projections_including_the_old_rows(bench: Bench) -> None:
    """Every projection must be rebuildable, and an old row without the new fields must still replay."""
    reception = _principal("reception")
    # The cap is the shift's running total (ADR-019): both withdrawals together stay just under it.
    bench.record("cash.withdrawn", _withdrawal(amount=SEEDED_CAP - 100_000), reception)
    bench.record("stock.waste_reported", _waste(quantity=2), reception, key="phone-1:waste-2")
    bench.record("cash.withdrawn", _withdrawal(amount=50_000), reception, key="phone-1:withdrawal-2")
    # Rows stored before SHF-3 carry neither the recipient nor the confirmer; they are history and replay.
    bench.store.append_many(
        [
            build_event("cash.withdrawn", payload={"amount": 25_000}, shift_id=SHIFT.text, work_date=WORK_DATE),
            build_event(
                "stock.waste_reported",
                payload={"item_id": ITEM, "location_id": SHELF, "quantity": 1},
                shift_id=SHIFT.text,
                work_date=WORK_DATE,
            ),
        ]
    )
    bench.projections.sync()
    live = (bench.cash(), bench.quantity())

    report = bench.projections.rebuild()

    assert report.drift == ()
    assert (bench.cash(), bench.quantity()) == live
    assert live[0]["expected"] == OPENING_CASH - (SEEDED_CAP - 100_000) - 50_000 - 25_000
    assert live[1] == SHELF_QUANTITY - 3
