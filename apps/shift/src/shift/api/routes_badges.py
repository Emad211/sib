"""The numbers on the home tiles: how many open jobs wait for this very person.

One small read answers every tile at once, so the home screen can paint «۳ کار باز» on the icon and
refresh it while the phone stays open. Each count asks the service that already owns that work and
counts only what *this* person can act on (never a figure, a name or a price), and a tile with
nothing waiting is left out. A count that fails is left out too: the home screen must open even when
one of its numbers cannot be read.
"""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from shift.api import deps
from shift.domain.permissions import Permission
from shift.services.auth import Principal
from shift.services.purchase import PURCHASE_PERMISSIONS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["badges"])
SessionUser = Annotated[Principal, Depends(deps.require_session())]

Counter = Callable[[Request, Principal], int]


def _handover(request: Request, principal: Principal) -> int:
    """The open handovers this person is one of the two people of."""
    shift = deps.running_shift(request)
    return sum(
        1
        for flow in request.app.state.handover.open_flows(shift.text)
        if principal.staff_id in (flow.incoming_id, flow.outgoing_id)
    )


def _events(request: Request, principal: Principal) -> int:
    """Records waiting for this person's decision, plus the open ones they may close."""
    shift = deps.running_shift(request)
    service = request.app.state.shift_events
    return len(service.pending_for(principal)) + len(service.open_targets(principal, shift))


def _purchase(request: Request, principal: Principal) -> int:
    """Parcels to count, shelf charges and returns to confirm, and (for a decider) orders waiting."""
    shift = deps.running_shift(request)
    purchase = request.app.state.purchase
    inbox = purchase.state(principal, shift).inbox
    waiting = 0
    if inbox is not None:
        waiting = len(inbox["parcels_to_count"]) + len(inbox["restock_to_confirm"]) + len(inbox["return_to_confirm"])
    if Permission.PURCHASE_ORDER_DECIDE in principal.permissions:
        waiting += len(purchase.pending(shift).orders)
    return waiting


def _accountant_queue(request: Request, principal: Principal) -> int:
    queue = request.app.state.accountant_queue.queue(principal)
    return len(queue.card_transfers) + len(queue.withdrawals) + len(queue.refunds)


def _decisions(request: Request, _principal: Principal) -> int:
    return int(request.app.state.decisions.pending().count)


def _rewards(request: Request, _principal: Principal) -> int:
    return len(request.app.state.rewards.pending())


# (tile href, the permission any of which shows the tile, the count)
_TILES: tuple[tuple[str, frozenset[Permission], Counter], ...] = (
    ("/handover", frozenset({Permission.HANDOVER_START}), _handover),
    ("/events", frozenset({Permission.EVENTS_APPEND}), _events),
    ("/purchase", frozenset(PURCHASE_PERMISSIONS), _purchase),
    ("/accountant/queue", frozenset({Permission.ACCOUNTANT_QUEUE_VIEW}), _accountant_queue),
    ("/manager/decisions", frozenset({Permission.DAILY_CARD_VIEW}), _decisions),
    ("/manager/rewards", frozenset({Permission.REWARD_GRANT}), _rewards),
)


@router.get("/badges", summary="Open jobs per home tile for the signed-in person")
def badges(request: Request, response: Response, principal: SessionUser) -> dict[str, dict[str, int]]:
    """Only the tiles this person holds and that have something waiting."""
    response.headers["Cache-Control"] = "no-store"
    counts: dict[str, int] = {}
    for href, needs, count in _TILES:
        if not principal.permissions & needs:
            continue
        try:
            waiting = count(request, principal)
        except Exception:  # noqa: BLE001 - one unreadable number must not take the home screen down
            log.warning("the %s badge could not be counted", href, exc_info=True)
            continue
        if waiting > 0:
            counts[href] = waiting
    return {"badges": counts}
