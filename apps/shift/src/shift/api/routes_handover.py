"""Handover API: the live-server blind-count flow, answering statuses and identifiers only.

Every route declares exactly one guard through `deps.require(...)` so the route matrix can walk the
dependency tree; the count route's cash step needs a second permission that depends on the body, and
it is checked in the handler because a static dependency cannot read the body (see `submit_count`).
A handover runs against the live server and never from the offline queue (ADR-016), and no count,
reference, agreed figure, variance or delta may appear in a field, header, cookie or error message
(R-02, ADR-011): an answer is a `CountStatus` from the domain vocabulary, nothing else.

Who hands over is not typed by staff: the outgoing person is the one who received the newest flow of
the same kind (custody), or the custodian of the manager's baseline record on a fresh install
(`HandoverService.custodian`). The body of `start` is only `{flow}`. The signer is not typed either:
each side signs on its own phone, so the service matches the session's person against the flow's two
people (S03 §3, H-04), and a phone can only ever sign its own side.

The manager list exists for H-08: the incoming person records the manager's phone confirmation by
choosing a name, and the manager needs no device of their own. The current list is how the second
phone joins a flow the first one opened, and the checklists and parcels routes record the two middle
steps of H-04 instead of walking past them.
"""

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shift.api import deps
from shift.api.deps import NO_PERMISSION
from shift.api.errors import ApiError, Conflict, Forbidden, NotFound
from shift.common.clock import utc_iso
from shift.domain.checklist_rules import Frequency
from shift.domain.handover_formulas import HandoverError
from shift.domain.handover_fsm import FLOW_STEPS, HandoverFlow
from shift.domain.handover_payloads import CountTarget
from shift.domain.handover_steps import STEP_LOCATIONS
from shift.domain.item_icons import icon_for
from shift.domain.permissions import Permission, Role
from shift.domain.policy import ShiftKey
from shift.services.auth import Principal
from shift.services.catalog import CatalogService, ItemKind
from shift.services.checklists import ChecklistAsker
from shift.services.handover import (
    NO_ACCOUNTING,
    NO_BASELINE,
    Caller,
    HandoverService,
    HandoverState,
    NotASigner,
    NotOfTheFlow,
)
from shift.services.purchase import PurchaseService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/handover", tags=["handover"])

# Answers never carry a figure, so no device or proxy may keep one: a screen that could be replayed
# from a cache would show yesterday's statuses next to today's counts.
NO_STORE = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
# R-02: a refusal says what to do next and never repeats a number; the English reason stays in the
# service and reaches the operator's log, not the phone.
NO_SUCH_HANDOVER = "این تحویل شیفت پیدا نشد."
GENERIC_REFUSAL = "این کار در وضعیت فعلی تحویل شیفت مجاز نیست؛ صفحه را تازه کنید."
NOT_A_SIGNER = "این تحویل مال شما نیست؛ فقط نفر ورودی و نفر خروجی همین تحویل امضا می‌کنند."
# A5 (H-10): the same two people are the only ones who count, advance, answer a checklist or hand a parcel.
NOT_OF_THE_FLOW = "این تحویل مال شما نیست؛ شمارش، چک‌لیست و بسته‌های آن با دو نفرِ همین تحویل است."
_REFUSALS: tuple[tuple[str, str], ...] = (
    ("invoices are open", "تحویل شیفت با صورت‌حساب باز شروع نمی‌شود؛ ابتدا صورت‌حساب‌های باز را ببندید."),
    ("already has a", "این تحویل از قبل برای این شیفت باز شده است."),
    ("themselves", "تحویل شیفت به خود مجاز نیست."),
    ("opens no cash box", "جریان پرستاری صندوق ندارد."),
    ("hands over no cash", "این جریان صندوق ندارد."),
    ("needs its opening cash", "شمارش اولیهٔ صندوق ثبت نشده است؛ مدیر باید آن را ثبت کند."),
    ("already has a figure", "صندوق از قبل عدد دارد؛ شمارش نفر ورودی آن را تسویه می‌کند."),
    ("left; wait for the manager", "نفر خروجی رفته است؛ تا تأیید تلفنی مدیر صبر کنید."),
    ("already closed", "این تحویل بسته شده است."),
    ("cash box has to be", "صندوق باید شمرده و تسویه شود."),
    ("counted and settled", "ابتدا همهٔ اقلام این مرحله شمرده و تسویه شوند."),
    ("not part of the handover", "این قلم در آغاز تحویل ثبت نشده بود."),
    ("current step", "این قلم روی مرحلهٔ جاری شمرده نمی‌شود."),
    ("cash step", "صندوق در مرحلهٔ صندوق شمرده می‌شود."),
    ("no joint count", "این قلم در انتظار شمارش مشترک نیست."),
    ("recorded through", "این قلم باید با شمارش مشترک ثبت شود."),
    ("already settled", "این قلم قبلاً تسویه شده است."),
    ("signatures belong", "امضا فقط در مرحلهٔ امضای دوطرفه ثبت می‌شود."),
    ("checklists work belongs", "پاسخ چک‌لیست فقط در مرحلهٔ چک‌لیست ثبت می‌شود."),
    ("not on this flow's checklist", "این مورد در چک‌لیست این تحویل نیست."),
    ("parcels work belongs", "ثبت بسته فقط در مرحلهٔ بسته‌های در راه انجام می‌شود."),
    ("cannot be skipped", "ثبت امضا پایان تحویل است؛ این مرحله رد نمی‌شود."),
    ("already signed", "این نفر قبلاً امضا کرده است."),
    ("signs alone", "نفر خروجی حاضر نیست؛ نفر ورودی تنها امضا می‌کند."),
    ("no early leave", "خروج زودهنگام نفر خروجی ثبت نشده است."),
    ("past an early-leave", "خروج زودهنگام قبلاً ثبت شده است."),
    ("past an emergency-start", "تأیید شروع اضطراری قبلاً ثبت شده است."),
    ("negative", "شمارش نمی‌تواند منفی باشد."),
    ("skips or repeats", "توالی مراحل تحویل در سیستم نادرست است؛ با مدیر تماس بگیرید."),
)

StartGuard = Annotated[Principal, Depends(deps.require(Permission.HANDOVER_START))]
CountGuard = Annotated[Principal, Depends(deps.require(Permission.HANDOVER_COUNT_SUBMIT))]
SignGuard = Annotated[Principal, Depends(deps.require(Permission.HANDOVER_SIGN))]


class StartIn(BaseModel):
    """Which of the two independent flows of this shift opens (H-12); nothing else is typed."""

    model_config = ConfigDict(extra="forbid")

    flow: HandoverFlow


class CountIn(BaseModel):
    """One blind count or the joint figure: the cash box, or one shelf item of the current step."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["cash", "item"]
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    location_id: str | None = Field(default=None, min_length=1, max_length=128)
    counted: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def _named_target(self) -> "CountIn":
        """An item scope names item and location; a cash scope names neither (S03 §4)."""
        if (self.item_id is None) != (self.location_id is None):
            raise ValueError("item_id and location_id are given together")
        if (self.scope == "item") != (self.item_id is not None):
            raise ValueError("an item scope names item_id and location_id; a cash scope names neither")
        return self

    def target(self) -> CountTarget:
        """The validated target the service compares against its frozen reference."""
        return CountTarget(scope=self.scope, item_id=self.item_id, location_id=self.location_id)


class ChecklistIn(BaseModel):
    """One checklist answer: a yes/no, plus the reading or photo its catalog kind asks for (S03 §1)."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=128)
    ok: bool
    reading: float | None = Field(default=None, ge=0)
    photo_id: str | None = Field(default=None, min_length=1, max_length=128)


class ParcelIn(BaseModel):
    """One parcel still on its way to the store, and how many packages it holds (H-04)."""

    model_config = ConfigDict(extra="forbid")

    parcel_id: str = Field(min_length=1, max_length=128)
    packages: int = Field(strict=True, ge=0)


class EmergencyStartIn(BaseModel):
    """The manager who confirmed the emergency start by phone; the manager needs no device (H-08)."""

    model_config = ConfigDict(extra="forbid")

    manager_id: str = Field(min_length=1, max_length=64)


def get_handover(request: Request) -> HandoverService:
    """The app-wide handover service created in the lifespan; fail closed when unwired."""
    service: HandoverService | None = getattr(request.app.state, "handover", None)
    if service is None:
        raise RuntimeError("the handover service was never wired")
    return service


def _no_store(response: Response) -> Response:
    response.headers.update(NO_STORE)
    return response


def _caller(principal: Principal) -> Caller:
    """R-04: every event names the person and the paired device that wrote it."""
    return Caller(actor_id=principal.staff_id, device_id=principal.device_id)


def _refusal(exc: HandoverError) -> ApiError:
    """Translate a service refusal into the one error envelope, in Persian and with no number."""
    reason = str(exc)
    if isinstance(exc, NotOfTheFlow):
        log.info("handover refusal: %s", reason)
        return Forbidden(NOT_OF_THE_FLOW)
    if isinstance(exc, NotASigner):
        log.info("handover refusal: %s", reason)
        return Forbidden(NOT_A_SIGNER)
    if reason == NO_ACCOUNTING:
        return Conflict(NO_ACCOUNTING)
    if reason == NO_BASELINE:
        return Conflict(NO_BASELINE)
    if reason.startswith("unknown handover"):
        return NotFound(NO_SUCH_HANDOVER)
    for needle, message in _REFUSALS:
        if needle in reason:
            log.info("handover refusal: %s", reason)
            return Conflict(message)
    log.warning("handover refusal without a Persian message: %s", reason)
    return Conflict(GENERIC_REFUSAL)


def _state(state: HandoverState) -> dict[str, Any]:
    """What a client may know: where the flow is and how each chain stands, never a figure."""
    emergency: dict[str, Any] | None = None
    if state.emergency_by is not None:
        recorded_at = state.emergency_at
        emergency = {"manager_id": state.emergency_by, "at": None if recorded_at is None else utc_iso(recorded_at)}
    return {
        "handover_id": state.handover_id,
        "flow": state.flow.value,
        "step": None if state.step is None else state.step.value,
        "steps": [step.value for step in FLOW_STEPS[state.flow]],
        "closed": state.closed,
        "signed": sorted(signer.value for signer in state.signatures),
        "early_left": state.early_left,
        "emergency": emergency,
        "cash_status": None if state.cash_status is None else state.cash_status.value,
        "counts": {item_id: chain.value for item_id, chain in sorted(state.item_statuses.items())},
    }


def _run(work: Any, *args: Any, **kwargs: Any) -> Any:
    """Call the service and turn a policy refusal into its HTTP answer, never a 500."""
    try:
        return work(*args, **kwargs)
    except HandoverError as exc:
        raise _refusal(exc) from exc


@router.post("/start", status_code=status.HTTP_201_CREATED)
def start_handover(
    body: StartIn,
    response: Response,
    principal: StartGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """Open this shift's flow of one kind, with its own cutoff, against the live server (H-11, H-12).

    Who hands over is derived by the service (the newest flow's incoming person, else the manager's
    baseline custodian); staff never type a person or a figure into this route.
    """
    state = _run(service.start, body.flow, incoming_id=principal.staff_id, caller=_caller(principal))
    _no_store(response)
    return _state(state)


@router.get("/managers")
def list_managers(request: Request, response: Response, _principal: CountGuard) -> list[dict[str, str]]:
    """The people a phone confirmation may name (H-08); the manager themselves needs no device."""
    entries = deps.auth_service(request).directory()
    _no_store(response)
    return [
        {"id": entry.staff_id, "name": entry.full_name}
        for entry in entries
        if entry.active and Role.MANAGER.value in entry.roles
    ]


@router.get("/current")
def current_handovers(
    request: Request,
    response: Response,
    _principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> list[dict[str, Any]]:
    """The open flows of this shift, so a second phone joins the flow the first one opened (S03 §3)."""
    shift = deps.running_shift(request)
    _no_store(response)
    return [
        {
            "handover_id": state.handover_id,
            "flow": state.flow.value,
            "step": None if state.step is None else state.step.value,
            "signed": sorted(signer.value for signer in state.signatures),
            "early_left": state.early_left,
        }
        for state in service.open_flows(shift.text)
    ]


@router.get("/{handover_id}")
def get_handover_state(
    handover_id: str,
    request: Request,
    response: Response,
    _principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """The flow as its own log rebuilt it: step, statuses, signatures and the parcels in transit.

    The parcels are the purchasing chain's own deliveries (SHF-4, P-12): the screen hands over a real
    parcel id instead of a number somebody typed, and only when the store has not taken it yet.
    """
    _no_store(response)
    purchase: PurchaseService = request.app.state.purchase
    parcels = [
        {"parcel_id": parcel.delivery_id, "packages": parcel.packages, "invoice_reference": parcel.invoice_reference}
        for parcel in purchase.in_transit(deps.running_shift(request))
    ]
    return {**_state(_run(service.state, handover_id)), "parcels_in_transit": parcels}


@router.get("/{handover_id}/targets")
def handover_targets(
    handover_id: str,
    request: Request,
    response: Response,
    _principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """The counted items of this flow's steps and the flow's checklists, with no quantities (H-01)."""
    state = _run(service.state, handover_id)
    at_shift = ShiftKey.parse(state.shift_id)
    catalog: CatalogService = request.app.state.catalog
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for step in FLOW_STEPS[state.flow]:
        location = STEP_LOCATIONS[step]
        if location is None:
            continue
        for item in catalog.items(at_shift, location_id=location):
            if item.kind is not ItemKind.COUNTED or item.frequency is not Frequency.EVERY_SHIFT:
                continue
            if (item.item_id, location) in seen:
                continue
            seen.add((item.item_id, location))
            items.append(
                {
                    "item_id": item.item_id,
                    "name": item.name,
                    "unit": item.unit,
                    "location_id": item.location_id,
                    "icon": icon_for(item),
                }
            )
    checklists = [
        {"item_id": item.item_id, "name": item.name, "kind": item.kind.value, "icon": icon_for(item)}
        for item in ChecklistAsker(catalog, request.app.state.database).due(state.flow, at_shift)
    ]
    _no_store(response)
    return {"handover_id": state.handover_id, "flow": state.flow.value, "items": items, "checklists": checklists}


@router.post("/{handover_id}/counts")
def submit_count(
    handover_id: str,
    body: CountIn,
    response: Response,
    principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, str]:
    """Take one blind count and answer with a status; a cash count needs the cash permission too."""
    if body.scope == "cash" and Permission.HANDOVER_CASH_SUBMIT not in principal.permissions:
        raise Forbidden(NO_PERMISSION)
    chain = _run(service.count, handover_id, body.target(), body.counted, caller=_caller(principal))
    _no_store(response)
    return {"status": chain.value}


@router.post("/{handover_id}/joint")
def submit_joint_count(
    handover_id: str,
    body: CountIn,
    response: Response,
    principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, str]:
    """Record the figure both people counted together and answer with a status (H-05, H-13)."""
    if body.scope == "cash" and Permission.HANDOVER_CASH_SUBMIT not in principal.permissions:
        raise Forbidden(NO_PERMISSION)
    chain = _run(service.joint, handover_id, body.target(), body.counted, caller=_caller(principal))
    _no_store(response)
    return {"status": chain.value}


@router.post("/{handover_id}/advance")
def advance_handover(
    handover_id: str,
    response: Response,
    principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """Leave the current step for the next one; the walk is ordered and never skips (H-04)."""
    state = _run(service.advance, handover_id, caller=_caller(principal))
    _no_store(response)
    return _state(state)


@router.post("/{handover_id}/checklists")
def answer_checklist(
    handover_id: str,
    body: ChecklistIn,
    response: Response,
    principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """Record one checklist answer on the checklists step (H-04); it belongs to that step alone."""
    state = _run(
        service.answer_checklist,
        handover_id,
        body.item_id,
        ok=body.ok,
        reading=body.reading,
        photo_id=body.photo_id,
        caller=_caller(principal),
    )
    _no_store(response)
    return _state(state)


@router.post("/{handover_id}/parcels")
def hand_parcel(
    handover_id: str,
    body: ParcelIn,
    request: Request,
    response: Response,
    principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """Record one parcel in transit changing hands on the parcels step (H-04).

    P-12: once the purchasing chain knows which parcels are on their way, the only ids this route
    accepts are those real deliveries — a typed number can no longer stand in for a parcel nobody can
    trace. An install that has not used the chain yet still hands over a parcel by its own id.
    """
    purchase: PurchaseService = request.app.state.purchase
    known = {parcel.delivery_id for parcel in purchase.in_transit(deps.running_shift(request))}
    if known and body.parcel_id not in known:
        raise Conflict("این شناسهٔ بسته در فهرست بارهای در راه نیست؛ بسته را از فهرست انتخاب کنید.")
    state = _run(service.hand_parcel, handover_id, body.parcel_id, body.packages, caller=_caller(principal))
    _no_store(response)
    return _state(state)


@router.post("/{handover_id}/sign")
def sign_handover(
    handover_id: str,
    response: Response,
    principal: SignGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """Record the caller's own signature at the dual-sign step; the last required one closes the flow."""
    state = _run(service.sign, handover_id, caller=_caller(principal))
    _no_store(response)
    return _state(state)


@router.post("/{handover_id}/early-leave")
def report_early_leave(
    handover_id: str,
    response: Response,
    principal: CountGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """Note that the outgoing person left before handing over (H-08); nothing happens by itself."""
    state = _run(service.report_early_leave, handover_id, caller=_caller(principal))
    _no_store(response)
    return _state(state)


@router.post("/{handover_id}/emergency-start")
def confirm_emergency_start(
    handover_id: str,
    body: EmergencyStartIn,
    response: Response,
    principal: StartGuard,
    service: Annotated[HandoverService, Depends(get_handover)],
) -> dict[str, Any]:
    """Record the manager's phone confirmation that the incoming person finishes alone (H-08)."""
    state = _run(service.confirm_emergency_start, handover_id, body.manager_id, caller=_caller(principal))
    _no_store(response)
    return _state(state)
