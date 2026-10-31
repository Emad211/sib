"""Manager-only campaign calendar: the page the manager defines campaigns on, and its JSON API.

The shift event «ت» may only name a campaign the manager defined beforehand (S01 §3), so this screen
is the only way a clinic discount comes into existence. The page is server-rendered and phone-first
(R-10): it lists every campaign with Jalali dates and Tehran stamps, carries `Cache-Control: no-store`
and no reference number (R-02, ADR-011), and its small form PUTs JSON through `manager-campaigns.js`
and reloads. The API is manager-only under `Permission.CATALOG_MANAGE`, the same rule as the catalog,
and answers 400 with the reason when a range or an amount is refused.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from shift.api.routes_web import _shell_page
from shift.common.clock import parse_iso, to_tehran
from shift.common.jalali import format_jalali
from shift.domain.policy import PolicyError
from shift.services.auth import Principal
from shift.services.campaigns import MAX_AMOUNT_TOMAN, Campaign, CampaignError, CampaignService

MANAGER_PATH = "/manager/campaigns"
API_PATH = "/api/v1/campaigns"
NO_STORE = "no-store"

# The guards are `deps.require(Permission.CATALOG_MANAGE)` for the API and
# `deps.require_page(Permission.SETTINGS_CHANGE)` for the page, both wired in `create_app`: the acting
# manager's session is resolved, or the caller gets 401/403, before any handler runs.
ManagerGuard = Callable[..., Principal]
PageGuard = Callable[..., Principal]
CampaignSource = Callable[..., CampaignService]


class CampaignUpdate(BaseModel):
    """One campaign as the manager typed it; the service owns the policy bounds and the messages."""

    model_config = ConfigDict(extra="forbid")

    name: str
    service: str
    amount_toman: int
    starts_on: str
    ends_on: str


class CampaignOut(BaseModel):
    """One campaign as the API answers it, its days as ISO `YYYY-MM-DD`."""

    campaign_id: str
    name: str
    service: str
    amount_toman: int
    starts_on: str
    ends_on: str
    created_by: str
    created_at: str


def _out(value: Campaign) -> CampaignOut:
    return CampaignOut(
        campaign_id=value.campaign_id,
        name=value.name,
        service=value.service,
        amount_toman=value.amount_toman,
        starts_on=value.starts_on.isoformat(),
        ends_on=value.ends_on.isoformat(),
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _moment_fa(iso_text: str) -> str:
    """`1405/08/09 09:18` for a stored instant, in the clinic's calendar and Tehran time."""
    local = to_tehran(parse_iso(iso_text))
    return f"{format_jalali(local.date())} {local:%H:%M}"


def _page_row(value: Campaign) -> dict[str, str]:
    """One campaign formatted once here, so the template carries Persian text and no figure logic.

    The Jalali dates are for reading and the ISO dates are what the edit form puts back into its
    date fields, which always speak ISO.
    """
    return {
        "campaign_id": value.campaign_id,
        "name": value.name,
        "service": value.service,
        "amount": f"{value.amount_toman:,}",
        "amount_toman": str(value.amount_toman),
        "from": format_jalali(value.starts_on),
        "to": format_jalali(value.ends_on),
        "starts_on": value.starts_on.isoformat(),
        "ends_on": value.ends_on.isoformat(),
        "created_by": value.created_by,
        "created_at": _moment_fa(value.created_at),
    }


def build_campaign_router(*, guard: ManagerGuard, page_guard: PageGuard, campaigns: CampaignSource) -> APIRouter:
    """Build the router; a manager guard, a page guard and a service source, never defaulted."""
    router = APIRouter(tags=["campaigns"])

    @router.get(API_PATH, response_model=list[CampaignOut])
    def list_campaigns(
        _manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CampaignService, Depends(campaigns)],
    ) -> list[CampaignOut]:
        """Every campaign, newest first: the list the phone offers a discount from."""
        return [_out(value) for value in service.all()]

    @router.put(f"{API_PATH}/{{campaign_id}}", response_model=CampaignOut)
    def save_campaign(
        campaign_id: str,
        body: CampaignUpdate,
        manager: Annotated[Principal, Depends(guard)],
        service: Annotated[CampaignService, Depends(campaigns)],
    ) -> CampaignOut:
        """Define or fix one campaign; a refused range or amount is a 400 with its reason."""
        try:
            saved = service.save(
                campaign_id,
                name=body.name,
                service=body.service,
                amount_toman=body.amount_toman,
                starts_on=body.starts_on,
                ends_on=body.ends_on,
                actor_id=manager.staff_id,
            )
        except (CampaignError, PolicyError) as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _out(saved)

    @router.get(MANAGER_PATH, response_class=HTMLResponse, summary="The campaign calendar")
    def get_manager_campaigns(
        request: Request,
        _editor: Annotated[Principal, Depends(page_guard)],
        service: Annotated[CampaignService, Depends(campaigns)],
    ) -> HTMLResponse:
        """The campaigns the manager defined, with the form that defines or fixes one."""
        rows = [_page_row(value) for value in service.all()]
        return _shell_page(
            request,
            "manager_campaigns.html",
            {"campaigns": rows, "max_amount": MAX_AMOUNT_TOMAN},
            cache_control=NO_STORE,
        )

    return router
