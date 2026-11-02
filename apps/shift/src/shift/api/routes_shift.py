"""The shift card: which shift is running and who is on it (ADR-029).

Anybody signed in may read it. It names the running shift (صبح، عصر، شب with its Jalali day and hours)
and the people who are on shift right now, with the role each one is on shift for, so a phone never
leaves anybody guessing whose shift it is. It carries names and role labels only — no figure, no
record, no reference (R-02, R-07). «Who is on shift» is the same answer the permission check uses
(`services/on_shift.py`), never a second copy of it.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from shift.adapters.db import staff_repo
from shift.api import deps
from shift.common.jalali import format_jalali
from shift.domain.on_shift import has_bound_role
from shift.domain.shift_clock import SHIFT_START_TIMES
from shift.services.auth import Principal
from shift.services.on_shift import on_shift_roles

router = APIRouter(prefix="/api/v1/shift", tags=["shift"])
SessionUser = Annotated[Principal, Depends(deps.require_session())]

SHIFT_NAMES = {1: "صبح", 2: "عصر", 3: "شب"}
ROLE_LABELS = {"reception": "پذیرش", "nurse": "پرستار", "shift_doctor": "پزشک شیفت"}
_ROLE_ORDER = {role: index for index, role in enumerate(ROLE_LABELS)}


def _clock(rank: int) -> str:
    start = SHIFT_START_TIMES[rank]
    return f"{start.hour:02d}:{start.minute:02d}"


@router.get("/now", summary="The running shift and the people on it")
def shift_now(request: Request, response: Response, principal: SessionUser) -> dict[str, Any]:
    """One small read for every phone: the shift, who holds it, and whether the caller is one of them."""
    response.headers["Cache-Control"] = "no-store"
    shift = deps.running_shift(request)
    instant = request.app.state.clock.now_utc()
    following = shift.rank % len(SHIFT_NAMES) + 1
    people: list[dict[str, Any]] = []
    with request.app.state.database.read() as connection:
        for person in staff_repo.list_with_roles(connection, shift.text):
            if not person.staff.active:
                continue
            working = on_shift_roles(connection, staff_id=person.staff.staff_id, roles=person.roles, instant=instant)
            if working:
                people.append(
                    {
                        "name": person.staff.full_name,
                        "roles": [ROLE_LABELS[role] for role in working],
                        "me": person.staff.staff_id == principal.staff_id,
                        "_order": min(_ROLE_ORDER[role] for role in working),
                    }
                )
    people.sort(key=lambda entry: (entry["_order"], entry["name"]))
    for entry in people:
        del entry["_order"]
    return {
        "shift": {
            "rank": shift.rank,
            "name": SHIFT_NAMES[shift.rank],
            "date": format_jalali(shift.work_date),
            "from": _clock(shift.rank),
            "to": _clock(following),
        },
        "people": people,
        "me": {"on_shift": bool(principal.on_shift_roles) if has_bound_role(principal.roles) else None},
    }
