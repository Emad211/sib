"""No change made during a shift can reach that shift or any earlier one (R-09)."""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from shift.adapters.db.core import Database
from shift.domain.policy import SettingKey, ShiftKey, canonical_setting
from shift.services.settings import SettingsService

BASE_DAY = date(2026, 10, 28)
PROBES = [ShiftKey(BASE_DAY + timedelta(days=day), rank) for day in range(-1, 9) for rank in (1, 2, 3)]
NUMBER_VALUES = {
    SettingKey.TAKEOVER_WINDOW_MINUTES: st.integers(0, 180),
    SettingKey.WITHDRAWAL_CAP_TOMAN: st.integers(0, 1_000_000),
    SettingKey.SESSION_TTL_HOURS: st.integers(1, 24),
    SettingKey.WEEKLY_COUNT_WEEKDAY: st.integers(0, 6),
}
CLOCK_KEYS = frozenset({SettingKey.CARD_TIME, SettingKey.WEEKLY_COUNT_TIME})
CLOCK_VALUES = st.times().map(lambda moment: f"{moment.hour:02d}:{moment.minute:02d}")

shift_keys = st.builds(
    lambda day, rank: ShiftKey(BASE_DAY + timedelta(days=day), rank),
    st.integers(min_value=0, max_value=6),
    st.sampled_from((1, 2, 3)),
)


@st.composite
def planned_changes(draw: st.DrawFn) -> tuple[ShiftKey, SettingKey, str]:
    """A change a manager makes while `shift` runs, with a value that setting accepts."""
    running = draw(shift_keys)
    key = draw(st.sampled_from(list(SettingKey)))
    raw = draw(CLOCK_VALUES) if key in CLOCK_KEYS else str(draw(NUMBER_VALUES[key]))
    return running, key, raw


@st.composite
def chronological_plan(draw: st.DrawFn) -> list[tuple[ShiftKey, SettingKey, str]]:
    """Changes in the order the shifts actually ran: a running shift never moves backwards."""
    plan = draw(st.lists(planned_changes(), max_size=12))
    return sorted(plan, key=lambda change: change[0])


@contextmanager
def migrated_database() -> Iterator[Database]:
    """A private migrated database per example: Hypothesis reuses pytest fixtures."""
    with tempfile.TemporaryDirectory() as folder:
        database = Database(Path(folder) / "settings.db")
        database.migrate()
        try:
            yield database
        finally:
            database.close()


@given(plan=chronological_plan())
@settings(max_examples=40, deadline=None)
def test_a_shift_only_sees_changes_recorded_before_it(plan: list[tuple[ShiftKey, SettingKey, str]]) -> None:
    with migrated_database() as database:
        service = SettingsService(database)
        seeded = {key: value.value for key, value in service.snapshot().items()}
        applied: list[tuple[ShiftKey, SettingKey, str]] = []
        for running, key, raw in plan:
            changed = service.change(key, raw, actor_id="staff-manager", running_shift=running)
            if changed.created:
                applied.append((changed.value.effective_from_shift, key, canonical_setting(key, raw)))

        for shift in PROBES:
            for key in SettingKey:
                expected = seeded[key]
                for effective, changed_key, value in applied:
                    if changed_key is key and effective <= shift:
                        expected = value
                assert service.value(key, shift).value == expected


@given(plan=chronological_plan())
@settings(max_examples=40, deadline=None)
def test_a_change_leaves_the_shift_it_was_made_in_untouched(plan: list[tuple[ShiftKey, SettingKey, str]]) -> None:
    with migrated_database() as database:
        service = SettingsService(database)
        for running, key, raw in plan:
            before = service.snapshot(running)

            changed = service.change(key, raw, actor_id="staff-manager", running_shift=running)

            assert service.snapshot(running) == before
            if changed.created:
                assert changed.value.effective_from_shift > running
