"""Settings are versioned: a change starts at the next shift, never inside the running one (R-09)."""

from datetime import date, time

import pytest
from clocks import FixedClock, inside_shift

from shift.adapters.db.core import Database
from shift.domain.policy import PolicyError, SettingKey, ShiftKey
from shift.services.settings import SettingError, SettingsService

DAY = date(2026, 10, 28)
MORNING = ShiftKey(DAY, 1)
EVENING = ShiftKey(DAY, 2)
NIGHT = ShiftKey(DAY, 3)
NEXT_MORNING = ShiftKey(date(2026, 10, 29), 1)


@pytest.fixture
def service(database: Database) -> SettingsService:
    return SettingsService(database)


def test_the_migration_seeds_the_four_policy_defaults(service: SettingsService) -> None:
    snapshot = service.snapshot()

    assert {key: value.value for key, value in snapshot.items()} == {
        SettingKey.CARD_TIME: "07:30",
        SettingKey.TAKEOVER_WINDOW_MINUTES: "30",
        SettingKey.WITHDRAWAL_CAP_TOMAN: "300000",
        SettingKey.SESSION_TTL_HOURS: "12",
    }
    assert {value.version for value in snapshot.values()} == {1}
    assert {value.effective_from_shift for value in snapshot.values()} == {ShiftKey.baseline()}


def test_typed_reads_answer_from_the_database(service: SettingsService) -> None:
    service.change(SettingKey.CARD_TIME, "8:15", actor_id="staff-manager", running_shift=EVENING)
    service.change(SettingKey.WITHDRAWAL_CAP_TOMAN, "۵۰۰۰۰۰", actor_id="staff-manager", running_shift=EVENING)

    assert service.clock_time(SettingKey.CARD_TIME, NIGHT) == time(8, 15)
    assert service.number(SettingKey.WITHDRAWAL_CAP_TOMAN, NIGHT) == 500_000
    assert service.number(SettingKey.SESSION_TTL_HOURS, NIGHT) == 12


def test_a_setting_is_only_read_as_the_kind_it_declares(service: SettingsService) -> None:
    with pytest.raises(PolicyError):
        service.number(SettingKey.CARD_TIME)
    with pytest.raises(PolicyError):
        service.clock_time(SettingKey.SESSION_TTL_HOURS)


def test_a_change_applies_from_the_next_shift_only(service: SettingsService) -> None:
    changed = service.change(SettingKey.WITHDRAWAL_CAP_TOMAN, "450000", actor_id="staff-manager", running_shift=EVENING)

    assert changed.created is True
    assert changed.value.version == 2
    assert changed.value.effective_from_shift == NIGHT
    assert changed.value.changed_by == "staff-manager"
    assert service.value(SettingKey.WITHDRAWAL_CAP_TOMAN, EVENING).value == "300000"
    assert service.value(SettingKey.WITHDRAWAL_CAP_TOMAN, NIGHT).value == "450000"
    assert service.value(SettingKey.WITHDRAWAL_CAP_TOMAN, NEXT_MORNING).value == "450000"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (SettingKey.CARD_TIME, "06:45"),
        (SettingKey.TAKEOVER_WINDOW_MINUTES, "45"),
        (SettingKey.WITHDRAWAL_CAP_TOMAN, "500000"),
        (SettingKey.SESSION_TTL_HOURS, "8"),
    ],
)
def test_mid_shift_changes_leave_the_running_shift_untouched(
    service: SettingsService, key: SettingKey, value: str
) -> None:
    before = service.snapshot(MORNING)

    service.change(key, value, actor_id="staff-manager", running_shift=MORNING)

    assert service.snapshot(MORNING) == before
    assert service.value(key, MORNING.following()).value == value


def test_a_change_outside_a_shift_applies_from_the_next_clock_shift(database: Database) -> None:
    service = SettingsService(database, clock=FixedClock(inside_shift(DAY, 1)))

    changed = service.change(SettingKey.SESSION_TTL_HOURS, "6", actor_id="staff-manager")

    assert changed.created is True
    assert changed.value.effective_from_shift == EVENING
    assert changed.value.effective_from_shift > ShiftKey.baseline()
    assert service.value(SettingKey.SESSION_TTL_HOURS, MORNING).value == "12"
    assert service.value(SettingKey.SESSION_TTL_HOURS, EVENING).value == "6"


def test_reading_without_a_shift_uses_the_clock_not_the_baseline(database: Database) -> None:
    clock = FixedClock(inside_shift(DAY, 1))
    service = SettingsService(database, clock=clock)
    service.change(SettingKey.SESSION_TTL_HOURS, "6", actor_id="staff-manager", running_shift=MORNING)

    assert service.snapshot()[SettingKey.SESSION_TTL_HOURS].value == "12"

    clock.moment = inside_shift(DAY, 2)

    assert service.snapshot()[SettingKey.SESSION_TTL_HOURS].value == "6"


def test_a_change_cannot_start_before_the_newest_version(service: SettingsService) -> None:
    service.change(SettingKey.SESSION_TTL_HOURS, "6", actor_id="staff-manager", running_shift=NIGHT)

    with pytest.raises(PolicyError, match="newest version"):
        service.change(SettingKey.SESSION_TTL_HOURS, "8", actor_id="staff-manager", running_shift=MORNING)

    assert [value.value for value in service.history(SettingKey.SESSION_TTL_HOURS)] == ["6", "12"]


def test_a_repeated_value_is_not_suppressed_by_an_out_of_order_legacy_version(database: Database) -> None:
    with database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO setting_versions "
            "(setting_key, value, version, effective_from_shift, changed_at, changed_by) "
            "VALUES ('session_ttl_hours', '6', 2, '2026-10-28#3', '2026-10-28T00:00:00.000Z', 'legacy'), "
            "('session_ttl_hours', '8', 3, '2026-10-28#2', '2026-10-28T00:00:00.000Z', 'legacy')"
        )
    service = SettingsService(database)

    changed = service.change(SettingKey.SESSION_TTL_HOURS, "8", actor_id="staff-manager", running_shift=NIGHT)

    assert changed.created is True
    assert changed.value.effective_from_shift == NEXT_MORNING
    assert service.value(SettingKey.SESSION_TTL_HOURS, NEXT_MORNING).value == "8"


def test_two_changes_in_one_shift_keep_the_last(service: SettingsService) -> None:
    service.change(SettingKey.TAKEOVER_WINDOW_MINUTES, "40", actor_id="staff-manager", running_shift=EVENING)
    last = service.change(SettingKey.TAKEOVER_WINDOW_MINUTES, "50", actor_id="staff-manager", running_shift=EVENING)

    assert last.value.version == 3
    assert last.value.effective_from_shift == NIGHT
    assert service.value(SettingKey.TAKEOVER_WINDOW_MINUTES, NIGHT).value == "50"


def test_repeating_the_stored_value_adds_no_version(service: SettingsService) -> None:
    first = service.change(SettingKey.TAKEOVER_WINDOW_MINUTES, "45", actor_id="staff-manager", running_shift=EVENING)
    again = service.change(SettingKey.TAKEOVER_WINDOW_MINUTES, "45", actor_id="staff-manager", running_shift=NIGHT)

    assert again.created is False
    assert again.value.version == first.value.version
    assert len(service.history(SettingKey.TAKEOVER_WINDOW_MINUTES)) == 2


def test_history_lists_every_version_newest_first(service: SettingsService) -> None:
    service.change(SettingKey.CARD_TIME, "06:00", actor_id="staff-manager", running_shift=MORNING)
    service.change(SettingKey.CARD_TIME, "06:30", actor_id="staff-manager", running_shift=EVENING)

    history = service.history(SettingKey.CARD_TIME)

    assert [value.value for value in history] == ["06:30", "06:00", "07:30"]
    assert [value.version for value in history] == [3, 2, 1]
    assert [value.effective_from_shift for value in history] == [NIGHT, EVENING, ShiftKey.baseline()]


def test_effective_from_never_moves_backwards(service: SettingsService) -> None:
    running_shifts = [MORNING, MORNING, EVENING, NIGHT, NEXT_MORNING]

    changes = [
        service.change(SettingKey.SESSION_TTL_HOURS, str(hours), actor_id="staff-manager", running_shift=running)
        for hours, running in enumerate(running_shifts, start=4)
    ]

    effective = [change.value.effective_from_shift for change in changes]
    assert effective == sorted(effective)


@pytest.mark.parametrize("value", ["181", "-1", "thirty"])
def test_an_invalid_value_is_rejected_and_nothing_is_written(service: SettingsService, value: str) -> None:
    with pytest.raises(PolicyError):
        service.change(SettingKey.TAKEOVER_WINDOW_MINUTES, value, actor_id="staff-manager", running_shift=EVENING)

    assert len(service.history(SettingKey.TAKEOVER_WINDOW_MINUTES)) == 1


@pytest.mark.parametrize("key", ["owner", "CARD_TIME"])
def test_an_unknown_setting_key_is_rejected(service: SettingsService, key: str) -> None:
    with pytest.raises(PolicyError):
        service.value(key)
    with pytest.raises(PolicyError):
        service.change(key, "08:00", actor_id="staff-manager")


@pytest.mark.parametrize("actor_id", ["", "   "])
def test_a_change_must_name_its_author(service: SettingsService, actor_id: str) -> None:
    with pytest.raises(PolicyError):
        service.change(SettingKey.CARD_TIME, "08:00", actor_id=actor_id)


def test_a_missing_setting_row_is_reported(database: Database, service: SettingsService) -> None:
    with database.write() as connection:
        connection.exec_driver_sql("DELETE FROM setting_versions WHERE setting_key = ?", ("card_time",))

    with pytest.raises(SettingError, match="card_time"):
        service.value(SettingKey.CARD_TIME)
    with pytest.raises(SettingError, match="card_time"):
        service.snapshot()
