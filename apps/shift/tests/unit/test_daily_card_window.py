"""The owner's card rules as tests: no Friday card, a two-day Saturday card, and no silent gap.

S01 §6 and ADR-018: the card falls due at 07:30 every day but Friday, and Saturday's card covers
Thursday and Friday. `due_card_days` is what makes a restart, or a server that was switched off at
07:30, leave no missing card. These are the rules the clinic's manager reads every morning, so they
are tested directly instead of only through the builder.
"""

from datetime import date, datetime, timedelta

import pytest

from shift.common.clock import TEHRAN
from shift.domain.daily_card import FRIDAY, SATURDAY, CardWindow, due_card_days, window_for

# A real week: 2026-10-31 is a Saturday, so 2026-10-30 is the Friday without a card.
THURSDAY = date(2026, 10, 29)
FRIDAY_DATE = date(2026, 10, 30)
SATURDAY_DATE = date(2026, 10, 31)
SUNDAY = date(2026, 11, 1)
WEDNESDAY = date(2026, 10, 28)
TUESDAY = date(2026, 10, 27)


def test_friday_has_no_card_at_all() -> None:
    assert window_for(FRIDAY_DATE) is None


def test_saturday_covers_thursday_and_friday() -> None:
    """The Friday gap is clinic policy, so Saturday carries the day the missing card would have."""
    window = window_for(SATURDAY_DATE)

    assert window == CardWindow(SATURDAY_DATE, THURSDAY, FRIDAY_DATE)
    assert window.day_count == 2
    assert window.start == datetime(2026, 10, 29, 0, 0, tzinfo=TEHRAN)
    assert window.end == datetime(2026, 10, 31, 0, 0, tzinfo=TEHRAN)


def test_sunday_covers_saturday() -> None:
    window = window_for(SUNDAY)

    assert (window.covered_from, window.covered_to) == (SATURDAY_DATE, SATURDAY_DATE)
    assert window.day_count == 1
    assert (window.start, window.end) == (
        datetime(2026, 10, 31, 0, 0, tzinfo=TEHRAN),
        datetime(2026, 11, 1, 0, 0, tzinfo=TEHRAN),
    )


def test_wednesday_covers_tuesday() -> None:
    window = window_for(WEDNESDAY)

    assert (window.covered_from, window.covered_to, window.day_count) == (TUESDAY, TUESDAY, 1)


def test_today_is_due_when_no_card_for_it_is_stored() -> None:
    assert due_card_days(WEDNESDAY, newest=TUESDAY) == (WEDNESDAY,)


def test_a_restart_backfills_every_missed_day_after_the_newest_card() -> None:
    """A server that was off from Wednesday to Sunday owes Thursday and Saturday, oldest first."""
    missed = due_card_days(SUNDAY, newest=WEDNESDAY)

    assert missed == (THURSDAY, SATURDAY_DATE, SUNDAY)


def test_the_backfill_never_reaches_further_back_than_the_horizon() -> None:
    """A long outage backfills a bounded stretch, not the whole history it missed."""
    today = date(2026, 11, 10)  # Tuesday

    days = due_card_days(today, newest=today - timedelta(days=30), horizon=5)

    assert days == (date(2026, 11, 5), date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9), today)
    assert min(days) == today - timedelta(days=5)


def test_a_fresh_install_gets_todays_card_only() -> None:
    """No stored card means the history begins today: no fortnight of empty cards on first run."""
    assert due_card_days(WEDNESDAY, newest=None, horizon=14) == (WEDNESDAY,)
    assert due_card_days(FRIDAY_DATE, newest=None, horizon=14) == ()


def test_friday_itself_produces_no_card_even_when_the_backlog_is_clear() -> None:
    assert due_card_days(FRIDAY_DATE, newest=THURSDAY) == ()


def test_nothing_is_due_once_today_has_its_card() -> None:
    assert due_card_days(WEDNESDAY, newest=WEDNESDAY) == ()
    assert due_card_days(WEDNESDAY, newest=WEDNESDAY + timedelta(days=1)) == ()


def test_a_horizon_below_one_day_is_refused() -> None:
    with pytest.raises(ValueError, match="horizon"):
        due_card_days(WEDNESDAY, newest=None, horizon=0)


def test_the_weekday_constants_match_datetime() -> None:
    """Friday and Saturday are `datetime.weekday()` numbers; a wrong pair would move the gap."""
    assert FRIDAY_DATE.weekday() == FRIDAY
    assert SATURDAY_DATE.weekday() == SATURDAY
