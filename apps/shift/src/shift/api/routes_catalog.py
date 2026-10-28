"""Manager-only catalog API: locations, items and staff roles, each versioned by shift (R-08)."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from shift.domain.policy import PolicyError, Role, ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogError, CatalogService, Item, ItemKind, Location, StaffRole

# The guard is `deps.require(Permission.CATALOG_MANAGE)` in `create_app`: it resolves the
# acting manager's session or raises 403 before any handler runs.
ManagerGuard = Callable[..., Principal]


class LocationUpdate(BaseModel):
    """A new version of a location; `retired` stops later shifts from using it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    retired: bool = False


class LocationOut(BaseModel):
    """One location as a shift sees it."""

    location_id: str
    name: str
    retired: bool
    version: int
    effective_from_shift: str


class ItemUpdate(BaseModel):
    """A new version of an item; `stock_factor` is shelf units per accounting unit."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    kind: ItemKind
    location_id: str = Field(min_length=1, max_length=48)
    unit: str = Field(min_length=1, max_length=16)
    minimum: int = Field(default=0, ge=0)
    acc_code: str | None = Field(default=None, max_length=32)
    stock_factor: int = Field(default=1, ge=1)
    retired: bool = False


class ItemOut(BaseModel):
    """One item as a shift sees it."""

    item_id: str
    name: str
    kind: ItemKind
    location_id: str
    unit: str
    minimum: int
    acc_code: str | None
    stock_factor: int
    retired: bool
    version: int
    effective_from_shift: str


class RoleUpdate(BaseModel):
    """Grant or revoke one role."""

    model_config = ConfigDict(extra="forbid")

    granted: bool


class StaffRoleOut(BaseModel):
    """The grant decision for one role of one person."""

    role: Role
    granted: bool
    version: int
    effective_from_shift: str


def _location(value: Location) -> LocationOut:
    return LocationOut(
        location_id=value.location_id,
        name=value.name,
        retired=value.retired,
        version=value.version,
        effective_from_shift=value.effective_from_shift.text,
    )


def _item(value: Item) -> ItemOut:
    return ItemOut(
        item_id=value.item_id,
        name=value.name,
        kind=value.kind,
        location_id=value.location_id,
        unit=value.unit,
        minimum=value.minimum,
        acc_code=value.acc_code,
        stock_factor=value.stock_factor,
        retired=value.retired,
        version=value.version,
        effective_from_shift=value.effective_from_shift.text,
    )


def _role(value: StaffRole) -> StaffRoleOut:
    return StaffRoleOut(
        role=value.role,
        granted=value.granted,
        version=value.version,
        effective_from_shift=value.effective_from_shift.text,
    )


def build_catalog_router(
    *,
    guard: ManagerGuard,
    catalog: Callable[..., CatalogService],
    running_shift: Callable[..., ShiftKey | None],
) -> APIRouter:
    """Build the router; a manager guard and a shift source are required, never defaulted."""
    router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])

    @router.get("/roles", response_model=list[Role])
    def list_roles(_manager: Annotated[Principal, Depends(guard)]) -> list[Role]:
        """The role vocabulary a manager may grant (S03 §1)."""
        return list(Role)

    @router.get("/locations", response_model=list[LocationOut])
    def list_locations(
        _manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CatalogService, Depends(catalog)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
        include_retired: Annotated[bool, Query()] = False,
    ) -> list[LocationOut]:
        """The locations the running shift may use, retired ones only when asked."""
        return [_location(value) for value in service.locations(shift, include_retired=include_retired)]

    @router.put("/locations/{location_id}", response_model=LocationOut)
    def save_location(
        location_id: str,
        body: LocationUpdate,
        manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CatalogService, Depends(catalog)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
    ) -> LocationOut:
        """Record a location version from the next shift on."""
        try:
            saved = service.save_location(
                location_id, name=body.name, retired=body.retired, actor_id=manager.staff_id, running_shift=shift
            )
        except (CatalogError, PolicyError) as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _location(saved.value)

    @router.get("/items", response_model=list[ItemOut])
    def list_items(
        _manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CatalogService, Depends(catalog)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
        location_id: Annotated[str | None, Query(max_length=48)] = None,
        include_retired: Annotated[bool, Query()] = False,
    ) -> list[ItemOut]:
        """The items the running shift may count, optionally one location's shelf."""
        try:
            items = service.items(shift, location_id=location_id, include_retired=include_retired)
        except CatalogError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return [_item(value) for value in items]

    @router.put("/items/{item_id}", response_model=ItemOut)
    def save_item(
        item_id: str,
        body: ItemUpdate,
        manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CatalogService, Depends(catalog)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
    ) -> ItemOut:
        """Record an item version from the next shift on."""
        try:
            saved = service.save_item(
                item_id,
                name=body.name,
                kind=body.kind,
                location_id=body.location_id,
                unit=body.unit,
                minimum=body.minimum,
                acc_code=body.acc_code,
                stock_factor=body.stock_factor,
                retired=body.retired,
                actor_id=manager.staff_id,
                running_shift=shift,
            )
        except (CatalogError, PolicyError) as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _item(saved.value)

    @router.get("/staff/{staff_id}/roles", response_model=list[StaffRoleOut])
    def list_staff_roles(
        staff_id: str,
        _manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CatalogService, Depends(catalog)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
    ) -> list[StaffRoleOut]:
        """The grant decision per role of one person, as the running shift sees it."""
        try:
            roles = service.staff_roles(staff_id, shift)
        except CatalogError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return [_role(value) for value in roles]

    @router.put("/staff/{staff_id}/roles/{role}", response_model=StaffRoleOut)
    def set_staff_role(
        staff_id: str,
        role: Role,
        body: RoleUpdate,
        manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CatalogService, Depends(catalog)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
    ) -> StaffRoleOut:
        """Grant or revoke one role from the next shift on (R-08)."""
        try:
            saved = service.set_role(
                staff_id, role, granted=body.granted, actor_id=manager.staff_id, running_shift=shift
            )
        except (CatalogError, PolicyError) as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _role(saved.value)

    return router
