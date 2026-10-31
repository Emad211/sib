"""The manager's reward screen and one person's own standing (S01 §5, SHF-5).

Two pages, one job each, server-rendered and phone-first like the rest of the manager area (R-10):
the manager sets the rate per work type, sees what accounting's copy holds, accrues the automatic
works, confirms or rejects what waits, reads the month's table and takes a reward back with a reason;
a person sees their own amount, their own rank and the team goal, and nothing about anybody else.

A page guard is declared exactly once per page through `deps.require_page`, so a receptionist gets a
403 whose body holds no figure at all and a signed-out browser is sent to the login screen. Both
pages answer `Cache-Control: no-store`, and no page carries a reference number (ADR-011). The
manager's four form posts carry one job each; the confirmation buttons are not a second approval
endpoint — they post SHF-3's own `/api/v1/events` with `event.approved` / `event.rejected` — and a
revocation goes through the same validated writer as every other shift event.
"""

import re
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from shift.api import deps
from shift.api.routes_web import _shell_page
from shift.common.clock import parse_iso, to_tehran
from shift.common.ids import new_ulid
from shift.common.jalali import format_jalali
from shift.domain.permissions import Permission
from shift.domain.rewards import CREDIT_WORK_TYPES, WORK_TYPE_LABELS, month_of, next_month, previous_month
from shift.domain.shift_events.rewards import REWARD_CONFIRMED, REWARD_REVOKED
from shift.services.auth import Principal
from shift.services.campaigns import MAX_AMOUNT_TOMAN
from shift.services.outbox import EventSubmission, OutboxValidationError
from shift.services.rewards import (
    CONFIRMED,
    MAX_GOAL_WORKS,
    PENDING,
    RATE_WORK_TYPES,
    REJECTED,
    REVOKED,
    AccountingCopy,
    RewardError,
    RewardRate,
    RewardRecord,
    RewardsService,
    TeamGoal,
    UnmappedEarner,
    UnratedWork,
    month_key,
)
from shift.services.shift_events import ShiftEventService

MANAGER_PATH = "/manager/rewards"
MINE_PATH = "/rewards"
NO_STORE = "no-store"
PERSIAN_DIGITS = str.maketrans("0123456789-", "۰۱۲۳۴۵۶۷۸۹−")
THOUSANDS = "٬"
# The Jalali month names, in the order `month_of` numbers them.
MONTH_NAMES = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)
STATUS_LABELS = {PENDING: "در انتظار تأیید", CONFIRMED: "تأییدشده", REJECTED: "ردشده", REVOKED: "ابطال‌شده"}
BAD_FORM_AMOUNT = "مبلغ پاداش را درست وارد کنید."
BAD_FORM_WORK = "این نوع کار را نمی‌توان دستی ثبت کرد."
_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")

# One page guard each: a signed-out browser is sent to the login screen, a non-holder gets a 403.
ManagerViewer = Annotated[Principal, Depends(deps.require_page(Permission.REWARD_GRANT))]
OwnViewer = Annotated[Principal, Depends(deps.require_page(Permission.REWARD_VIEW))]

RewardsSource = Callable[[Request], RewardsService]
ShiftEventsSource = Callable[[Request], ShiftEventService]


def build_rewards_router(*, rewards: RewardsSource, shift_events: ShiftEventsSource) -> APIRouter:
    """Build the two pages and the manager's four form posts from the services the app created."""
    pages = APIRouter(tags=["rewards"])
    RewardsDep = Annotated[RewardsService, Depends(rewards)]
    WriterDep = Annotated[ShiftEventService, Depends(shift_events)]

    @pages.get(MANAGER_PATH, response_class=HTMLResponse, summary="The manager's reward screen")
    def get_manager_rewards(
        request: Request,
        _viewer: ManagerViewer,
        service: RewardsDep,
        month: str | None = None,
    ) -> HTMLResponse:
        """One month: the copy's state, the pending confirmations, the table, the rates and the gaps."""
        key = _requested_month(month, request)
        payroll = service.payroll(key)
        return _shell_page(
            request,
            "manager_rewards.html",
            {
                "month": _month_view(key),
                "pending": [_record_row(record) for record in service.pending()],
                "rows": [_record_row(record) for record in service.records(key)],
                "totals": {
                    "earned": _number_fa(payroll.earned_toman),
                    "deducted": _number_fa(payroll.deducted_toman),
                    "net": _number_fa(payroll.net_toman),
                    "people": _number_fa(len(payroll.per_staff())),
                },
                "rates": _rate_rows(service.rates(key)),
                "unrated": [_unrated_row(entry) for entry in service.unrated(key)],
                "unmapped": [_unmapped_row(entry) for entry in service.unmapped(key)],
                "staff": [
                    {"staff_id": option.staff_id, "name": option.full_name, "acc_user": option.acc_user or ""}
                    for option in service.staff_options()
                ],
                "accounting": _copy_view(service.copied(key)),
                "max_amount": MAX_AMOUNT_TOMAN,
                "goal": _goal_view(service.team_goal(key)),
                "max_goal": MAX_GOAL_WORKS,
                "credit_work_types": [
                    {"value": work_type.value, "label": WORK_TYPE_LABELS[work_type]}
                    for work_type in CREDIT_WORK_TYPES
                ],
            },
            cache_control=NO_STORE,
        )

    @pages.post(f"{MANAGER_PATH}/rates", summary="Set one work type's amount for a month")
    def post_rate(
        manager: ManagerViewer,
        service: RewardsDep,
        work_type: Annotated[str, Form()],
        month: Annotated[str, Form()],
        amount_toman: Annotated[str, Form()],
    ) -> RedirectResponse:
        """The manager's amount per work type per month; a refused figure writes nothing."""
        try:
            service.set_rate(work_type, month, amount_toman, principal=manager)
        except RewardError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _back_to(month)

    @pages.post(f"{MANAGER_PATH}/accrue", summary="Append this month's earning records")
    def post_accrue(
        _viewer: ManagerViewer,
        service: RewardsDep,
        month: Annotated[str, Form()],
    ) -> RedirectResponse:
        """One accrual run: idempotent, so pressing it twice writes nothing the second time."""
        try:
            service.accrue(month)
        except RewardError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _back_to(month)

    @pages.post(f"{MANAGER_PATH}/revoke", summary="Take a confirmed reward back")
    def post_revoke(
        request: Request,
        manager: ManagerViewer,
        writer: WriterDep,
        month: Annotated[str, Form()],
        event_id: Annotated[str, Form()],
        reason: Annotated[str, Form()],
    ) -> RedirectResponse:
        """A revocation is a shift event like any other, so the one validated writer records it."""
        shift = deps.running_shift(request)
        moment = request.app.state.clock.now_utc()
        event_key = new_ulid(moment)
        submission = EventSubmission(
            type=REWARD_REVOKED,
            payload={"reason": reason},
            actor_id=manager.staff_id,
            device_id=manager.device_id,
            client_at=moment,
            client_event_id=event_key,
            shift_id=shift.text,
            ref_event_id=event_id,
            idempotency_key=event_key,
            shift_work_date=shift.work_date,
        )
        try:
            writer.record(submission, principal=manager, shift=shift)
        except OutboxValidationError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _back_to(month)

    @pages.post(f"{MANAGER_PATH}/credit", summary="Credit an assigned task or the follow-up path")
    def post_credit(
        request: Request,
        manager: ManagerViewer,
        writer: WriterDep,
        month: Annotated[str, Form()],
        staff_id: Annotated[str, Form()],
        work_type: Annotated[str, Form()],
        amount_toman: Annotated[str, Form()],
        note: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """D2: the manager's own closing confirmation; a follow-up credit lands once a month per person."""
        kind = work_type.strip()
        if kind not in {work_type.value for work_type in CREDIT_WORK_TYPES}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_FORM_WORK)
        amount = _form_amount(amount_toman)
        shift = deps.running_shift(request)
        moment = request.app.state.clock.now_utc()
        event_key = new_ulid(moment)
        # A manager task is credited per task; the follow-up path is confirmed by hand once a month.
        idempotency_key = (
            f"reward:follow_up:{month.strip()}:{staff_id.strip()}" if kind == "follow_up" else event_key
        )
        payload: dict[str, object] = {"staff_id": staff_id.strip(), "work_type": kind, "amount_toman": amount}
        if note.strip():
            payload["note"] = note.strip()
        submission = EventSubmission(
            type=REWARD_CONFIRMED,
            payload=payload,
            actor_id=manager.staff_id,
            device_id=manager.device_id,
            client_at=moment,
            client_event_id=event_key,
            shift_id=shift.text,
            idempotency_key=idempotency_key,
            shift_work_date=shift.work_date,
        )
        try:
            writer.record(submission, principal=manager, shift=shift)
        except OutboxValidationError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _back_to(month)

    @pages.post(f"{MANAGER_PATH}/goal", summary="Set the month's team goal")
    def post_goal(
        manager: ManagerViewer,
        service: RewardsDep,
        month: Annotated[str, Form()],
        goal_works: Annotated[str, Form()],
    ) -> RedirectResponse:
        """D2: one monthly number beside the rates; it is a display target and pays nothing by itself."""
        try:
            service.set_team_goal(month, goal_works, principal=manager)
        except RewardError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return _back_to(month)

    @pages.post(f"{MANAGER_PATH}/staff-map", summary="Map a staff row to an accounting user")
    def post_staff_map(
        manager: ManagerViewer,
        service: RewardsDep,
        staff_id: Annotated[str, Form()],
        acc_user: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """The one place an accounting user becomes a shift staff row; an empty value clears it."""
        try:
            service.map_staff(staff_id, acc_user, principal=manager)
        except RewardError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        return RedirectResponse(MANAGER_PATH, status_code=status.HTTP_303_SEE_OTHER)

    @pages.get(MINE_PATH, response_class=HTMLResponse, summary="One person's own reward standing")
    def get_my_rewards(
        request: Request,
        viewer: OwnViewer,
        service: RewardsDep,
        month: str | None = None,
    ) -> HTMLResponse:
        """Own amount, own confirmed works, own rank and the team goal — never another person."""
        key = _requested_month(month, request)
        standing = service.standing(viewer.staff_id, key)
        return _shell_page(
            request,
            "rewards_mine.html",
            {
                "month": _month_view(key),
                "staff_name": standing.staff_name,
                "own_amount": _number_fa(standing.own_toman),
                "own_works": _number_fa(standing.own_works),
                "rank": _number_fa(standing.rank),
                "headcount": _number_fa(standing.headcount),
                "team_works": _number_fa(standing.team_works),
                "goal": _progress_view(standing.team_works, standing.goal_works),
            },
            cache_control=NO_STORE,
        )

    return pages


def _requested_month(value: str | None, request: Request) -> str:
    """The month in the URL, or the month the clinic is in now; a bad value is not an error page."""
    return month_key(value, fallback=month_of(request.app.state.clock.now_utc()))


def _back_to(month: str) -> RedirectResponse:
    """Post, then read: the manager's screen answers with the month it just changed."""
    text = month.strip()
    key = text if _MONTH.fullmatch(text) else ""
    return RedirectResponse(f"{MANAGER_PATH}?month={key}", status_code=status.HTTP_303_SEE_OTHER)


def _number_fa(value: int) -> str:
    """A whole number in the clinic's own digits; a deduction keeps its minus sign."""
    return f"{value:,}".translate(PERSIAN_DIGITS).replace(",", THOUSANDS)


def _form_amount(text: str) -> int:
    """The manager's typed amount: whole, positive Toman, Persian digits accepted, never a raw error."""
    cleaned = text.strip().translate(PERSIAN_DIGITS).translate(str.maketrans("", "", "،,٬ "))
    if not cleaned.isdigit() or int(cleaned) <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_FORM_AMOUNT)
    return int(cleaned)


def _goal_view(goal: TeamGoal | None) -> dict[str, str | bool | None]:
    """The month's team goal as the form shows it: the number to fix, or the empty month."""
    return {
        "set": goal is not None,
        "amount": "" if goal is None else str(goal.goal_works),
        "amount_fa": None if goal is None else _number_fa(goal.goal_works),
        "set_by": None if goal is None else goal.set_by,
        "set_at": None if goal is None else _stamp_fa(goal.set_at),
    }


def _progress_view(team_works: int, goal_works: int | None) -> dict[str, str | int | None]:
    """D2: how far the team is towards the manager's number, as a count and a rounded percentage."""
    if goal_works is None:
        return {"set": False, "goal": None, "percent": None, "works": _number_fa(team_works)}
    percent = min(100, round(team_works * 100 / goal_works))
    return {
        "set": True,
        "goal": _number_fa(goal_works),
        "percent": _number_fa(percent),
        "works": _number_fa(team_works),
    }


def _stamp_fa(iso_text: str) -> str:
    """A stored instant as `۱۴۰۵/۰۸/۰۶ ۱۰:۰۰` in the clinic's calendar and Tehran time."""
    local = to_tehran(parse_iso(iso_text))
    return f"{format_jalali(local.date())} {local:%H:%M}".translate(PERSIAN_DIGITS)


def _month_view(key: str) -> dict[str, str]:
    """The month's key and its Persian name, plus the two neighbours the arrows point at."""
    year, _, number = key.partition("-")
    return {
        "key": key,
        "label": f"{MONTH_NAMES[int(number) - 1]} {_number_fa(int(year))}",
        "previous": previous_month(key),
        "next": next_month(key),
    }


def _record_row(record: RewardRecord) -> dict[str, object]:
    """One reward record formatted once here, so the template carries text and no figure logic."""
    return {
        "event_id": record.event_id,
        "staff": record.staff_name,
        "work": WORK_TYPE_LABELS.get(record.work_type, record.work_type),
        "amount": _number_fa(record.amount_toman),
        "at": _stamp_fa(record.at_tehran),
        "note": record.note,
        "status": STATUS_LABELS.get(record.status, record.status),
        "revocable": record.status == CONFIRMED,
        "deduction": record.amount_toman < 0,
    }


def _rate_rows(rates: list[RewardRate]) -> list[dict[str, object]]:
    """Every rate work type with the amount this month holds, so the form shows what is missing."""
    stored = {rate.work_type: rate for rate in rates}
    rows: list[dict[str, object]] = []
    for work_type in RATE_WORK_TYPES:
        rate = stored.get(work_type)
        rows.append(
            {
                "work_type": work_type,
                "label": WORK_TYPE_LABELS.get(work_type, work_type),
                "amount": "" if rate is None else str(rate.amount_toman),
                "amount_fa": None if rate is None else _number_fa(rate.amount_toman),
                "set_by": None if rate is None else rate.set_by,
                "set_at": None if rate is None else _stamp_fa(rate.set_at),
            }
        )
    return rows


def _unrated_row(entry: UnratedWork) -> dict[str, str]:
    """Works of one type that earned nothing because the month has no rate for them."""
    return {"work": WORK_TYPE_LABELS.get(entry.work_type, entry.work_type), "works": _number_fa(entry.works)}


def _unmapped_row(entry: UnmappedEarner) -> dict[str, str]:
    """One accounting user nobody mapped, with how many works of one type wait for a person."""
    return {
        "acc_user": entry.acc_user,
        "work": WORK_TYPE_LABELS.get(entry.work_type, entry.work_type),
        "works": _number_fa(entry.works),
    }


def _copy_view(copy: AccountingCopy) -> dict[str, object]:
    """What the accounting copy holds: two counts and whether it has ever been read at all."""
    return {
        "vitals": _number_fa(copy.vitals),
        "registrations": _number_fa(copy.registrations),
        "empty": not copy.any_rows,
    }
