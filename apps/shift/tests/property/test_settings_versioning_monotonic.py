"""No sequence of updates can change what an already-started shift has already seen (R-09)."""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

from clocks import FixedClock, inside_shift
from hypothesis import given, settings
from hypothesis import strategies as st

from shift.adapters.db.core import Database
from shift.domain.policy import SettingKey, ShiftKey
from shift.services.settings import SettingsService

BASE_DAY = date(2026, 10, 28)
SHIFTS = [ShiftKey(BASE_DAY + timedelta(days=day), rank) for day in range(-1, 5) for rank in (1, 2, 3)]
PROBES = [ShiftKey(BASE_DAY + timedelta(days=day), rank) for day in range(-1, 6) for rank in (1, 2, 3)]
KEY = SettingKey.SESSION_TTL_HOURS


@st.composite
def update_plans(draw: st.DrawFn) -> list[tuple[ShiftKey, int, bool]]:
    """Chronological updates; `None` sometimes stands in for the missing running-shift source."""
    size = draw(st.integers(min_value=1, max_value=8))
    positions = sorted(draw(st.lists(st.integers(0, len(SHIFTS) - 1), min_size=size, max_size=size)))
    values = draw(st.lists(st.integers(1, 24), min_size=size, max_size=size))
    source_missing = draw(st.lists(st.booleans(), min_size=size, max_size=size))
    return [
        (SHIFTS[position], value, missing)
        for position, value, missing in zip(positions, values, source_missing, strict=True)
    ]


@contextmanager
def migrated_database() -> Iterator[Database]:
    """A private migrated database per Hypothesis example."""
    with tempfile.TemporaryDirectory() as folder:
        database = Database(Path(folder) / "settings.db")
        database.migrate()
        try:
            yield database
        finally:
            database.close()


@given(plan=update_plans())
@settings(max_examples=40, deadline=None)
def test_an_already_started_shift_never_sees_a_later_update(plan: list[tuple[ShiftKey, int, bool]]) -> None:
    with migrated_database() as database:
        clock = FixedClock(inside_shift(BASE_DAY, 1))
        service = SettingsService(database, clock=clock)
        effective = [ShiftKey.baseline()]
        for running, value, source_missing in plan:
            clock.moment = inside_shift(running.work_date, running.rank)
            before = {probe: service.value(KEY, probe).value for probe in PROBES if probe <= running}

            changed = service.change(
                KEY, str(value), actor_id="staff-manager", running_shift=None if source_missing else running
            )

            assert {probe: service.value(KEY, probe).value for probe in before} == before
            if changed.created:
                assert changed.value.effective_from_shift > running
                effective.append(changed.value.effective_from_shift)

        assert effective == sorted(effective)
