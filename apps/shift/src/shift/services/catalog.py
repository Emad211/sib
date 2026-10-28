"""Versioned catalog: locations, items and staff roles, each effective from one shift (R-08, R-09)."""

import re
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Connection

from shift.adapters.db import catalog_repo
from shift.adapters.db.catalog_repo import ItemRow, LocationRow, RoleRow
from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, utc_iso
from shift.domain.policy import PolicyError, Role, ShiftKey, effective_from

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,47}")
_STAFF_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class CatalogError(ValueError):
    """The requested catalog change is not allowed."""


class ItemKind(StrEnum):
    """How a checklist item is answered (S03 §1); only counted items have a shelf quantity."""

    COUNTED = "counted"
    YES_NO = "yes_no"
    READING = "reading"
    PHOTO = "photo"


@dataclass(frozen=True, slots=True)
class Location:
    """A place stock is counted in, such as a shelf, room or the main store."""

    location_id: str
    name: str
    retired: bool
    version: int
    effective_from_shift: ShiftKey


@dataclass(frozen=True, slots=True)
class Item:
    """One catalog item; `stock_factor` is shelf units per accounting ledger unit."""

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
    effective_from_shift: ShiftKey


@dataclass(frozen=True, slots=True)
class StaffRole:
    """One person's hold on one role as a shift sees it; several may hold at once (R-08)."""

    staff_id: str
    role: Role
    granted: bool
    version: int
    effective_from_shift: ShiftKey


@dataclass(frozen=True, slots=True)
class CatalogChange:
    """What a catalog write produced: the version now carrying the content, and if it is new."""

    value: Location | Item | StaffRole
    created: bool


def _identifier(value: object, field: str) -> str:
    text = str(value).strip()
    if _IDENTIFIER.fullmatch(text) is None:
        raise CatalogError(f"{field} must be a lower-case slug of letters, digits, '_' or '-': {value!r}")
    return text


def _staff_id(value: object) -> str:
    text = str(value).strip()
    if _STAFF_ID.fullmatch(text) is None:
        raise CatalogError(f"staff_id must be 1..64 letters, digits, '_' or '-': {value!r}")
    return text


def _label(value: object, field: str, maximum: int) -> str:
    text = str(value).strip()
    if not 1 <= len(text) <= maximum:
        raise CatalogError(f"{field} must be 1..{maximum} characters")
    return text


def _quantity(value: object, field: str, *, least: int) -> int:
    if isinstance(value, bool) or isinstance(value, float):
        raise CatalogError(f"{field} must be a whole number, got {value!r}")
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise CatalogError(f"{field} must be a whole number, got {value!r}") from exc
    if number < least:
        raise CatalogError(f"{field} must be at least {least}, got {number}")
    return number


def _actor(actor_id: str) -> str:
    """Every change names its author (R-04); an empty actor means a broken caller."""
    actor = actor_id.strip()
    if not actor:
        raise PolicyError("actor_id is required")
    return actor


def _location(row: LocationRow) -> Location:
    return Location(
        location_id=row.location_id,
        name=row.name,
        retired=row.retired,
        version=row.version,
        effective_from_shift=ShiftKey.parse(row.effective_from_shift),
    )


def _item(row: ItemRow) -> Item:
    return Item(
        item_id=row.item_id,
        name=row.name,
        kind=ItemKind(row.kind),
        location_id=row.location_id,
        unit=row.unit,
        minimum=row.minimum,
        acc_code=row.acc_code,
        stock_factor=row.stock_factor,
        retired=row.retired,
        version=row.version,
        effective_from_shift=ShiftKey.parse(row.effective_from_shift),
    )


def _staff_role(row: RoleRow) -> StaffRole:
    return StaffRole(
        staff_id=row.staff_id,
        role=Role(row.role),
        granted=row.granted,
        version=row.version,
        effective_from_shift=ShiftKey.parse(row.effective_from_shift),
    )


def _content(row: ItemRow) -> tuple[object, ...]:
    return (
        row.name,
        row.kind,
        row.location_id,
        row.unit,
        row.minimum,
        row.acc_code,
        row.stock_factor,
        row.retired,
    )


class CatalogService:
    """Reads the catalog of a shift and appends versions that only later shifts can see."""

    def __init__(self, database: Database, *, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or SystemClock()

    def locations(self, shift: ShiftKey | None = None, *, include_retired: bool = False) -> list[Location]:
        """The locations `shift` may use; retired ones only when a manager asks for them."""
        with self._database.read() as connection:
            rows = catalog_repo.list_locations_effective(connection, (shift or ShiftKey.baseline()).text)
        return [_location(row) for row in rows if include_retired or not row.retired]

    def items(
        self,
        shift: ShiftKey | None = None,
        *,
        location_id: str | None = None,
        include_retired: bool = False,
    ) -> list[Item]:
        """The items `shift` may count, optionally just one location's.

        The location filter runs after version resolution, so an item that moved appears
        only under the location it is in during `shift`.
        """
        with self._database.read() as connection:
            rows = catalog_repo.list_items_effective(connection, (shift or ShiftKey.baseline()).text)
        wanted = None if location_id is None else _identifier(location_id, "location_id")
        return [
            item
            for item in (_item(row) for row in rows)
            if (include_retired or not item.retired) and (wanted is None or item.location_id == wanted)
        ]

    def staff_roles(self, staff_id: object, shift: ShiftKey | None = None) -> list[StaffRole]:
        """The grant decision per role of one person as `shift` sees it, in role name order."""
        resolved = _staff_id(staff_id)
        with self._database.read() as connection:
            rows = catalog_repo.list_staff_roles_effective(connection, resolved, (shift or ShiftKey.baseline()).text)
        return [_staff_role(row) for row in rows]

    def roles_of(self, staff_id: object, shift: ShiftKey | None = None) -> frozenset[Role]:
        """The roles the person holds in `shift`; queues and permissions follow this set."""
        return frozenset(grant.role for grant in self.staff_roles(staff_id, shift) if grant.granted)

    def save_location(
        self,
        location_id: object,
        *,
        name: object,
        actor_id: str,
        retired: bool = False,
        running_shift: ShiftKey | None = None,
    ) -> CatalogChange:
        """Create or update a location from the shift after `running_shift` (R-09).

        Repeating what is already stored appends nothing and reports `created=False`.
        """
        resolved_id = _identifier(location_id, "location_id")
        label = _label(name, "name", 80)
        actor = _actor(actor_id)
        target = effective_from(running_shift)
        with self._database.write() as connection:
            latest = catalog_repo.find_location_latest(connection, resolved_id)
            if latest is not None and (latest.name, latest.retired) == (label, retired):
                return CatalogChange(_location(latest), created=False)
            version = catalog_repo.location_next_version(connection, resolved_id)
            catalog_repo.insert_location_version(
                connection,
                location_id=resolved_id,
                name=label,
                retired=retired,
                version=version,
                effective_from_shift=target.text,
                changed_at=utc_iso(self._clock.now_utc()),
                changed_by=actor,
            )
        return CatalogChange(Location(resolved_id, label, retired, version, target), created=True)

    def save_item(
        self,
        item_id: object,
        *,
        name: object,
        kind: ItemKind | str,
        location_id: object,
        unit: object,
        actor_id: str,
        minimum: object = 0,
        acc_code: object | None = None,
        stock_factor: object = 1,
        retired: bool = False,
        running_shift: ShiftKey | None = None,
    ) -> CatalogChange:
        """Create or update an item from the shift after `running_shift` (R-09).

        Repeating what is already stored appends nothing and reports `created=False`.
        """
        resolved_id = _identifier(item_id, "item_id")
        label = _label(name, "name", 80)
        try:
            resolved_kind = ItemKind(kind)
        except ValueError as exc:
            raise CatalogError(f"unknown item kind: {kind!r}") from exc
        place = _identifier(location_id, "location_id")
        resolved_unit = _label(unit, "unit", 16)
        floor = _quantity(minimum, "minimum", least=0)
        factor = _quantity(stock_factor, "stock_factor", least=1)
        code = None if acc_code is None else _label(acc_code, "acc_code", 32)
        if resolved_kind is ItemKind.COUNTED and code is None:
            raise CatalogError("a counted item needs an accounting code (acc_code), S03 §1")
        actor = _actor(actor_id)
        target = effective_from(running_shift)
        requested = (label, resolved_kind.value, place, resolved_unit, floor, code, factor, retired)
        with self._database.write() as connection:
            latest = catalog_repo.find_item_latest(connection, resolved_id)
            if latest is not None and _content(latest) == requested:
                return CatalogChange(_item(latest), created=False)
            if not retired:
                self._require_active_location(connection, place, target)
            self._require_free_acc_code(connection, resolved_id, code, target)
            version = catalog_repo.item_next_version(connection, resolved_id)
            catalog_repo.insert_item_version(
                connection,
                item_id=resolved_id,
                name=label,
                kind=resolved_kind.value,
                location_id=place,
                unit=resolved_unit,
                minimum=floor,
                acc_code=code,
                stock_factor=factor,
                retired=retired,
                version=version,
                effective_from_shift=target.text,
                changed_at=utc_iso(self._clock.now_utc()),
                changed_by=actor,
            )
        changed = Item(
            item_id=resolved_id,
            name=label,
            kind=resolved_kind,
            location_id=place,
            unit=resolved_unit,
            minimum=floor,
            acc_code=code,
            stock_factor=factor,
            retired=retired,
            version=version,
            effective_from_shift=target,
        )
        return CatalogChange(changed, created=True)

    def set_role(
        self,
        staff_id: object,
        role: Role | str,
        *,
        granted: bool,
        actor_id: str,
        running_shift: ShiftKey | None = None,
    ) -> CatalogChange:
        """Grant or revoke one role from the shift after `running_shift` (R-08, R-09)."""
        resolved_staff = _staff_id(staff_id)
        try:
            resolved_role = Role(role)
        except ValueError as exc:
            raise CatalogError(f"unknown role: {role!r}") from exc
        actor = _actor(actor_id)
        target = effective_from(running_shift)
        with self._database.write() as connection:
            latest = catalog_repo.find_role_latest(connection, resolved_staff, resolved_role.value)
            if latest is not None and latest.granted == granted:
                return CatalogChange(_staff_role(latest), created=False)
            version = catalog_repo.role_next_version(connection, resolved_staff, resolved_role.value)
            catalog_repo.insert_role_version(
                connection,
                staff_id=resolved_staff,
                role=resolved_role.value,
                granted=granted,
                version=version,
                effective_from_shift=target.text,
                changed_at=utc_iso(self._clock.now_utc()),
                changed_by=actor,
            )
        return CatalogChange(StaffRole(resolved_staff, resolved_role, granted, version, target), created=True)

    @staticmethod
    def _require_active_location(connection: Connection, location_id: str, target: ShiftKey) -> None:
        location = catalog_repo.find_location_effective(connection, location_id, target.text)
        if location is None or location.retired:
            raise CatalogError(f"location {location_id!r} is not in use from {target.text}")

    @staticmethod
    def _require_free_acc_code(connection: Connection, item_id: str, acc_code: str | None, target: ShiftKey) -> None:
        """One accounting code names one item for ever (S01 §4, S03 §1).

        Retiring an item does not free its code: accounting rows already carry it, and the
        bridge resolves them back to this item. A successor product is a new version of the
        same item, not a new item.
        """
        if acc_code is None:
            return
        for row in catalog_repo.list_items_effective(connection, target.text):
            if row.item_id != item_id and row.acc_code == acc_code:
                raise CatalogError(f"acc_code {acc_code!r} already belongs to item {row.item_id!r}")
