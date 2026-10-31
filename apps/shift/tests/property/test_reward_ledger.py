"""The ledger is the log, recomputed (SHF-5): a random history must equal the naive sum.

The whole ledger is two rules — a confirmation pays in the month it landed in, a revocation is
deducted in the month after it — and both are counted from the same append-only records the manager
and the accountant see. A random history of both, across four years of Tehran instants and every
month and year boundary, must reproduce month by month exactly what a naive walk over the same
instants finds, and no deduction may ever land in the month the revocation itself happened in (a
month that has been paid is never rewritten). Every value is generated; nothing real is read.
"""

from datetime import datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shift.common.clock import TEHRAN
from shift.common.jalali import gregorian_to_jalali
from shift.domain.rewards import (
    RewardLine,
    WorkType,
    deducted_in,
    deduction_month,
    earned_in,
    month_of,
    net_by_staff,
    net_toman,
)

# Four years of instants, minute by minute, so every month and year boundary is reachable.
START = datetime(2024, 1, 1, tzinfo=TEHRAN)
MINUTES = 4 * 365 * 24 * 60
STAFF = ("staff-a", "staff-b", "staff-c")
WORK_TYPES = (WorkType.VITALS, WorkType.REGISTRATION)
AMOUNTS = st.integers(min_value=0, max_value=5_000_000)
INSTANTS = st.integers(min_value=0, max_value=MINUTES).map(lambda offset: START + timedelta(minutes=offset))

lines = st.builds(
    lambda staff, work_type, amount, moment: RewardLine(staff, work_type, amount, moment),
    st.sampled_from(STAFF),
    st.sampled_from(WORK_TYPES),
    AMOUNTS,
    INSTANTS,
)
histories = st.builds(
    lambda confirmed, revoked: (confirmed, revoked), st.lists(lines, max_size=25), st.lists(lines, max_size=25)
)


def _month_index(month: str) -> int:
    """`1405-08` as one comparable number, so "the next month" is arithmetic and not a string."""
    year, _, number = month.partition("-")
    return int(year) * 12 + int(number) - 1


def _naive_month_index(moment: datetime) -> int:
    """The Jalali month of a Tehran instant, computed straight from the calendar conversion."""
    local = moment.astimezone(TEHRAN)
    year, month, _day = gregorian_to_jalali(local.year, local.month, local.day)
    return year * 12 + month - 1


def _months(history: tuple[list[RewardLine], list[RewardLine]]) -> list[str]:
    """Every month the history touches and the month after each, as the keys the rules answer in."""
    indexes = {
        index
        for line in (*history[0], *history[1])
        for index in (_naive_month_index(line.at), _naive_month_index(line.at) + 1)
    }
    return sorted(f"{index // 12:04d}-{index % 12 + 1:02d}" for index in indexes)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(histories)
def test_the_derived_ledger_equals_a_naive_recomputation(history: tuple[list[RewardLine], list[RewardLine]]) -> None:
    confirmed, revoked = history

    for month in _months(history):
        index = _month_index(month)
        naive_earned = sum(line.amount_toman for line in confirmed if _naive_month_index(line.at) == index)
        naive_deducted = sum(line.amount_toman for line in revoked if _naive_month_index(line.at) + 1 == index)

        assert earned_in(month, confirmed) == naive_earned
        assert deducted_in(month, revoked) == naive_deducted
        assert net_toman(earned_in(month, confirmed), deducted_in(month, revoked)) == naive_earned - naive_deducted
        assert sum(net_by_staff(month, confirmed, revoked).values()) == naive_earned - naive_deducted


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(histories)
def test_a_deduction_never_lands_in_the_month_of_the_revocation_itself(
    history: tuple[list[RewardLine], list[RewardLine]],
) -> None:
    _, revoked = history

    for line in revoked:
        own_month = month_of(line.at)
        target = deduction_month(line.at)

        assert target != own_month
        assert _month_index(target) == _month_index(own_month) + 1
        assert deducted_in(own_month, [line]) == 0
        assert deducted_in(target, [line]) == line.amount_toman
