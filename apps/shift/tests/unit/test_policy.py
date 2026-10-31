"""Settings vocabulary, shift keys and roles are pure, bounded and shift-ordered (R-08, R-09)."""

from datetime import date, datetime, time

import pytest

from shift.domain.policy import (
    SETTING_SPECS,
    PolicyError,
    Role,
    SettingKey,
    SettingKind,
    SettingSpec,
    ShiftKey,
    canonical_setting,
    parse_setting,
    spec_for,
)
from shift.domain.shift_clock import covering, effective_from, require_monotonic


def test_setting_keys_are_the_stored_names() -> None:
    assert {key.value for key in SettingKey} == {
        "card_time",
        "takeover_window_minutes",
        "withdrawal_cap_toman",
        "session_ttl_hours",
        "weekly_count_time",
        "weekly_count_weekday",
        "sugar_threshold",
        "systolic_threshold",
        "diastolic_threshold",
        "oxygen_pressure_threshold",
        "cash_float_toman",
    }


def test_every_setting_has_exactly_one_spec() -> None:
    assert set(SETTING_SPECS) == set(SettingKey)


def test_specs_pin_the_kind_and_bounds_of_every_setting() -> None:
    assert SETTING_SPECS[SettingKey.CARD_TIME] == SettingSpec(SettingKind.CLOCK_TIME)
    assert SETTING_SPECS[SettingKey.TAKEOVER_WINDOW_MINUTES] == SettingSpec(SettingKind.INTEGER, 0, 180)
    assert SETTING_SPECS[SettingKey.WITHDRAWAL_CAP_TOMAN] == SettingSpec(SettingKind.INTEGER, 0, 100_000_000)
    assert SETTING_SPECS[SettingKey.SESSION_TTL_HOURS] == SettingSpec(SettingKind.INTEGER, 1, 24)
    assert SETTING_SPECS[SettingKey.WEEKLY_COUNT_TIME] == SettingSpec(SettingKind.CLOCK_TIME)
    assert SETTING_SPECS[SettingKey.WEEKLY_COUNT_WEEKDAY] == SettingSpec(SettingKind.WEEKDAY, 0, 6)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("07:30", time(7, 30)),
        ("7:30", time(7, 30)),
        ("۰۷:۳۰", time(7, 30)),
        ("00:00", time(0, 0)),
        ("23:59", time(23, 59)),
    ],
)
def test_card_time_parses_and_folds_persian_digits(raw: str, expected: time) -> None:
    assert parse_setting(SettingKey.CARD_TIME, raw) == expected


@pytest.mark.parametrize("raw", ["0730", "7:3", "24:00", "07:60", "07:30:00", "", "  ", "abc"])
def test_card_time_rejects_anything_that_is_not_hh_mm(raw: str) -> None:
    with pytest.raises(PolicyError):
        parse_setting(SettingKey.CARD_TIME, raw)


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        (SettingKey.TAKEOVER_WINDOW_MINUTES, "0", 0),
        (SettingKey.TAKEOVER_WINDOW_MINUTES, "180", 180),
        (SettingKey.WITHDRAWAL_CAP_TOMAN, "300000", 300_000),
        (SettingKey.WITHDRAWAL_CAP_TOMAN, "۳۰۰۰۰۰", 300_000),
        (SettingKey.SESSION_TTL_HOURS, " 24 ", 24),
        (SettingKey.SESSION_TTL_HOURS, 12, 12),
    ],
)
def test_whole_number_settings_parse(key: SettingKey, raw: object, expected: int) -> None:
    assert parse_setting(key, raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        (SettingKey.TAKEOVER_WINDOW_MINUTES, "-1"),
        (SettingKey.TAKEOVER_WINDOW_MINUTES, "181"),
        (SettingKey.WITHDRAWAL_CAP_TOMAN, "100000001"),
        (SettingKey.WITHDRAWAL_CAP_TOMAN, "3000.5"),
        (SettingKey.SESSION_TTL_HOURS, "0"),
        (SettingKey.SESSION_TTL_HOURS, "25"),
        (SettingKey.SESSION_TTL_HOURS, "twelve"),
        (SettingKey.SESSION_TTL_HOURS, ""),
    ],
)
def test_whole_number_settings_reject_values_outside_policy(key: SettingKey, raw: str) -> None:
    with pytest.raises(PolicyError):
        parse_setting(key, raw)


def test_canonical_text_is_stable_and_reparsable() -> None:
    assert canonical_setting(SettingKey.CARD_TIME, "7:05") == "07:05"
    assert canonical_setting(SettingKey.WITHDRAWAL_CAP_TOMAN, "۳۰۰۰۰۰") == "300000"
    assert canonical_setting(SettingKey.SESSION_TTL_HOURS, 12) == "12"
    assert parse_setting(SettingKey.CARD_TIME, canonical_setting(SettingKey.CARD_TIME, "7:05")) == time(7, 5)


@pytest.mark.parametrize("key", ["owner", "", "card-time"])
def test_an_unknown_setting_key_is_a_policy_error(key: str) -> None:
    with pytest.raises(PolicyError):
        spec_for(key)
    with pytest.raises(PolicyError):
        parse_setting(key, "1")


def test_roles_are_the_seven_documented_roles() -> None:
    assert [role.value for role in Role] == [
        "reception",
        "nurse",
        "storekeeper",
        "purchaser",
        "accountant",
        "shift_doctor",
        "manager",
    ]


@pytest.mark.parametrize(
    ("work_date", "rank"),
    [
        (date(2026, 10, 28), 1),
        (date(2026, 10, 28), 3),
        (date(2026, 1, 1), 2),
        (date(9999, 12, 31), 3),
    ],
)
def test_shift_key_text_round_trips(work_date: date, rank: int) -> None:
    key = ShiftKey(work_date, rank)

    assert key.text == f"{work_date.isoformat()}#{rank}"
    assert ShiftKey.parse(key.text) == key


@pytest.mark.parametrize(
    "text", ["2026-10-28", "2026-10-28#", "2026-10-28#0", "2026-10-28#4", "2026-10-28#x", "28-10-2026#1", ""]
)
def test_shift_key_parse_rejects_anything_else(text: str) -> None:
    with pytest.raises(PolicyError):
        ShiftKey.parse(text)


@pytest.mark.parametrize("rank", [0, 4, -1, 99])
def test_shift_key_rejects_unknown_ranks(rank: int) -> None:
    with pytest.raises(PolicyError):
        ShiftKey(date(2026, 10, 28), rank)


def test_shift_key_rejects_a_datetime_work_date() -> None:
    with pytest.raises(PolicyError):
        ShiftKey(datetime(2026, 10, 28, 7, 30))  # type: ignore[arg-type]


def test_following_walks_the_day_then_the_next_morning() -> None:
    day = date(2026, 10, 28)

    assert ShiftKey(day, 1).following() == ShiftKey(day, 2)
    assert ShiftKey(day, 2).following() == ShiftKey(day, 3)
    assert ShiftKey(day, 3).following() == ShiftKey(date(2026, 10, 29), 1)


def test_shift_keys_and_their_text_sort_in_the_same_order() -> None:
    keys = [
        ShiftKey(date(2026, 10, 27), 3),
        ShiftKey(date(2026, 10, 28), 1),
        ShiftKey(date(2026, 10, 28), 2),
        ShiftKey(date(2026, 10, 28), 3),
        ShiftKey(date(2026, 10, 29), 1),
    ]

    assert sorted(keys) == keys
    assert [key.text for key in keys] == sorted(key.text for key in keys)


def test_baseline_precedes_every_real_shift() -> None:
    assert ShiftKey.baseline() == ShiftKey(date.min, 1)
    assert ShiftKey.baseline().text == "0001-01-01#1"
    assert ShiftKey.baseline() < ShiftKey(date(2026, 1, 1), 1)


def test_a_change_applies_from_the_shift_after_the_running_one() -> None:
    running = ShiftKey(date(2026, 10, 28), 2)

    assert effective_from(running, now=datetime(2026, 10, 28, 12, 0)) == ShiftKey(date(2026, 10, 28), 3)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 28, 5, 0), ShiftKey(date(2026, 10, 27), 3)),
        (datetime(2026, 10, 28, 7, 29), ShiftKey(date(2026, 10, 27), 3)),
        (datetime(2026, 10, 28, 7, 30), ShiftKey(date(2026, 10, 28), 1)),
        (datetime(2026, 10, 28, 12, 0), ShiftKey(date(2026, 10, 28), 1)),
        (datetime(2026, 10, 28, 13, 29), ShiftKey(date(2026, 10, 28), 1)),
        (datetime(2026, 10, 28, 13, 30), ShiftKey(date(2026, 10, 28), 2)),
        (datetime(2026, 10, 28, 19, 30), ShiftKey(date(2026, 10, 28), 3)),
        (datetime(2026, 10, 28, 23, 59), ShiftKey(date(2026, 10, 28), 3)),
    ],
)
def test_covering_finds_the_shift_that_started_most_recently(moment: datetime, expected: ShiftKey) -> None:
    assert covering(moment) == expected


def test_without_a_running_shift_a_change_starts_at_the_next_clock_shift() -> None:
    assert effective_from(None, now=datetime(2026, 10, 28, 5, 0)) == ShiftKey(date(2026, 10, 28), 1)
    assert effective_from(None, now=datetime(2026, 10, 28, 12, 0)) == ShiftKey(date(2026, 10, 28), 2)
    assert effective_from(None, now=datetime(2026, 10, 28, 23, 0)) == ShiftKey(date(2026, 10, 29), 1)


@pytest.mark.parametrize(
    "moment",
    [datetime(2026, 10, 28, 5, 0), datetime(2026, 10, 28, 12, 0), datetime(2026, 10, 28, 23, 0)],
)
def test_a_runtime_change_never_targets_the_baseline(moment: datetime) -> None:
    target = effective_from(None, now=moment)

    assert target > ShiftKey.baseline()
    assert target > covering(moment)


def test_the_baseline_is_not_a_runtime_running_shift() -> None:
    with pytest.raises(PolicyError, match="baseline"):
        effective_from(ShiftKey.baseline(), now=datetime(2026, 10, 28, 12, 0))


def test_monotonic_target_accepts_equal_or_later_and_rejects_earlier() -> None:
    newest = ShiftKey(date(2026, 10, 28), 3)

    assert require_monotonic(newest, newest) == newest
    assert require_monotonic(ShiftKey(date(2026, 10, 29), 1), newest) == ShiftKey(date(2026, 10, 29), 1)
    with pytest.raises(PolicyError, match="newest version"):
        require_monotonic(ShiftKey(date(2026, 10, 28), 2), newest)
    assert require_monotonic(ShiftKey(date(2026, 10, 28), 2), None) == ShiftKey(date(2026, 10, 28), 2)
