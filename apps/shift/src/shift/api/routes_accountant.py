"""The accountant's pages: the money queue and the campaign report (S01 §3, «کار حسابدار», F8).

Two screens, one person's work. The queue is what the owner's document gives the accountant — the
card-to-card receipts she matches, the withdrawals that left the box, the refunds waiting for the doctor
and the month's reward list — and the campaign report is where the event table sends every «ت» discount
so the manager can see what his campaigns cost.

Server-rendered and phone-first, like the manager's pages (R-10): each page does one job, every page
declares exactly one guard through `deps.require_page`, so a role without the permission gets a 403
whose body holds no item and no figure at all, and a signed-out browser is sent to the login screen.
Both pages answer `Cache-Control: no-store`: an accountant's figures must not sit in a shared cache.
No page carries a reference number (R-02, ADR-011).

The decision buttons post the phone's own `event.approved` / `event.rejected` through the existing
`/api/v1/events` endpoint; the page's script only copies the token out of the shell's meta tag. There is
no second decision endpoint here, because one money path is the whole point of the queue.
"""

from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from shift.api import deps
from shift.api.routes_web import _shell_page
from shift.common.clock import parse_iso, to_tehran
from shift.common.jalali import format_jalali
from shift.domain.permissions import Permission
from shift.domain.price_history import PriceSeries
from shift.services.accountant_queue import (
    PENDING,
    AccountantQueueService,
    CardTransferLine,
    PayrollView,
    RefundLine,
    WithdrawalLine,
)
from shift.services.auth import Principal
from shift.services.campaign_report import CampaignLine, CampaignReport, CampaignReportService
from shift.services.price_history import PriceHistoryService

QUEUE_PATH = "/accountant/queue"
CAMPAIGNS_PATH = "/accountant/campaigns"
PRICES_PATH = "/accountant/prices"
NO_STORE = "no-store"
UNKNOWN = "نامشخص"
NO_VENDOR = "بدون فروشنده"
RANGE_REFUSED = "بازهٔ تاریخ درست نیست؛ ماه جاری نمایش داده شد."
# The one state a row of these sections can be in: everything decided has left the queue.
STATE_LABELS = {PENDING: "در انتظار تصمیم"}

# A page guard each: a signed-out browser is sent to the login screen, a role without it gets a 403.
QueueViewer = Annotated[Principal, Depends(deps.require_page(Permission.ACCOUNTANT_QUEUE_VIEW))]
ReportViewer = Annotated[Principal, Depends(deps.require_page(Permission.CAMPAIGN_REPORT_VIEW))]

QueueService = Callable[[Request], AccountantQueueService]
ReportService = Callable[[Request], CampaignReportService]


def build_accountant_router(*, queue: QueueService, report: ReportService) -> APIRouter:
    """Build the accountant's pages; the two services come from the app state, never from a global."""
    pages = APIRouter(tags=["accountant"])
    QueueDep = Annotated[AccountantQueueService, Depends(queue)]
    ReportDep = Annotated[CampaignReportService, Depends(report)]

    @pages.get(QUEUE_PATH, response_class=HTMLResponse, summary="The accountant's open money work")
    def get_accountant_queue(request: Request, viewer: QueueViewer, service: QueueDep) -> HTMLResponse:
        """The three money sections and the month's rewards, oldest first, with a muted empty line."""
        view = service.queue(viewer)
        return _shell_page(
            request,
            "accountant_queue.html",
            {
                "card_transfers": [_transfer_row(line) for line in view.card_transfers],
                "withdrawals": [_withdrawal_row(line) for line in view.withdrawals],
                "refunds": [_refund_row(line) for line in view.refunds],
                "payroll": _payroll_view(view.payroll),
            },
            cache_control=NO_STORE,
        )

    @pages.get(CAMPAIGNS_PATH, response_class=HTMLResponse, summary="What the campaigns gave away")
    def get_campaign_report(
        request: Request,
        _viewer: ReportViewer,
        service: ReportDep,
        first: Annotated[str | None, Query(alias="from")] = None,
        last: Annotated[str | None, Query(alias="to")] = None,
    ) -> HTMLResponse:
        """One line per campaign over the caller's two ISO days, or over the current Jalali month.

        A range the clinic cannot read — a malformed day, or one that runs backwards — is answered with
        the month the accountant is closing and one line saying so, never with a 500.
        """
        try:
            view = service.report(first=_day(first), last=_day(last))
            message = ""
        except ValueError:
            # A malformed day and a backwards range are the same answer for a browser: the month.
            view = service.report()
            message = RANGE_REFUSED
        return _shell_page(
            request,
            "campaign_report.html",
            {"report": _report_view(view), "message": message},
            cache_control=NO_STORE,
        )

    @pages.get(PRICES_PATH, response_class=HTMLResponse, summary="The price history per item and vendor")
    def get_price_history(request: Request, _viewer: QueueViewer) -> HTMLResponse:
        """F2: every price the clinic paid, per item and vendor, read from the digitized invoices."""
        service = _price_history_service(request)
        names = service.item_names()
        return _shell_page(
            request,
            "accountant_prices.html",
            {"series": [_series_row(series, names) for series in service.series()]},
            cache_control=NO_STORE,
        )

    return pages


def _price_history_service(request: Request) -> PriceHistoryService:
    """The price screen's reader: the log plus the catalog's item names, both from the lifespan."""
    return PriceHistoryService(request.app.state.database, request.app.state.catalog)


def _series_row(series: PriceSeries, names: dict[str, str]) -> dict[str, Any]:
    """One item from one vendor: its prices oldest first, newest price and the change it carries."""
    return {
        "item": names.get(series.item_id, UNKNOWN),
        "vendor": series.vendor or NO_VENDOR,
        "latest": f"{series.latest:,}",
        "change": f"{series.change_toman:,}" if len(series.points) > 1 else None,
        "points": [
            {
                "at": _stamp_fa(point.at_tehran),
                "price": f"{point.unit_price_toman:,}",
                "quantity": f"{point.quantity:,}",
                "invoice": point.invoice_reference or UNKNOWN,
            }
            for point in reversed(series.points)
        ],
    }


# --- formatting: one layer, the clinic's own helpers -------------------------------------------------


def _day(text: str | None) -> date | None:
    """An ISO day from the range form, or None when the caller left the field empty."""
    if text is None or not text.strip():
        return None
    return date.fromisoformat(text.strip())


def _stamp_fa(iso_text: str) -> str:
    """`1405/08/04 14:10` for the Tehran ISO text the log stores."""
    local = to_tehran(parse_iso(iso_text))
    return f"{format_jalali(local.date())} {local:%H:%M}"


def _amount_fa(amount: int | None) -> str:
    """A Toman figure the way every other page writes one, or the word for "nobody knows"."""
    return UNKNOWN if amount is None else f"{amount:,}"


def _side_fa(above_cap: bool | None) -> str:
    """Which side of the cap a withdrawal was on; a record with no readable shift says it cannot tell."""
    if above_cap is None:
        return "سقف نامشخص"
    return "بالای سقف" if above_cap else "تا سقف"


def _transfer_row(line: CardTransferLine) -> dict[str, Any]:
    """«ک»: amount, admission number, who reported it and when, plus its own two buttons."""
    return {
        "event_id": line.event_id,
        "decidable": line.decidable,
        "amount": f"{line.amount_toman:,}",
        "admission_no": line.admission_no,
        "actor": line.actor_name,
        "at": _stamp_fa(line.at_tehran),
        "state": STATE_LABELS.get(line.status, line.status),
        "photo": _photo_url(line.photo_sha256),
    }


def _withdrawal_row(line: WithdrawalLine) -> dict[str, Any]:
    """«د»: the amount, the recipient, the reason and whether it was over the cap of its own shift."""
    return {
        "event_id": line.event_id,
        "decidable": line.decidable,
        "amount": f"{line.amount_toman:,}",
        "recipient": line.recipient_name,
        "reason": line.reason,
        "actor": line.actor_name,
        "at": _stamp_fa(line.at_tehran),
        "side": _side_fa(line.above_cap),
        "cap": UNKNOWN if line.cap_toman is None else f"{line.cap_toman:,}",
        "state": STATE_LABELS.get(line.status, line.status),
        "photo": _photo_url(line.photo_sha256),
    }


def _photo_url(sha256: str | None) -> str | None:
    """The receipt behind a row, as a link the accountant opens; never a hash printed for staff (SHF-7)."""
    return f"/api/v1/photos/{sha256}" if sha256 else None


def _refund_row(line: RefundLine) -> dict[str, Any]:
    """«ب»: the admission whose money should come back, why, and who asked for it."""
    return {
        "event_id": line.event_id,
        "decidable": line.decidable,
        "admission_no": line.admission_no,
        "reason": line.reason,
        "actor": line.actor_name,
        "at": _stamp_fa(line.at_tehran),
        "state": STATE_LABELS.get(line.status, line.status),
    }


def _payroll_view(payroll: PayrollView | None) -> dict[str, Any] | None:
    """The month's rewards as the accountant adds them to the payslip, or None when there are none."""
    if payroll is None:
        return None
    return {
        "month": payroll.month.replace("-", "/"),
        "lines": [
            {
                "staff": line.staff_name,
                "label": line.label,
                "amount": f"{line.amount_toman:,}",
                "at": _stamp_fa(line.at_tehran),
                "note": line.note,
            }
            for line in payroll.lines
        ],
        "staff": [{"staff": pay.staff_name, "net": f"{pay.net_toman:,}"} for pay in payroll.staff],
        "earned": f"{payroll.earned_toman:,}",
        "deducted": f"{payroll.deducted_toman:,}",
        "net": f"{payroll.net_toman:,}",
    }


def _report_view(report: CampaignReport) -> dict[str, Any]:
    """The report's own rows, each one written out here so the template carries no figure logic."""
    return {
        "from": report.first_day.isoformat(),
        "to": report.last_day.isoformat(),
        "from_fa": format_jalali(report.first_day),
        "to_fa": format_jalali(report.last_day),
        "lines": [_campaign_row(line) for line in report.lines],
        "count": report.count,
        "total": f"{report.total_toman:,}",
        "unpriced": report.unpriced_count,
    }


def _campaign_row(line: CampaignLine) -> dict[str, Any]:
    """One campaign: its name (or its id, when the calendar lost it), what it gave and how many times."""
    return {
        "campaign_id": line.campaign_id,
        "name": line.name or line.campaign_id,
        "service": line.service,
        "amount": _amount_fa(line.amount_toman),
        "count": line.count,
        "total": _amount_fa(line.total_toman),
        "silent": line.count == 0,
        "unknown": line.amount_toman is None,
    }
