"""Device routes: the `/enroll` page phones open from a QR, and device administration.

`POST /api/v1/devices/enroll` is public by design: the one-time token in the QR is the
credential, because the phone has no session yet. Everything else is manager-guarded.
"""

from typing import Annotated

import segno
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from shift.api import deps
from shift.api.templating import templates
from shift.common.clock import parse_iso, to_tehran, utc_iso
from shift.common.jalali import format_jalali
from shift.domain.permissions import Permission
from shift.services.auth import Principal
from shift.services.devices import DeviceService, EnrolledDevice

router = APIRouter()

DeviceAdmin = Annotated[Principal, Depends(deps.require_page(Permission.DEVICE_ENROLL))]
DeviceEnroller = Annotated[Principal, Depends(deps.require(Permission.DEVICE_ENROLL))]
DeviceRevoker = Annotated[Principal, Depends(deps.require(Permission.DEVICE_REVOKE))]


class MintRequest(BaseModel):
    """Which staff member the printed QR is for."""

    model_config = ConfigDict(extra="forbid")

    staff_id: str = Field(min_length=1, max_length=64)


class EnrollRequest(BaseModel):
    """The QR fragment token plus what this phone calls itself and its public key."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)
    device_label: str = Field(min_length=1, max_length=64)
    device_pubkey: str = Field(min_length=16, max_length=4096)


def _service(request: Request) -> DeviceService:
    return request.app.state.devices


def _device_payload(device: EnrolledDevice) -> dict[str, object]:
    return {
        "device_id": device.device_id,
        "staff_id": device.staff_id,
        "label": device.label,
        "enrolled_at": device.enrolled_at,
        "revoked_at": device.revoked_at,
        "revoked_reason": device.revoked_reason,
    }


def _qr_data_uri(url: str) -> str:
    """Render the enrollment URL as an inline SVG, so the screen needs no CDN."""
    return segno.make(url, error="m").svg_data_uri()


def _tehran_stamp(iso_text: str) -> str:
    """`2026/08/06 14:32` for a stored UTC instant, in the clinic's time zone."""
    moment = to_tehran(parse_iso(iso_text))
    return f"{format_jalali(moment.date())} {moment:%H:%M}"


@router.get("/enroll", response_class=HTMLResponse, tags=["devices"])
def enroll_page(request: Request) -> HTMLResponse:
    """Public page that reads the token from the URL fragment and pairs the phone."""
    return templates.TemplateResponse(request, "enroll.html", {})


@router.get("/admin/devices", response_class=HTMLResponse, tags=["devices"])
def device_admin_page(request: Request, principal: DeviceAdmin) -> HTMLResponse:
    """Manager screen: mint a pairing QR, see enrolled phones, revoke one."""
    directory = deps.auth_service(request).directory()
    names = {entry.staff_id: entry.full_name for entry in directory}
    return templates.TemplateResponse(
        request,
        "device_admin.html",
        {
            "csrf_token": principal.csrf_token,
            "staff": [
                {
                    "id": entry.staff_id,
                    "name": entry.full_name,
                    "roles": ", ".join(entry.roles),
                    "active": entry.active,
                    "locked": entry.locked_until is not None,
                }
                for entry in directory
            ],
            "devices": [
                {
                    "device": _device_payload(device),
                    "staff_name": names.get(device.staff_id, device.staff_id),
                    "enrolled_at_fa": _tehran_stamp(device.enrolled_at),
                }
                for device in _service(request).list_devices()
            ],
        },
    )


@router.get("/api/v1/devices", tags=["devices"])
def list_devices(request: Request, _: DeviceEnroller) -> list[dict[str, object]]:
    """Every device with its revocation state, newest first."""
    return [_device_payload(device) for device in _service(request).list_devices()]


@router.post("/api/v1/devices/enrollment-tokens", status_code=201, tags=["devices"])
def mint_enrollment_token(body: MintRequest, request: Request, principal: DeviceEnroller) -> JSONResponse:
    """Mint a single-use 10-minute token and return the QR for it (shown once)."""
    offer = _service(request).mint(staff_id=body.staff_id, created_by=principal.staff_id, origin=str(request.base_url))
    return JSONResponse(
        {
            "device_id": offer.device_id,
            "staff_id": offer.staff_id,
            "enrollment_url": offer.url,
            "expires_at": utc_iso(offer.expires_at),
            "expires_at_fa": _tehran_stamp(utc_iso(offer.expires_at)),
            "qr_svg": _qr_data_uri(offer.url),
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/v1/devices/enroll", status_code=201, tags=["devices"])
def enroll(request: Request, body: EnrollRequest) -> dict[str, object]:
    """Consume the QR token and pair this phone; the token is single use."""
    device = _service(request).enroll(token=body.token, label=body.device_label, pubkey=body.device_pubkey)
    return _device_payload(device)


@router.post("/api/v1/devices/{device_id}/revoke", status_code=204, tags=["devices"])
def revoke_device(device_id: str, request: Request, principal: DeviceRevoker) -> Response:
    """Revoke a lost phone and kill every session it opened."""
    _service(request).revoke(device_id=device_id, actor_id=principal.staff_id)
    return Response(status_code=204)
