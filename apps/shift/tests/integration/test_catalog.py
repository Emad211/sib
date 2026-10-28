"""The catalog is versioned per shift: locations, items and staff roles (R-08, R-09, step 11)."""

from datetime import date

import pytest

from shift.adapters.db.core import Database
from shift.domain.policy import PolicyError, Role, ShiftKey
from shift.services.catalog import CatalogError, CatalogService, ItemKind

DAY = date(2026, 10, 28)
MORNING = ShiftKey(DAY, 1)
EVENING = ShiftKey(DAY, 2)
NIGHT = ShiftKey(DAY, 3)


@pytest.fixture
def service(database: Database) -> CatalogService:
    return CatalogService(database)


def add_location(
    service: CatalogService,
    location_id: str = "shelf_reception",
    *,
    name: str = "قفسهٔ پذیرش",
    retired: bool = False,
    running_shift: ShiftKey | None = None,
) -> None:
    service.save_location(
        location_id, name=name, retired=retired, actor_id="staff-manager", running_shift=running_shift
    )


def save_item(service: CatalogService, item_id: str = "para_500_amp", **overrides: object) -> None:
    fields: dict[str, object] = {
        "name": "پاراستامول ۵۰۰ میلی‌گرم",
        "kind": ItemKind.COUNTED,
        "location_id": "shelf_reception",
        "unit": "آمپول",
        "minimum": 10,
        "acc_code": "10001",
        "stock_factor": 1,
        "actor_id": "staff-manager",
    }
    fields.update(overrides)
    service.save_item(item_id, **fields)  # type: ignore[arg-type]


def test_the_catalog_starts_empty(service: CatalogService) -> None:
    assert service.locations() == []
    assert service.items() == []
    assert service.staff_roles("staff-1") == []


def test_a_new_location_is_effective_from_the_next_shift(service: CatalogService) -> None:
    add_location(service, running_shift=MORNING)

    assert service.locations(MORNING) == []
    [location] = service.locations(EVENING)
    assert location.location_id == "shelf_reception"
    assert location.name == "قفسهٔ پذیرش"
    assert location.retired is False
    assert location.version == 1
    assert location.effective_from_shift == EVENING


def test_retiring_a_location_leaves_the_running_shift_using_it(service: CatalogService) -> None:
    add_location(service)

    add_location(service, retired=True, running_shift=MORNING)

    assert [location.retired for location in service.locations(MORNING)] == [False]
    assert service.locations(EVENING) == []
    assert [location.retired for location in service.locations(EVENING, include_retired=True)] == [True]


def test_renaming_a_location_appends_a_version(service: CatalogService) -> None:
    add_location(service, name="قفسهٔ پذیرش")
    add_location(service, name="قفسهٔ پذیرش ۲", running_shift=MORNING)

    assert service.locations(MORNING)[0].name == "قفسهٔ پذیرش"
    assert service.locations(EVENING)[0].name == "قفسهٔ پذیرش ۲"
    assert service.locations(EVENING)[0].version == 2


def test_saving_the_same_location_again_adds_no_version(service: CatalogService) -> None:
    add_location(service)
    add_location(service, running_shift=EVENING)

    assert service.locations(NIGHT)[0].version == 1


@pytest.mark.parametrize("location_id", ["Shelf Reception", "", "1shelf", "قفسه", "shelf reception"])
def test_a_location_id_must_be_a_slug(service: CatalogService, location_id: str) -> None:
    with pytest.raises(CatalogError):
        add_location(service, location_id)


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_a_location_needs_a_usable_name(service: CatalogService, name: str) -> None:
    with pytest.raises(CatalogError):
        add_location(service, name=name)


def test_a_counted_item_needs_an_accounting_code(service: CatalogService) -> None:
    add_location(service)

    with pytest.raises(CatalogError, match="acc_code"):
        save_item(service, acc_code=None)


def test_a_checklist_item_needs_no_accounting_code(service: CatalogService) -> None:
    add_location(service)

    save_item(service, "store_lock_ok", kind=ItemKind.YES_NO, unit="وضعیت", acc_code=None, minimum=0)

    assert service.items()[0].acc_code is None


def test_an_item_kind_must_be_known(service: CatalogService) -> None:
    add_location(service)

    with pytest.raises(CatalogError, match="kind"):
        save_item(service, kind="weight")


def test_an_item_needs_a_location_that_is_in_use(service: CatalogService) -> None:
    with pytest.raises(CatalogError, match="location"):
        save_item(service)

    add_location(service)
    add_location(service, retired=True)
    with pytest.raises(CatalogError, match="location"):
        save_item(service, running_shift=MORNING)


def test_a_location_and_its_item_can_be_created_in_the_same_shift(service: CatalogService) -> None:
    add_location(service, running_shift=MORNING)
    save_item(service, running_shift=MORNING)

    assert service.items(MORNING) == []
    assert [item.item_id for item in service.items(EVENING)] == ["para_500_amp"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"stock_factor": 0}, "stock_factor"),
        ({"stock_factor": 1.5}, "stock_factor"),
        ({"minimum": "ten"}, "minimum"),
        ({"minimum": -1}, "minimum"),
        ({"unit": ""}, "unit"),
        ({"name": "x" * 81}, "name"),
        ({"item_id": "Para 500"}, "item_id"),
    ],
)
def test_item_fields_are_validated(service: CatalogService, overrides: dict[str, object], message: str) -> None:
    add_location(service)

    with pytest.raises(CatalogError, match=message):
        save_item(service, **overrides)


def test_an_accounting_code_belongs_to_one_item(service: CatalogService) -> None:
    add_location(service)
    save_item(service)

    with pytest.raises(CatalogError, match="10001"):
        save_item(service, "para_125_syrup", acc_code="10001")


def test_an_accounting_code_is_never_handed_to_a_second_item(service: CatalogService) -> None:
    add_location(service)
    save_item(service, retired=True)

    with pytest.raises(CatalogError, match="10001"):
        save_item(service, "para_125_syrup", acc_code="10001")


def test_a_retired_item_can_be_revived(service: CatalogService) -> None:
    add_location(service)
    save_item(service, retired=True)
    revived = service.save_item(
        "para_500_amp",
        name="پاراستامول ۵۰۰ میلی‌گرم",
        kind=ItemKind.COUNTED,
        location_id="shelf_reception",
        unit="آمپول",
        minimum=10,
        acc_code="10001",
        retired=False,
        actor_id="staff-manager",
        running_shift=MORNING,
    )

    assert revived.value.version == 2
    assert service.items(MORNING) == []
    assert [item.retired for item in service.items(MORNING, include_retired=True)] == [True]
    assert [item.retired for item in service.items(EVENING)] == [False]


def test_an_item_can_be_retired_after_its_location(service: CatalogService) -> None:
    add_location(service)
    save_item(service)
    add_location(service, retired=True)

    retired = service.save_item(
        "para_500_amp",
        name="پاراستامول ۵۰۰ میلی‌گرم",
        kind=ItemKind.COUNTED,
        location_id="shelf_reception",
        unit="آمپول",
        minimum=10,
        acc_code="10001",
        retired=True,
        actor_id="staff-manager",
        running_shift=MORNING,
    )

    assert retired.created is True
    assert service.items(EVENING) == []


def test_items_can_be_filtered_by_location(service: CatalogService) -> None:
    add_location(service)
    add_location(service, "cpr_room", name="اتاق CPR")
    save_item(service)
    save_item(service, "oxygen_mask", location_id="cpr_room", acc_code="10004")

    assert [item.item_id for item in service.items(location_id="cpr_room")] == ["oxygen_mask"]
    assert len(service.items()) == 2


def test_the_location_filter_follows_an_item_that_moved(service: CatalogService) -> None:
    add_location(service)
    add_location(service, "cpr_room", name="اتاق CPR")
    save_item(service)
    save_item(service, location_id="cpr_room", running_shift=MORNING)

    assert service.items(MORNING, location_id="shelf_reception")[0].location_id == "shelf_reception"
    assert service.items(EVENING, location_id="shelf_reception") == []
    assert service.items(EVENING, location_id="cpr_room")[0].location_id == "cpr_room"


def test_a_role_grant_is_effective_from_the_next_shift(service: CatalogService) -> None:
    grant = service.set_role("staff-1", Role.NURSE, granted=True, actor_id="staff-manager", running_shift=MORNING)

    assert grant.created is True
    assert grant.value.version == 1
    assert grant.value.effective_from_shift == EVENING
    assert service.roles_of("staff-1", MORNING) == frozenset()
    assert service.roles_of("staff-1", EVENING) == frozenset({Role.NURSE})


def test_one_person_can_hold_several_roles(service: CatalogService) -> None:
    service.set_role("staff-1", Role.NURSE, granted=True, actor_id="staff-manager")
    service.set_role("staff-1", Role.STOREKEEPER, granted=True, actor_id="staff-manager")
    service.set_role("staff-2", Role.RECEPTION, granted=True, actor_id="staff-manager")

    assert service.roles_of("staff-1", EVENING) == frozenset({Role.NURSE, Role.STOREKEEPER})
    assert service.roles_of("staff-2", EVENING) == frozenset({Role.RECEPTION})


def test_revoking_a_role_leaves_the_running_shift_holding_it(service: CatalogService) -> None:
    service.set_role("staff-1", Role.NURSE, granted=True, actor_id="staff-manager")
    revoked = service.set_role("staff-1", Role.NURSE, granted=False, actor_id="staff-manager", running_shift=MORNING)

    assert revoked.created is True
    assert revoked.value.version == 2
    assert service.roles_of("staff-1", MORNING) == frozenset({Role.NURSE})
    assert service.roles_of("staff-1", EVENING) == frozenset()
    assert [(grant.role, grant.granted) for grant in service.staff_roles("staff-1", EVENING)] == [(Role.NURSE, False)]


def test_granting_the_same_role_twice_adds_no_version(service: CatalogService) -> None:
    service.set_role("staff-1", Role.NURSE, granted=True, actor_id="staff-manager")
    again = service.set_role("staff-1", Role.NURSE, granted=True, actor_id="staff-manager", running_shift=MORNING)

    assert again.created is False
    assert again.value.version == 1


@pytest.mark.parametrize("role", ["owner", "", "NURSE"])
def test_a_role_must_come_from_the_vocabulary(service: CatalogService, role: str) -> None:
    with pytest.raises(CatalogError, match="role"):
        service.set_role("staff-1", role, granted=True, actor_id="staff-manager")


@pytest.mark.parametrize("staff_id", ["", "  ", "staff 1", "x" * 65])
def test_a_staff_id_must_be_usable(service: CatalogService, staff_id: str) -> None:
    with pytest.raises(CatalogError, match="staff_id"):
        service.set_role(staff_id, Role.NURSE, granted=True, actor_id="staff-manager")


@pytest.mark.parametrize("actor_id", ["", "   "])
def test_a_catalog_change_must_name_its_author(service: CatalogService, actor_id: str) -> None:
    with pytest.raises(PolicyError):
        service.save_location("shelf_reception", name="قفسهٔ پذیرش", actor_id=actor_id)
    with pytest.raises(PolicyError):
        service.set_role("staff-1", Role.NURSE, granted=True, actor_id=actor_id)
