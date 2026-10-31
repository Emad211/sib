/**
 * Jalali date picker: calendar arithmetic, Persian input and the Tehran-time trap.
 *
 * Zero dependencies (node:test only). The exhaustive parity check against
 * src/shift/common/jalali.py for every day from 1380/01/01 to 1450/12/29 lives in
 * tests/unit/test_js_jalali_parity.py, which drives this same module from Python.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const pickerPath = path.resolve(__dirname, "../../src/shift/static/jalali-datepicker.js");
const {
  JALALI_MONTHS,
  WEEKDAY_NAMES,
  FRIDAY,
  SHAPE_MESSAGE,
  foldDigits,
  toPersianDigits,
  gregorianToJalali,
  jalaliToGregorian,
  isValidJalaliDate,
  isLeapJalaliYear,
  daysInJalaliMonth,
  weekdayIndex,
  jalaliWeekdayIndex,
  isoFromJalali,
  jalaliFromIso,
  formatJalali,
  jalaliText,
  parseJalaliInput,
  parseBound,
  withinBounds,
  addDaysIso,
  tehranDateFromInstant,
  tehranTodayJalali,
  monthGrid,
  monthTitle,
  invalidCalendarMessage,
  outOfRangeMessage,
} = require(pickerPath);

describe("Jalali conversion matches src/shift/common/jalali.py", () => {
  const pairs = [
    [1380, 1, 1, 2001, 3, 21],
    [1399, 1, 1, 2020, 3, 20],
    [1399, 12, 29, 2021, 3, 19],
    [1399, 12, 30, 2021, 3, 20],
    [1400, 1, 1, 2021, 3, 21],
    [1403, 1, 1, 2024, 3, 20],
    [1403, 12, 29, 2025, 3, 19],
    [1403, 12, 30, 2025, 3, 20],
    [1404, 1, 1, 2025, 3, 21],
    [1404, 12, 29, 2026, 3, 20],
    [1405, 1, 1, 2026, 3, 21],
    [1405, 8, 4, 2026, 10, 26],
    [1405, 8, 9, 2026, 10, 31],
    [1408, 1, 1, 2029, 3, 20],
    [1408, 12, 30, 2030, 3, 20],
    [1450, 12, 29, 2072, 3, 19],
  ];

  for (const [jy, jm, jd, gy, gm, gd] of pairs) {
    it(`${jy}/${jm}/${jd} is ${gy}-${gm}-${gd}`, () => {
      assert.deepEqual(jalaliToGregorian(jy, jm, jd), [gy, gm, gd]);
      assert.deepEqual(gregorianToJalali(gy, gm, gd), [jy, jm, jd]);
      assert.equal(isoFromJalali(jy, jm, jd), `${gy}-${String(gm).padStart(2, "0")}-${String(gd).padStart(2, "0")}`);
      assert.deepEqual(jalaliFromIso(isoFromJalali(jy, jm, jd)), [jy, jm, jd]);
    });
  }

  it("round trips every day of a decade in both directions", () => {
    for (let day = Date.UTC(2020, 0, 1); day <= Date.UTC(2030, 11, 31); day += 86400000) {
      const [gy, gm, gd] = tehranDateFromInstant(day + 12 * 3600000);
      const jalali = gregorianToJalali(gy, gm, gd);
      assert.deepEqual(jalaliToGregorian(...jalali), [gy, gm, gd]);
    }
  });
});

describe("Esfand 29/30 and Farvardin 1 on both sides of a leap year", () => {
  for (const year of [1399, 1403, 1408]) {
    it(`${year} is leap: Esfand has 30 days`, () => {
      assert.equal(isLeapJalaliYear(year), true);
      assert.equal(daysInJalaliMonth(year, 12), 30);
      assert.deepEqual(jalaliToGregorian(year, 12, 30), [year + 622, 3, 20]);
    });
  }

  for (const year of [1400, 1402, 1404, 1405]) {
    it(`${year} is not leap: Esfand stops at 29`, () => {
      assert.equal(isLeapJalaliYear(year), false);
      assert.equal(daysInJalaliMonth(year, 12), 29);
      assert.throws(() => jalaliToGregorian(year, 12, 30), RangeError);
      assert.equal(isValidJalaliDate(year, 12, 30), false);
      assert.equal(isValidJalaliDate(year, 12, 29), true);
    });
  }

  it("rolls 1403/12/29 to 1403/12/30 and then to 1404/01/01", () => {
    assert.equal(addDaysIso(isoFromJalali(1403, 12, 29), 1), isoFromJalali(1403, 12, 30));
    assert.deepEqual(jalaliFromIso(addDaysIso(isoFromJalali(1403, 12, 30), 1)), [1404, 1, 1]);
  });

  it("rolls 1404/12/29 straight to 1405/01/01", () => {
    assert.deepEqual(jalaliFromIso(addDaysIso(isoFromJalali(1404, 12, 29), 1)), [1405, 1, 1]);
    assert.deepEqual(gregorianToJalali(2026, 2, 28), [1404, 12, 9]);
  });
});

describe("Gregorian year ends inside a Jalali month", () => {
  const pairs = [
    ["2024-12-31", 1403, 10, 11],
    ["2025-01-01", 1403, 10, 12],
    ["2026-12-31", 1405, 10, 10],
    ["2024-02-29", 1402, 12, 10],
  ];

  for (const [iso, jy, jm, jd] of pairs) {
    it(`${iso} is ${jy}/${jm}/${jd}`, () => {
      assert.deepEqual(jalaliFromIso(iso), [jy, jm, jd]);
      assert.equal(isoFromJalali(jy, jm, jd), iso);
    });
  }

  it("keeps the Jalali month and the date across a Gregorian new year", () => {
    for (let offset = -3; offset <= 3; offset += 1) {
      const iso = addDaysIso("2025-01-01", offset);
      const [jy, jm, jd] = jalaliFromIso(iso);
      assert.equal(jm, 10);
      assert.equal(jd, 12 + offset);
      assert.equal(isoFromJalali(jy, jm, jd), iso);
    }
  });
});

describe("Persian and Latin digit input", () => {
  for (const text of ["۱۴۰۵/۰۸/۰۹", "۱۴۰۵/۸/۹", "1405/08/09", " ۱۴۰۵/۰۸/۰۹ ", "١٤٠٥/٠٨/٠٩", "۱۴۰۵-۰۸-۰۹", "۱۴۰۵.۰۸.۰۹"]) {
    it(`reads ${JSON.stringify(text)} as 2026-10-31`, () => {
      const result = parseJalaliInput(text);
      assert.equal(result.ok, true);
      assert.equal(result.iso, "2026-10-31");
      assert.deepEqual([result.year, result.month, result.day], [1405, 8, 9]);
      assert.equal(result.error, "");
    });
  }

  it("refuses an impossible date with a Persian message", () => {
    const result = parseJalaliInput("۱۴۰۴/۱۲/۳۰");
    assert.equal(result.ok, false);
    assert.equal(result.empty, false);
    assert.equal(result.iso, "");
    assert.equal(result.error, invalidCalendarMessage("۱۴۰۴/۱۲/۳۰"));
    assert.match(result.error, /۱۴۰۴\/۱۲\/۳۰/);
  });

  it("refuses a month outside 1..12 and a day outside the month", () => {
    for (const text of ["۱۴۰۵/۱۳/۰۱", "۱۴۰۵/۰۷/۳۱", "۱۴۰۵/۱۲/۳۰", "۱۴۰۵/۰۸/۰۰", "۱۴۰۵/۸/۹۵"]) {
      const result = parseJalaliInput(text);
      assert.equal(result.ok, false, text);
      assert.equal(result.empty, false, text);
      assert.equal(result.error, invalidCalendarMessage(text), text);
    }
  });

  it("refuses malformed text with the shape message", () => {
    for (const text of ["۱۴۰۵/۰۸", "۱۴۰۵/۰۸/۰۹/۰۱", "abc", "14050/08/09", "۱۴۰۵//۰۹"]) {
      const result = parseJalaliInput(text);
      assert.equal(result.ok, false, text);
      assert.equal(result.empty, false, text);
      assert.equal(result.error, SHAPE_MESSAGE, text);
    }
  });

  it("treats empty text as empty, not as an error", () => {
    for (const text of ["", "   ", "\u200c"]) {
      const result = parseJalaliInput(text);
      assert.equal(result.ok, false);
      assert.equal(result.empty, true);
      assert.equal(result.error, "");
      assert.equal(result.iso, "");
    }
  });

  it("only ever returns an ISO day that exists in the calendar", () => {
    const leapDay = parseJalaliInput("۱۴۰۳/۱۲/۳۰");
    assert.equal(leapDay.iso, "2025-03-20");
    assert.deepEqual(jalaliFromIso(leapDay.iso), [1403, 12, 30]);
    assert.equal(parseJalaliInput("۱۴۰۴/۱۲/۳۰").iso, "");
  });
});

describe("min and max", () => {
  it("reads a Jalali or an ISO bound", () => {
    assert.equal(parseBound("۱۴۰۰/۰۱/۰۱"), "2021-03-21");
    assert.equal(parseBound("2026-03-21"), "2026-03-21");
    assert.equal(parseBound(""), null);
    assert.equal(parseBound("nonsense"), null);
    assert.equal(parseBound("۱۴۰۴/۱۲/۳۰"), null);
  });

  it("refuses a date outside the bounds in Persian", () => {
    const bounds = { minIso: "2021-03-21", maxIso: "2025-03-19" };
    assert.equal(withinBounds("2025-03-19", bounds), true);
    assert.equal(withinBounds("2025-03-20", bounds), false);
    assert.equal(withinBounds("2021-03-20", bounds), false);

    const result = parseJalaliInput("۱۴۰۳/۱۲/۳۰", bounds);
    assert.equal(result.ok, false);
    assert.equal(result.iso, "");
    assert.equal(result.error, outOfRangeMessage(bounds));
    assert.match(result.error, /۱۴۰۳\/۱۲\/۲۹/);
  });

  it("names only the bound that is set", () => {
    assert.match(outOfRangeMessage({ minIso: "2021-03-21", maxIso: "" }), /پیش از ۱۴۰۰\/۰۱\/۰۱/);
    assert.match(outOfRangeMessage({ minIso: "2021-03-21", maxIso: "" }), /تواند/);
    assert.match(outOfRangeMessage({ minIso: "", maxIso: "2025-03-19" }), /بعد از ۱۴۰۳\/۱۲\/۲۹/);
    assert.match(outOfRangeMessage({ minIso: "2021-03-21", maxIso: "2025-03-19" }), /بین ۱۴۰۰\/۰۱\/۰۱ و ۱۴۰۳\/۱۲\/۲۹/);
  });
});

describe("Tehran decides today, not the phone's time zone (UTC+03:30 fixed)", () => {
  it("keeps 1405/08/09 until 23:59:59 Tehran", () => {
    assert.deepEqual(tehranDateFromInstant(Date.UTC(2026, 9, 31, 20, 29, 59)), [2026, 10, 31]);
    assert.deepEqual(tehranTodayJalali(Date.UTC(2026, 9, 31, 20, 29, 59)), [1405, 8, 9]);
  });

  it("turns to 1405/08/10 at the Tehran midnight, three and a half hours before UTC", () => {
    assert.deepEqual(tehranDateFromInstant(Date.UTC(2026, 9, 31, 20, 30, 0)), [2026, 11, 1]);
    assert.deepEqual(tehranTodayJalali(Date.UTC(2026, 9, 31, 20, 30, 0)), [1405, 8, 10]);
  });

  it("gives the Tehran date for a phone whose own date is still yesterday", () => {
    // 21:00 in Los Angeles on 2026-10-31 is 04:00 on 2026-11-01 in Tehran.
    assert.deepEqual(tehranTodayJalali(Date.UTC(2026, 10, 1, 4, 0, 0)), [1405, 8, 10]);
  });

  it("crosses the Jalali new year at the Tehran midnight", () => {
    assert.deepEqual(tehranTodayJalali(Date.UTC(2026, 2, 20, 20, 29, 59)), [1404, 12, 29]);
    assert.deepEqual(tehranTodayJalali(Date.UTC(2026, 2, 20, 20, 30, 0)), [1405, 1, 1]);
  });
});

describe("Month grid: Saturday first, Fridays marked", () => {
  it("starts Aban 1405 on a Friday and shows its 30 days", () => {
    const grid = monthGrid(1405, 8);
    assert.equal(grid.daysInMonth, 30);
    assert.equal(grid.firstWeekday, FRIDAY);
    assert.equal(FRIDAY, 6);

    assert.equal(grid.weeks[0][FRIDAY].day, 1);
    assert.equal(grid.weeks[0][FRIDAY].iso, "2026-10-23");
    assert.equal(grid.weeks[0][FRIDAY].friday, true);
    assert.equal(grid.weeks[1][0].day, 2);
    assert.equal(grid.weeks[1][0].friday, false);
    assert.deepEqual(grid.weeks[0].slice(0, FRIDAY), [null, null, null, null, null, null]);
    assert.equal(grid.weeks.flat().filter(Boolean).length, 30);
  });

  it("puts every Friday in the last column and marks it", () => {
    for (const [year, month] of [
      [1405, 8],
      [1403, 12],
      [1404, 12],
      [1405, 12],
    ]) {
      for (const week of monthGrid(year, month).weeks) {
        assert.equal(week.length, 7);
        for (const [column, cell] of week.entries()) {
          if (!cell) continue;
          assert.equal(cell.weekday, jalaliWeekdayIndex(year, month, cell.day));
          assert.equal(cell.friday, cell.weekday === FRIDAY);
          assert.equal(cell.friday, column === FRIDAY);
        }
      }
    }
  });

  it("shows 30 Esfand days in 1403 and 29 in 1404", () => {
    assert.equal(monthGrid(1403, 12).daysInMonth, 30);
    assert.equal(monthGrid(1404, 12).daysInMonth, 29);
    assert.equal(monthGrid(1404, 12).weeks.flat().filter(Boolean).at(-1).day, 29);
  });

  it("keeps the grid inside the month, in order, every day once", () => {
    const days = monthGrid(1405, 1)
      .weeks.flat()
      .filter(Boolean)
      .map((cell) => cell.day);
    assert.deepEqual(
      days,
      Array.from({ length: 31 }, (_, index) => index + 1),
    );
  });
});

describe("Persian formatting", () => {
  it("writes the Jalali date with Persian digits and zero padding", () => {
    assert.equal(toPersianDigits("1405/08/09"), "۱۴۰۵/۰۸/۰۹");
    assert.equal(formatJalali(1405, 8, 9), "۱۴۰۵/۰۸/۰۹");
    assert.equal(jalaliText("2026-10-31"), "۱۴۰۵/۰۸/۰۹");
    assert.equal(jalaliText(""), "");
  });

  it("folds both Persian and Arabic-Indic digits back to Latin", () => {
    assert.equal(foldDigits("۱۴۰۵/۰۸/۰۹"), "1405/08/09");
    assert.equal(foldDigits("١٤٠٥"), "1405");
    assert.equal(foldDigits("1405"), "1405");
  });

  it("names months and weekdays in Persian", () => {
    assert.equal(JALALI_MONTHS.length, 12);
    assert.equal(JALALI_MONTHS[0], "فروردین");
    assert.equal(JALALI_MONTHS[11], "اسفند");
    assert.equal(WEEKDAY_NAMES.length, 7);
    assert.equal(WEEKDAY_NAMES[0], "شنبه");
    assert.equal(WEEKDAY_NAMES[6], "جمعه");
    assert.equal(monthTitle(1405, 8), "آبان ۱۴۰۵");
  });

  it("formats the day number in Persian digits for a grid cell", () => {
    assert.equal(toPersianDigits("1"), "۱");
    assert.equal(toPersianDigits("30"), "۳۰");
  });
});

describe("adding days never leaves the calendar", () => {
  it("moves one day across the Gregorian new year in both directions", () => {
    assert.equal(addDaysIso("2024-12-31", 1), "2025-01-01");
    assert.equal(addDaysIso("2025-01-01", -1), "2024-12-31");
    assert.equal(addDaysIso("2028-02-28", 1), "2028-02-29");
    assert.equal(addDaysIso("2028-02-29", 1), "2028-03-01");
  });

  it("rejects an ISO day the calendar does not have", () => {
    assert.equal(jalaliFromIso("2025-02-30"), null);
    assert.equal(jalaliFromIso(""), null);
    assert.throws(() => addDaysIso("2025-02-30", 1), RangeError);
  });
});

describe("weekday index counts from Saturday", () => {
  it("names 1403/01/01 (2024-03-20) a Wednesday in the fourth column", () => {
    assert.equal(weekdayIndex(2024, 3, 20), 4);
    assert.equal(WEEKDAY_NAMES[4], "چهارشنبه");
  });

  it("names 1403/01/03 (2024-03-22) a Friday in the last column", () => {
    assert.equal(weekdayIndex(2024, 3, 22), FRIDAY);
    assert.equal(jalaliWeekdayIndex(1403, 1, 3), FRIDAY);
  });
});
