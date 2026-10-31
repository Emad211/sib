"""Manager-only settings API: read what a shift sees, change what the next shift gets (R-09)."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from shift.domain.policy import PolicyError, SettingKey, ShiftKey
from shift.services.auth import Principal
from shift.services.settings import SettingError, SettingsService, SettingValue

# The guard is `deps.require(Permission.SETTINGS_CHANGE)` in `create_app`: it resolves the
# acting manager's session or raises 403 before any handler runs.
ManagerGuard = Callable[..., Principal]


class SettingUpdate(BaseModel):
    """A new value as text, so the domain canonicalises and bounds it."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=32)


class SettingOut(BaseModel):
    """One setting as a shift sees it, with the version that answered."""

    key: SettingKey
    value: str
    version: int
    effective_from_shift: str
    changed_at: str
    changed_by: str


def _out(value: SettingValue) -> SettingOut:
    return SettingOut(
        key=value.key,
        value=value.value,
        version=value.version,
        effective_from_shift=value.effective_from_shift.text,
        changed_at=value.changed_at,
        changed_by=value.changed_by,
    )


def _setting_key(text: str) -> SettingKey:
    try:
        return SettingKey(text)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown setting: {text}") from None


def build_settings_router(
    *,
    guard: ManagerGuard,
    settings: Callable[..., SettingsService],
    running_shift: Callable[..., ShiftKey | None],
) -> APIRouter:
    """Build the router; a manager guard and a shift source are required, never defaulted."""
    router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

    @router.get("", response_model=list[SettingOut])
    def list_settings(
        _manager: Annotated[Principal, Depends(guard)],
        service: Annotated[SettingsService, Depends(settings)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
    ) -> list[SettingOut]:
        """Every setting as the shift that is running now sees it."""
        try:
            snapshot = service.snapshot(shift)
        except SettingError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        return [_out(snapshot[key]) for key in SettingKey]

    @router.put("/{setting}", response_model=SettingOut)
    def change_setting(
        setting: str,
        body: SettingUpdate,
        manager: Annotated[Principal, Depends(guard)],
        service: Annotated[SettingsService, Depends(settings)],
        shift: Annotated[ShiftKey | None, Depends(running_shift)],
    ) -> SettingOut:
        """Record a new version; a shift already running keeps the version it started with."""
        try:
            changed = service.change(_setting_key(setting), body.value, actor_id=manager.staff_id, running_shift=shift)
        except PolicyError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _out(changed.value)

    @router.get("/{setting}/history", response_model=list[SettingOut])
    def setting_history(
        setting: str,
        _manager: Annotated[Principal, Depends(guard)],
        service: Annotated[SettingsService, Depends(settings)],
    ) -> list[SettingOut]:
        """Every version of one setting, newest first, so a changed value stays traceable."""
        return [_out(value) for value in service.history(_setting_key(setting))]

    return router
