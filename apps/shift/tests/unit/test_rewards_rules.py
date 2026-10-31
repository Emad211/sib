"""پاداش rules (S01 §5, SHF-5): which month pays, which month deducts, the rank and H-06.

A wrong month or a wrong sum costs a real person real wages, so the four month rules, the ledger
arithmetic and the competition rank are pinned here. The last test is H-06 in one place: a stock or
cash shortage may never reach a reward, which means no shortage figure may even enter the rules
module. Every instant below is invented (ADR-006).
"""

import ast
from datetime import UTC, datetime
from pathlib import Path

from shift.common.clock import TEHRAN
from shift.common.jalali import jalali_to_gregorian
from shift.domain import rewards
from shift.domain.rewards import (
    Payroll,
    PayrollLine,
    RewardLine,
    WorkType,
    deducted_in,
    deduction_month,
    earned_in,
    month_of,
    net_toman,
    next_month,
    payment_month,
    previous_month,
    standing_of,
    works_by_staff,
)

RULES = Path(__file__).resolve().parents[2] / "src" / "shift" / "domain" / "rewards.py"
# Nothing in this module may know a shortage exists: H-06 keeps the shelf and the box out of a reward.
FORBIDDEN = ("discrepanc", "cash", "handover", "stock", "shortage")


def _tehran(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    """An instant on the clinic's wall clock, which is what every month rule is written in."""
    return datetime(year, month, day, hour, minute, tzinfo=TEHRAN)


def _jalali_at(year: int, month: int, day: int, hour: int = 12) -> datetime:
    """The Tehran instant of one Jalali civil day."""
    gregorian_year, gregorian_month, gregorian_day = jalali_to_gregorian(year, month, day)
    return _tehran(gregorian_year, gregorian_month, gregorian_day, hour)


def _line(staff_id: str, amount: int, at: datetime) -> RewardLine:
    """One ledger record: the rules never care whether a caller reads it as a work or a deduction."""
    return RewardLine(staff_id=staff_id, work_type=WorkType.VITALS, amount_toman=amount, at=at)


def test_a_month_key_is_the_jalali_month_of_a_tehran_instant() -> None:
    """1405/08/04 is 2026-10-26, so the key reads the way the clinic writes the month."""
    assert month_of(_tehran(2026, 10, 28)) == "1405-08"
    assert month_of(_tehran(2026, 10, 26, 0, 0)) == "1405-08"
    assert month_of(_tehran(2026, 10, 22, 23, 59)) == "1405-07"


def test_tehran_midnight_decides_the_month_not_utc() -> None:
    """At 20:29 UTC it is still 23:59 in Tehran; half a minute later the clinic is in the next month."""
    assert month_of(datetime(2026, 10, 22, 20, 29, tzinfo=UTC)) == "1405-07"
    assert month_of(datetime(2026, 10, 22, 20, 30, tzinfo=UTC)) == "1405-08"


def test_the_last_day_of_esfand_is_its_own_month_and_farwardin_starts_the_year() -> None:
    """The year rollover is the month rollover, and neither of them is Gregorian."""
    last_day = _jalali_at(1405, 12, 29, 23)
    first_day = _jalali_at(1406, 1, 1, 0)

    assert (month_of(last_day), month_of(first_day)) == ("1405-12", "1406-01")


def test_previous_and_next_month_walk_the_year_backwards_and_forwards() -> None:
    assert previous_month("1405-08") == "1405-07"
    assert previous_month("1405-01") == "1404-12"
    assert next_month("1405-08") == "1405-09"
    assert next_month("1405-12") == "1406-01"


def test_a_reward_is_paid_in_the_month_it_was_confirmed_not_the_month_of_the_work() -> None:
    """The owner's default: the work may happen in Mordad and still be paid with Mehr."""
    assert payment_month(_jalali_at(1405, 9, 1)) == "1405-09"
    assert payment_month(_jalali_at(1405, 8, 30, 23)) == "1405-08"


def test_a_revocation_is_deducted_in_the_month_after_it() -> None:
    assert deduction_month(_jalali_at(1405, 8, 15)) == "1405-09"
    assert deduction_month(_jalali_at(1405, 12, 29, 23)) == "1406-01"
    assert deduction_month(_jalali_at(1405, 8, 1)) != "1405-08"


def test_earned_in_a_month_is_the_works_confirmed_in_that_month() -> None:
    works = [
        _line("staff-a", 50_000, _jalali_at(1405, 8, 6)),
        _line("staff-b", 70_000, _jalali_at(1405, 8, 30, 23)),
        _line("staff-a", 90_000, _jalali_at(1405, 9, 1)),
    ]

    assert earned_in("1405-08", works) == 120_000
    assert earned_in("1405-09", works) == 90_000
    assert earned_in("1405-07", works) == 0


def test_deducted_in_a_month_is_the_revocations_whose_next_month_it_is() -> None:
    revoked = [
        _line("staff-a", 50_000, _jalali_at(1405, 8, 6)),
        _line("staff-b", 30_000, _jalali_at(1405, 8, 30, 23)),
        _line("staff-a", 10_000, _jalali_at(1405, 12, 29)),
    ]

    assert deducted_in("1405-08", revoked) == 0
    assert deducted_in("1405-09", revoked) == 80_000
    assert deducted_in("1406-01", revoked) == 10_000


def test_net_is_earned_minus_deducted() -> None:
    assert net_toman(120_000, 80_000) == 40_000
    assert net_toman(0, 80_000) == -80_000


def test_the_work_count_of_a_month_is_per_person() -> None:
    works = [
        _line("staff-a", 50_000, _jalali_at(1405, 8, 6)),
        _line("staff-a", 50_000, _jalali_at(1405, 8, 7)),
        _line("staff-b", 50_000, _jalali_at(1405, 8, 8)),
        _line("staff-c", 50_000, _jalali_at(1405, 9, 1)),
    ]

    assert works_by_staff("1405-08", works) == {"staff-a": 2, "staff-b": 1}


def test_a_payroll_keeps_a_deduction_line_negative_and_names_every_person_once() -> None:
    payroll = Payroll(
        month="1405-08",
        lines=(
            PayrollLine("staff-a", "پرستار یک", "vitals", "vitals", 50_000, "2026-10-28T10:00:00.000000+03:30", None),
            PayrollLine("staff-b", "پذیرش یک", "registration", "registration", 20_000, "", None),
            PayrollLine("staff-a", "پرستار یک", "vitals", "revoked", -10_000, "", "ثبت جعلی"),
        ),
        earned_toman=70_000,
        deducted_toman=10_000,
        net_toman=60_000,
    )

    assert payroll.per_staff() == (("staff-a", "پرستار یک", 40_000), ("staff-b", "پذیرش یک", 20_000))


def test_the_rank_counts_confirmed_works_and_a_tie_shares_the_rank() -> None:
    """The competition is a count of confirmed works, so two equal counts are one place."""
    works = [
        *(_line("staff-a", 1_000, _jalali_at(1405, 8, day)) for day in (1, 2, 3)),
        *(_line("staff-b", 1_000, _jalali_at(1405, 8, day)) for day in (4, 5, 6)),
        _line("staff-c", 1_000, _jalali_at(1405, 8, 7)),
    ]

    first = standing_of("staff-a", "پرستار یک", "1405-08", works, [])
    last = standing_of("staff-c", "پذیرش یک", "1405-08", works, [])

    assert (first.rank, first.headcount, first.team_works) == (1, 3, 7)
    assert (last.rank, last.headcount, last.team_works) == (3, 3, 7)
    assert (first.own_works, first.own_toman) == (3, 3_000)


def test_a_standing_carries_only_the_caller_own_figures() -> None:
    """Nothing here exposes another person: one name, one amount, one rank (S01 §5)."""
    works = [
        _line("staff-a", 50_000, _jalali_at(1405, 8, 6)),
        _line("staff-b", 900_000, _jalali_at(1405, 8, 7)),
        _line("staff-b", 900_000, _jalali_at(1405, 8, 8)),
    ]
    revoked = [_line("staff-b", 900_000, _tehran(2026, 10, 1))]

    standing = standing_of("staff-a", "پرستار یک", "1405-08", works, revoked)

    assert (standing.staff_id, standing.staff_name) == ("staff-a", "پرستار یک")
    assert (standing.own_toman, standing.own_works) == (50_000, 1)
    assert (standing.rank, standing.headcount, standing.team_works) == (2, 2, 3)


def test_a_person_with_no_confirmed_work_is_still_one_of_the_month_participants() -> None:
    """«رتبهٔ شما: ۶ از ۵» is not a standing: the caller may never out-rank the headcount (P-03)."""
    works = [_line(f"staff-{letter}", 1_000, _jalali_at(1405, 8, day)) for day, letter in enumerate("abcde", start=1)]

    last = standing_of("staff-new", "پرستار تازه", "1405-08", works, [])
    empty = standing_of("staff-new", "پرستار تازه", "1405-08", [], [])

    assert (last.rank, last.headcount, last.own_works) == (6, 6, 0)
    assert (empty.rank, empty.headcount) == (1, 1)


def test_a_shortage_or_cash_figure_cannot_reach_a_reward() -> None:
    """H-06: a warning only, never a deduction — so the rules module may not even know one exists."""
    tree = ast.parse(RULES.read_text(encoding="utf-8"), filename=str(RULES))
    imported = [
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]

    assert not [module for module in imported if any(word in module for word in FORBIDDEN)]
    assert not [name for name in vars(rewards) if any(word in name.lower() for word in FORBIDDEN)]
