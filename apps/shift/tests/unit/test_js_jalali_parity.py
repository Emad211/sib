"""The picker's JavaScript must agree with `shift.common.jalali` on every day of 1380..1450.

The table is generated here by the Python converter and handed to node in a temp file, so a single
test covers both directions (Jalali -> Gregorian and Gregorian -> Jalali), the Persian text the
person sees, and the round trip through typed Persian digits. No database and no real data: the
calendar is arithmetic.
"""

import json
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from shift.common.jalali import gregorian_to_jalali, jalali_to_gregorian

_FIRST_JALALI = (1380, 1, 1)
_LAST_JALALI = (1450, 12, 29)
_PICKER = Path(__file__).resolve().parents[2] / "src" / "shift" / "static" / "jalali-datepicker.js"
_NODE = shutil.which("node")

requires_node = pytest.mark.skipif(_NODE is None, reason="node is not on PATH")

_EVERY_DAY_RUNNER = """
const fs = require("node:fs");
const picker = require(process.argv[2]);
const table = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const rows = table.map(([iso, jalali]) => {
  const shown = picker.jalaliText(iso);
  const typed = picker.parseJalaliInput(shown);
  const back = picker.jalaliFromIso(picker.isoFromJalali(jalali[0], jalali[1], jalali[2]));
  return [picker.jalaliFromIso(iso), typed.ok ? [typed.year, typed.month, typed.day] : null, back, shown];
});
process.stdout.write(JSON.stringify(rows));
"""

_ESFAND_RUNNER = """
const picker = require(process.argv[2]);
const years = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(years.map((year) => picker.isValidJalaliDate(year, 12, 30))));
"""


def _every_day() -> list[tuple[str, list[int]]]:
    """(ISO Gregorian day, Jalali day) for the whole range, straight from the Python converter."""
    first = date(*jalali_to_gregorian(*_FIRST_JALALI))
    last = date(*jalali_to_gregorian(*_LAST_JALALI))
    days: list[tuple[str, list[int]]] = []
    current = first
    while current <= last:
        gregorian = (current.year, current.month, current.day)
        days.append((current.isoformat(), list(gregorian_to_jalali(*gregorian))))
        current += timedelta(days=1)
    return days


def _run_node(tmp_path: Path, runner: str, *arguments: Path | str) -> str:
    script = tmp_path / "runner.js"
    script.write_text(runner, encoding="utf-8")
    completed = subprocess.run(
        [str(_NODE), str(script), str(_PICKER), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@requires_node
def test_every_day_of_the_range_matches_the_python_converter(tmp_path: Path) -> None:
    table = _every_day()
    assert table[0] == ("2001-03-21", [1380, 1, 1])
    assert table[-1] == ("2072-03-19", [1450, 12, 29])
    assert len(table) == 25932  # 71 Jalali years, 17 of them 366 days long, the last day 12/29
    payload = tmp_path / "table.json"
    payload.write_text(json.dumps(table), encoding="utf-8")
    rows = json.loads(_run_node(tmp_path, _EVERY_DAY_RUNNER, payload))

    assert len(rows) == len(table)
    mismatches = []
    for (iso, jalali), (from_iso, typed, back, shown) in zip(table, rows, strict=True):
        if from_iso != jalali or typed != jalali or back != jalali:
            mismatches.append((iso, jalali, from_iso, typed, back))
        if re.search(r"[0-9]", shown):
            mismatches.append((iso, jalali, "latin digits in the visible text", shown))

    assert not mismatches, mismatches[:5]


@requires_node
def test_esfand_30_exists_in_the_same_years_on_both_sides(tmp_path: Path) -> None:
    years = list(range(_FIRST_JALALI[0], _LAST_JALALI[0] + 1))
    answers = json.loads(_run_node(tmp_path, _ESFAND_RUNNER, json.dumps(years)))

    python_side = []
    for year in years:
        try:
            jalali_to_gregorian(year, 12, 30)
        except ValueError:
            python_side.append(False)
        else:
            python_side.append(True)

    assert answers == python_side
    assert python_side.count(True) == 17, "a 33-year cycle has 8 leap years, so 71 years hold 17"
