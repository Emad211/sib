"""«گزارش کمپین» (S01 §3 «ت», review finding F8): what the clinic's campaigns actually gave away.

The owner's event table sends every `campaign.discount_applied` to a campaign report, and the manager
reads it for one question: did the campaign that was defined bring anyone in, and what did it cost him.
So the report is one line per campaign of the calendar — how many discounts were applied in the window,
the campaign's own amount, and the total those discounts gave away. A campaign with no discount in the
window keeps its line with a zero, because a silent campaign is exactly the finding; hiding it behind an
empty table would hide the answer.

The report is derived on every call, like `open_decisions` and `my_queue`: the log is the source and
nothing is stored (R-03), so a rebuild can never disagree with it. Its window is two Tehran midnights
opened and closed by the daily card's own `CardWindow`, so a discount at 23:59 of the last day counts
and one at 00:00 of the next day does not; with no range the caller gets the Jalali month the accountant
is closing.

A discount the log holds for a campaign the calendar no longer has still counts — it happened — and it
is labelled by its id. Its amount is unknown, and this module never invents one: the line says so and
the totals leave it out.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from shift.adapters.db import events_repo
from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, to_tehran, utc_iso
from shift.common.jalali import gregorian_to_jalali, jalali_to_gregorian
from shift.domain.daily_card import CardWindow
from shift.domain.event_types import EventType
from shift.services.campaigns import CampaignService

DISCOUNT = EventType.CAMPAIGN_DISCOUNT_APPLIED.value


class ReportRangeError(ValueError):
    """The range this caller asked for is not a window a report can be read over."""


@dataclass(frozen=True, slots=True)
class CampaignLine:
    """One campaign of the window: what it gave away, or a zero when the month kept it silent.

    `name` and `amount_toman` are None for a campaign the calendar no longer holds; the row then reads
    by its id, `count` still says how many discounts happened, and `total_toman` stays unknown.
    """

    campaign_id: str
    name: str | None
    service: str | None
    amount_toman: int | None
    count: int
    total_toman: int | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "service": self.service,
            "amount_toman": self.amount_toman,
            "count": self.count,
            "total_toman": self.total_toman,
        }


@dataclass(frozen=True, slots=True)
class CampaignReport:
    """One window's campaign report: every campaign's line, and the figures the manager adds up.

    `count` is every discount of the window; `total_toman` adds up only the lines whose campaign still
    has an amount, and `unpriced_count` says how many discounts could not be priced at all.
    """

    first_day: date
    last_day: date
    lines: tuple[CampaignLine, ...]
    count: int
    total_toman: int
    unpriced_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "from": self.first_day.isoformat(),
            "to": self.last_day.isoformat(),
            "lines": [line.to_payload() for line in self.lines],
            "count": self.count,
            "total_toman": self.total_toman,
            "unpriced_count": self.unpriced_count,
        }


class CampaignReportService:
    """Reads the «ت» discounts of a window and prices them from the manager's campaign calendar."""

    def __init__(self, database: Database, *, campaigns: CampaignService, clock: Clock | None = None) -> None:
        self._database = database
        self._campaigns = campaigns
        self._clock = clock or SystemClock()

    def report(self, *, first: date | None = None, last: date | None = None) -> CampaignReport:
        """Every campaign's line over `[first, last]`; with no range, the current Jalali month.

        The bounds are days of the clinic's calendar, both included: the window it reads is the two
        Tehran midnights around them, exactly as the daily card cuts its own.
        """
        month_first, month_last = _month_range(to_tehran(self._clock.now_utc()).date())
        first_day = month_first if first is None else first
        last_day = month_last if last is None else last
        if first_day > last_day:
            raise ReportRangeError("بازهٔ تاریخ درست نیست: روز شروع باید پیش از روز پایان باشد.")

        counts = self._counts(CardWindow(last_day, first_day, last_day))
        lines: list[CampaignLine] = []
        for campaign in self._campaigns.all():
            count = counts.pop(campaign.campaign_id, 0)
            lines.append(
                CampaignLine(
                    campaign_id=campaign.campaign_id,
                    name=campaign.name,
                    service=campaign.service,
                    amount_toman=campaign.amount_toman,
                    count=count,
                    total_toman=count * campaign.amount_toman,
                )
            )
        # A discount whose campaign left the calendar: it happened, so it takes a line of its own.
        lines.extend(
            CampaignLine(campaign_id, None, None, None, count, None) for campaign_id, count in sorted(counts.items())
        )
        return CampaignReport(
            first_day=first_day,
            last_day=last_day,
            lines=tuple(lines),
            count=sum(line.count for line in lines),
            total_toman=sum(line.total_toman or 0 for line in lines),
            unpriced_count=sum(line.count for line in lines if line.total_toman is None),
        )

    def _counts(self, window: CardWindow) -> dict[str, int]:
        """How many discounts the log holds per campaign between the window's two Tehran midnights."""
        counts: dict[str, int] = {}
        with self._database.read() as connection:
            for event in events_repo.iter_between(connection, after=utc_iso(window.start), until=utc_iso(window.end)):
                if event.type != DISCOUNT:
                    continue
                campaign_id = str(event.payload.get("campaign_id") or "")
                counts[campaign_id] = counts.get(campaign_id, 0) + 1
        return counts


def _month_range(day: date) -> tuple[date, date]:
    """The first and last Gregorian day of the Jalali month that holds `day`.

    The last day is one before the first day of the next month, so the short month of Esfand and every
    31-day month come out right without a table of month lengths.
    """
    year, month, _day = gregorian_to_jalali(day.year, day.month, day.day)
    first = date(*jalali_to_gregorian(year, month, 1))
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    last = date(*jalali_to_gregorian(next_year, next_month, 1)) - timedelta(days=1)
    return first, last
