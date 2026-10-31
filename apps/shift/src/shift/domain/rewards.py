"""پاداش (S01 §5, SHF-5): the reward rules in one module — pure, zero IO, and no shortage.

The clinic's provisional decisions (shf5-DESIGN.md §3, the owner may still change them) are code here:
a reward belongs to the month it was **confirmed** in, never the month of the work; a revoked reward
keeps its issued total there and is deducted from the **next** month, because the log is append-only
and a month that has been paid is never rewritten; work with no rate for its month earns no record and
is listed for the manager instead, so setting the rate later lets the same work accrue (accrual is
idempotent); a stock or cash shortage never reduces a reward (H-06: a warning only), so no shortage,
cash or shelf figure is imported here and no function could subtract one; the competition shows a
person their own amount, their own rank and the team goal, and nothing about anybody else.

Money leaves this module as a whole Toman integer; `Payroll` is the accountant's month, `Standing` is
one person's screen, and `month_of` is the single place the clinic's month key is cut.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from shift.common.clock import to_tehran
from shift.common.jalali import gregorian_to_jalali

MONTHS_IN_YEAR = 12


class WorkType(StrEnum):
    """The rewarded works of the owner's table; none of them is a button on the staff phone."""

    VITALS = "vitals"  # قند و فشار — automatic, from accounting's own injections
    REGISTRATION = "registration"  # ثبت بیمار — a valid national ID and a valid, unique mobile
    MANAGER_TASK = "manager_task"  # کار ارجاعی مدیر — the manager credits the closed task


# The labels every screen prints; keyed by `WorkType`, so a stored string finds its label too.
WORK_TYPE_LABELS: Mapping[WorkType, str] = {
    WorkType.VITALS: "قند و فشار",
    WorkType.REGISTRATION: "ثبت بیمار",
    WorkType.MANAGER_TASK: "کار ارجاعی مدیر",
}


def month_of(instant: datetime) -> str:
    """The Jalali month key `"1405-08"` of a Tehran instant; UTC would cut the month in the evening."""
    local = to_tehran(instant)
    year, month, _day = gregorian_to_jalali(local.year, local.month, local.day)
    return f"{year:04d}-{month:02d}"


def _shift_month(month: str, delta: int) -> str:
    """Move one month key forward or back, rolling the year at 12 → next year's 1."""
    year, _, number = str(month).strip().partition("-")
    if not (year.isdigit() and number.isdigit() and 1 <= int(number) <= MONTHS_IN_YEAR):
        raise ValueError(f"month must be a Jalali YYYY-MM key, got {month!r}")
    index = int(year) * MONTHS_IN_YEAR + int(number) - 1 + delta
    return f"{index // MONTHS_IN_YEAR:04d}-{index % MONTHS_IN_YEAR + 1:02d}"


def previous_month(month: str) -> str:
    """The month before `month`: `1405-01` → `1404-12`."""
    return _shift_month(month, -1)


def next_month(month: str) -> str:
    """The month after `month`: `1405-12` → `1406-01`."""
    return _shift_month(month, 1)


def payment_month(confirmed_at: datetime) -> str:
    """The month a reward is paid in: the month its confirmation landed in."""
    return month_of(confirmed_at)


def deduction_month(revoked_at: datetime) -> str:
    """The month a revocation is deducted in: the month after it, never its own."""
    return next_month(month_of(revoked_at))


@dataclass(frozen=True, slots=True)
class RewardLine:
    """One ledger record as the rules read it: who, how much, and when the clinic closed it.

    A confirmation and a revocation have the same shape and differ only in which function reads
    them (`earned_in` or `deducted_in`), so a shortage line has no type that could ever reach them.
    """

    staff_id: str
    work_type: str
    amount_toman: int
    at: datetime


def earned_in(month: str, lines: Iterable[RewardLine]) -> int:
    """Everything confirmed in `month`, whatever month the work itself happened in."""
    return sum(line.amount_toman for line in lines if payment_month(line.at) == month)


def deducted_in(month: str, lines: Iterable[RewardLine]) -> int:
    """Everything to take back in `month`: the revocations of the month before it."""
    return sum(line.amount_toman for line in lines if deduction_month(line.at) == month)


def net_toman(earned_toman: int, deducted_toman: int) -> int:
    """A month's answer: what was earned minus what is taken back, negative when it runs short."""
    return earned_toman - deducted_toman


def works_by_staff(month: str, lines: Iterable[RewardLine]) -> dict[str, int]:
    """Confirmed works per person in `month`: the count the competition ranks, not the amount."""
    counts: dict[str, int] = {}
    for line in lines:
        if payment_month(line.at) == month:
            counts[line.staff_id] = counts.get(line.staff_id, 0) + 1
    return counts


def net_by_staff(month: str, confirmed: Iterable[RewardLine], revoked: Iterable[RewardLine]) -> dict[str, int]:
    """What each person is owed for `month`: their confirmations minus that month's deductions."""
    totals: dict[str, int] = {}
    for line in confirmed:
        if payment_month(line.at) == month:
            totals[line.staff_id] = totals.get(line.staff_id, 0) + line.amount_toman
    for line in revoked:
        if deduction_month(line.at) == month:
            totals[line.staff_id] = totals.get(line.staff_id, 0) - line.amount_toman
    return totals


@dataclass(frozen=True, slots=True)
class PayrollLine:
    """One row of the accountant's list; a deduction line carries a negative amount."""

    staff_id: str
    staff_name: str
    work_type: str
    source: str
    amount_toman: int
    at_tehran: str
    note: str | None


@dataclass(frozen=True, slots=True)
class Payroll:
    """One month of the payroll: its lines, what it earned, what it takes back and the net."""

    month: str
    lines: tuple[PayrollLine, ...]
    earned_toman: int
    deducted_toman: int
    net_toman: int

    def per_staff(self) -> tuple[tuple[str, str, int], ...]:
        """`(staff_id, name, net)` per person, by name: what the accountant adds to a payslip."""
        names = {line.staff_id: line.staff_name for line in self.lines}
        nets: dict[str, int] = {}
        for line in self.lines:
            nets[line.staff_id] = nets.get(line.staff_id, 0) + line.amount_toman
        return tuple(sorted((staff_id, names[staff_id], net) for staff_id, net in nets.items() if net))


@dataclass(frozen=True, slots=True)
class Standing:
    """What one person may see of the competition: their own figures and the team's goal."""

    staff_id: str
    staff_name: str
    month: str
    own_toman: int
    own_works: int
    rank: int
    headcount: int
    team_works: int


def standing_of(
    staff_id: str,
    staff_name: str,
    month: str,
    confirmed: Sequence[RewardLine],
    revoked: Sequence[RewardLine],
) -> Standing:
    """One person's screen: own net, own count, rank by count of confirmed works, and the team total.

    The rank is a competition rank over how many works each participant got confirmed, so equal
    counts share a rank (1, 1, 3). `headcount` is how many people were part of the month at all;
    `team_works` is the owner's team goal: every confirmed work of the month.
    """
    counts = works_by_staff(month, confirmed)
    own_works = counts.get(staff_id, 0)
    return Standing(
        staff_id=staff_id,
        staff_name=staff_name,
        month=month,
        own_toman=net_by_staff(month, confirmed, revoked).get(staff_id, 0),
        own_works=own_works,
        rank=1 + sum(1 for count in counts.values() if count > own_works),
        headcount=len(counts),
        team_works=sum(counts.values()),
    )
