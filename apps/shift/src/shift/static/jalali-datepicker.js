/**
 * Sib Shift — a Jalali (Solar Hijri) date picker, dependency-free vanilla JS.
 *
 * Usage: link the stylesheet and load this file, then put the attribute on an input.
 *
 *   <link rel="stylesheet" href="/static/jalali-datepicker.css">
 *   <input class="form-control" type="text" name="starts_on" data-jalali-date
 *          data-min="1405/01/01" data-max="1405/12/29" value="2026-10-31">
 *   <script src="/static/jalali-datepicker.js" defer></script>
 *
 * The visible text is Jalali with Persian digits (۱۴۰۵/۰۸/۰۹). The form still submits the ISO
 * Gregorian date: the input's `name` moves to a hidden field, so a server sees `starts_on=2026-10-31`
 * exactly as a native `<input type="date">` would send it. Page scripts read and write the value
 * with `ShiftJalaliDatepicker.getIso(input)` / `setIso(input, "2026-10-31")` and listen for the same
 * `input` and `change` events a native date input fires. `data-min`/`data-max` (or native `min`/`max`)
 * take a Jalali bound with slashes (`1405/01/01`) or an ISO bound with dashes (`2026-03-21`); the same
 * rule reads an initial `value`. A date the calendar does not have (1404/12/30) or a date outside the
 * bounds is refused with a Persian message and nothing is submitted for that field.
 *
 * "Today" is always Tehran (UTC+03:30 fixed, no DST), never the phone's own time zone, and the
 * calendar arithmetic is the same 33-year cycle as `src/shift/common/jalali.py`. Nodes are built
 * with `createElement` and no inline style, so the page's CSP needs no exception.
 */

(function () {
  "use strict";

  // --- names and digits ---------------------------------------------------

  const JALALI_MONTHS = [
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
  ];
  const WEEKDAY_NAMES = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"];
  const WEEKDAY_SHORT = ["ش", "ی", "د", "س", "چ", "پ", "ج"];
  const FRIDAY = 6;

  const PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  const ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";
  const DIGIT_RANGE = /[\u06f0-\u06f9\u0660-\u0669]/g;
  const INVISIBLE_MARKS = /[\u200c\u200e\u200f]/g;

  // Iran has had no daylight saving since 2022: the clinic's day is a fixed +03:30.
  const TEHRAN_OFFSET_MS = (3 * 60 + 30) * 60 * 1000;

  const MIN_JALALI_YEAR = 978; // the converter's own domain: 978/10/11 is 1600-01-01
  const MAX_JALALI_YEAR = 9999;

  const SHAPE_MESSAGE = "تاریخ را به شکل ۱۴۰۵/۰۸/۰۹ بنویسید.";
  const PANEL_LABEL = "انتخاب تاریخ";
  const CLEAR_LABEL = "پاک کردن";
  const PLACEHOLDER = "۱۴۰۵/۰۸/۰۹";

  // --- Jalali <-> Gregorian, the algorithm of src/shift/common/jalali.py ----

  const MONTH_STARTS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];

  function isInteger(value) {
    return typeof value === "number" && Number.isInteger(value);
  }

  function isLeapGregorianYear(year) {
    return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  }

  function daysInGregorianMonth(year, month) {
    if (month === 2) return isLeapGregorianYear(year) ? 29 : 28;
    return month === 4 || month === 6 || month === 9 || month === 11 ? 30 : 31;
  }

  function isValidGregorianDate(year, month, day) {
    if (!isInteger(year) || !isInteger(month) || !isInteger(day)) return false;
    if (month < 1 || month > 12 || day < 1) return false;
    return day <= daysInGregorianMonth(year, month);
  }

  /** Days since 1600-01-01 in the proleptic Gregorian calendar (the Python module's axis). */
  function gregorianDayNumber(year, month, day) {
    if (!isValidGregorianDate(year, month, day)) {
      throw new RangeError("invalid Gregorian date");
    }
    let days = 365 * (year - 1600) + Math.floor((year - 1600 + 3) / 4) - Math.floor((year - 1600 + 99) / 100);
    days += Math.floor((year - 1600 + 399) / 400) + MONTH_STARTS[month - 1] + day - 1;
    if (month > 2 && isLeapGregorianYear(year)) days += 1;
    return days;
  }

  function gregorianToJalali(year, month, day) {
    let days = gregorianDayNumber(year, month, day) - 79;
    const cycles = Math.floor(days / 12053);
    days -= cycles * 12053;
    let jalaliYear = 979 + 33 * cycles + 4 * Math.floor(days / 1461);
    days %= 1461;
    if (days >= 366) {
      jalaliYear += Math.floor((days - 1) / 365);
      days = (days - 1) % 365;
    }
    if (days < 186) {
      return [jalaliYear, 1 + Math.floor(days / 31), 1 + (days % 31)];
    }
    return [jalaliYear, 7 + Math.floor((days - 186) / 30), 1 + ((days - 186) % 30)];
  }

  function jalaliDayNumber(year, month, day) {
    let days = 365 * (year - 979) + Math.floor((year - 979) / 33) * 8 + Math.floor(((year - 979) % 33 + 3) / 4);
    for (let index = 0; index < month - 1; index += 1) {
      days += index < 6 ? 31 : 30;
    }
    return days + day - 1;
  }

  /** The Gregorian civil date `days` after 1600-01-01 (the inverse of gregorianDayNumber). */
  function gregorianFromDayNumber(days) {
    if (!isInteger(days) || days < 0) {
      throw new RangeError("day number outside the supported range");
    }
    let gregorianYear = 1600 + 400 * Math.floor(days / 146097);
    let rest = days % 146097;
    let leap = true;
    if (rest >= 36525) {
      rest -= 1;
      gregorianYear += 100 * Math.floor(rest / 36524);
      rest %= 36524;
      if (rest >= 365) {
        rest += 1;
      } else {
        leap = false;
      }
    }
    gregorianYear += 4 * Math.floor(rest / 1461);
    rest %= 1461;
    if (rest >= 366) {
      leap = false;
      rest -= 1;
      gregorianYear += Math.floor(rest / 365);
      rest %= 365;
    }
    const starts = leap ? MONTH_STARTS.map((start, index) => (index >= 2 ? start + 1 : start)) : MONTH_STARTS;
    let month = 1;
    while (month < 12 && rest >= starts[month]) month += 1;
    return [gregorianYear, month, rest - starts[month - 1] + 1];
  }

  /** Jalali date as [Gregorian year, month, day]; throws RangeError on a date not in the calendar. */
  function jalaliToGregorian(year, month, day) {
    if (!isInteger(year) || !isInteger(month) || !isInteger(day)) {
      throw new RangeError("invalid Jalali date");
    }
    if (year < MIN_JALALI_YEAR || year > MAX_JALALI_YEAR) {
      throw new RangeError("Jalali year outside the supported range");
    }
    if (month < 1 || month > 12 || day < 1 || day > 31) {
      throw new RangeError("invalid Jalali month or day");
    }
    const gregorian = gregorianFromDayNumber(jalaliDayNumber(year, month, day) + 79);
    const back = gregorianToJalali(gregorian[0], gregorian[1], gregorian[2]);
    if (back[0] !== year || back[1] !== month || back[2] !== day) {
      throw new RangeError("invalid Jalali date");
    }
    return gregorian;
  }

  function isValidJalaliDate(year, month, day) {
    try {
      jalaliToGregorian(year, month, day);
      return true;
    } catch (error) {
      return false;
    }
  }

  /** The 33-year cycle decides the leap years: 1399, 1403 and 1408 have an Esfand 30. */
  function isLeapJalaliYear(year) {
    if (!isInteger(year) || year < MIN_JALALI_YEAR || year > MAX_JALALI_YEAR) return false;
    return jalaliDayNumber(year + 1, 1, 1) - jalaliDayNumber(year, 1, 1) === 366;
  }

  function daysInJalaliMonth(year, month) {
    if (month <= 6) return 31;
    if (month <= 11) return 30;
    return isLeapJalaliYear(year) ? 30 : 29;
  }

  function pad(value, length) {
    return String(value).padStart(length, "0");
  }

  function isoFromJalali(year, month, day) {
    const gregorian = jalaliToGregorian(year, month, day);
    return `${pad(gregorian[0], 4)}-${pad(gregorian[1], 2)}-${pad(gregorian[2], 2)}`;
  }

  /** [Jalali year, month, day] for an ISO Gregorian day, or null when the day does not exist. */
  function jalaliFromIso(iso) {
    if (typeof iso !== "string") return null;
    const parts = iso.trim().split("-");
    if (parts.length !== 3 || parts.some((part) => !/^\d{1,4}$/.test(part))) return null;
    const numbers = parts.map(Number);
    if (!isValidGregorianDate(numbers[0], numbers[1], numbers[2])) return null;
    return gregorianToJalali(numbers[0], numbers[1], numbers[2]);
  }

  /** The ISO day of the Jalali cell at the same place in the month, `delta` days away. */
  function addDaysIso(iso, delta) {
    if (!isInteger(delta)) throw new RangeError("delta must be a whole number of days");
    const parts = String(iso).split("-").map(Number);
    if (parts.length !== 3 || !isValidGregorianDate(parts[0], parts[1], parts[2])) {
      throw new RangeError("invalid ISO date");
    }
    const moved = gregorianFromDayNumber(gregorianDayNumber(parts[0], parts[1], parts[2]) + delta);
    return `${pad(moved[0], 4)}-${pad(moved[1], 2)}-${pad(moved[2], 2)}`;
  }

  // --- Persian text -------------------------------------------------------

  function foldDigits(text) {
    return String(text == null ? "" : text).replace(DIGIT_RANGE, (digit) => {
      const persian = PERSIAN_DIGITS.indexOf(digit);
      return String(persian >= 0 ? persian : ARABIC_INDIC_DIGITS.indexOf(digit));
    });
  }

  function toPersianDigits(text) {
    return String(text == null ? "" : text).replace(/[0-9]/g, (digit) => PERSIAN_DIGITS.charAt(Number(digit)));
  }

  /** `1405/08/09` in Persian digits, the text the person sees. */
  function formatJalali(year, month, day) {
    return toPersianDigits(`${pad(year, 4)}/${pad(month, 2)}/${pad(day, 2)}`);
  }

  function jalaliText(iso) {
    const jalali = jalaliFromIso(iso);
    return jalali ? formatJalali(jalali[0], jalali[1], jalali[2]) : "";
  }

  function invalidCalendarMessage(text) {
    return `${toPersianDigits(foldDigits(text).trim())} در تقویم جلالی وجود ندارد.`;
  }

  function outOfRangeMessage(bounds) {
    const min = bounds && bounds.minIso ? jalaliText(bounds.minIso) : "";
    const max = bounds && bounds.maxIso ? jalaliText(bounds.maxIso) : "";
    if (min && max) return `تاریخ باید بین ${min} و ${max} باشد.`;
    if (min) return `تاریخ نمی‌تواند پیش از ${min} باشد.`;
    if (max) return `تاریخ نمی‌تواند بعد از ${max} باشد.`;
    return SHAPE_MESSAGE;
  }

  // --- reading what the person typed --------------------------------------

  function withinBounds(iso, bounds) {
    if (!iso) return false;
    const min = bounds && bounds.minIso ? bounds.minIso : "";
    const max = bounds && bounds.maxIso ? bounds.maxIso : "";
    if (min && iso < min) return false;
    if (max && iso > max) return false;
    return true;
  }

  /**
   * Read `۱۴۰۵/۰۸/۰۹` (Persian or Latin digits, `/`, `-` or `.`) into an ISO day.
   * Returns `{ ok, iso, year, month, day, empty, error }`; `error` is the Persian line to show.
   */
  function parseJalaliInput(text, bounds) {
    const raw = typeof text === "string" ? text : "";
    const folded = foldDigits(raw).replace(INVISIBLE_MARKS, "").trim();
    if (!folded) return { ok: false, iso: "", empty: true, error: "" };

    const match = /^(\d{1,4})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})$/.exec(folded);
    if (!match) return { ok: false, iso: "", empty: false, error: SHAPE_MESSAGE };

    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    if (!isValidJalaliDate(year, month, day)) {
      return { ok: false, iso: "", empty: false, error: invalidCalendarMessage(raw) };
    }

    const iso = isoFromJalali(year, month, day);
    if (!withinBounds(iso, bounds)) {
      return { ok: false, iso: "", empty: false, error: outOfRangeMessage(bounds) };
    }
    return { ok: true, iso, year, month, day, empty: false, error: "" };
  }

  /** A `data-min`/`data-max` bound: dashes mean an ISO Gregorian day, slashes mean Jalali. */
  function parseBound(text) {
    const raw = typeof text === "string" ? text.trim() : "";
    if (!raw) return null;
    const folded = foldDigits(raw).replace(INVISIBLE_MARKS, "");
    if (/^\d{1,4}-\d{1,2}-\d{1,2}$/.test(folded)) {
      const gregorian = jalaliFromIso(folded);
      return gregorian ? isoFromJalali(gregorian[0], gregorian[1], gregorian[2]) : null;
    }
    const parsed = parseJalaliInput(raw);
    return parsed.ok ? parsed.iso : null;
  }

  // --- the clinic's day ---------------------------------------------------

  /** The Gregorian civil date in Tehran for an instant, whatever time zone the phone is in. */
  function tehranDateFromInstant(epochMs) {
    const instant = Number(epochMs);
    if (!Number.isFinite(instant)) throw new RangeError("instant must be a number of milliseconds");
    const shifted = new Date(instant + TEHRAN_OFFSET_MS);
    return [shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, shifted.getUTCDate()];
  }

  function tehranTodayJalali(epochMs) {
    return gregorianToJalali(...tehranDateFromInstant(epochMs));
  }

  // --- weekday and the month grid -----------------------------------------

  /** 0 is Saturday (the first column), 6 is Friday. */
  function weekdayIndex(year, month, day) {
    const days = gregorianDayNumber(year, month, day);
    return ((days % 7) + 7) % 7;
  }

  function jalaliWeekdayIndex(year, month, day) {
    return weekdayIndex(...jalaliToGregorian(year, month, day));
  }

  function monthTitle(year, month) {
    return `${JALALI_MONTHS[month - 1]} ${toPersianDigits(String(year))}`;
  }

  /** One month as weeks of seven cells (null is an empty cell); Saturday sits in the first column. */
  function monthGrid(year, month) {
    const daysInMonth = daysInJalaliMonth(year, month);
    const firstWeekday = jalaliWeekdayIndex(year, month, 1);
    const cells = new Array(firstWeekday).fill(null);
    for (let day = 1; day <= daysInMonth; day += 1) {
      const weekday = (firstWeekday + day - 1) % 7;
      cells.push({ day, weekday, iso: isoFromJalali(year, month, day), friday: weekday === FRIDAY });
    }
    while (cells.length % 7 !== 0) cells.push(null);
    const weeks = [];
    for (let index = 0; index < cells.length; index += 7) weeks.push(cells.slice(index, index + 7));
    return { year, month, daysInMonth, firstWeekday, weeks };
  }

  // --- the picker in the page ---------------------------------------------

  const instances = new WeakMap();
  let sequence = 0;

  function svgChevron(forward) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("d", forward === "next-month" ? "M10 3L5 8l5 5" : "M6 3l5 5-5 5");
    svg.appendChild(path);
    return svg;
  }

  function svgDoubleChevron(forward) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("d", forward === "next-year" ? "M7 3L2 8l5 5M14 3l-5 5 5 5" : "M9 3l5 5-5 5M2 3l5 5-5 5");
    svg.appendChild(path);
    return svg;
  }

  function navButton(label, glyphFor, direction) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "jp-nav";
    button.setAttribute("aria-label", label);
    button.appendChild(glyphFor(direction));
    button.dataset.jpDirection = direction;
    return button;
  }

  function buildPanel(instance) {
    const panel = document.createElement("div");
    panel.className = "jp-panel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "false");
    panel.setAttribute("aria-label", PANEL_LABEL);

    panel.id = `jp-panel-${instance.id}`;

    const head = document.createElement("div");
    head.className = "jp-head";
    instance.navButtons = {
      "prev-year": navButton("سال قبل", svgDoubleChevron, "prev-year"),
      "prev-month": navButton("ماه قبل", svgChevron, "prev-month"),
      "next-month": navButton("ماه بعد", svgChevron, "next-month"),
      "next-year": navButton("سال بعد", svgDoubleChevron, "next-year"),
    };

    const title = document.createElement("span");
    title.className = "jp-title";
    title.id = `jp-title-${instance.id}`;
    instance.title = title;

    head.append(
      instance.navButtons["prev-year"],
      instance.navButtons["prev-month"],
      title,
      instance.navButtons["next-month"],
      instance.navButtons["next-year"],
    );

    const grid = document.createElement("table");
    grid.className = "jp-grid";
    grid.setAttribute("role", "grid");
    grid.setAttribute("aria-labelledby", title.id);

    const headRow = document.createElement("tr");
    WEEKDAY_SHORT.forEach((short, index) => {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.setAttribute("abbr", WEEKDAY_NAMES[index]);
      cell.setAttribute("aria-label", WEEKDAY_NAMES[index]);
      cell.textContent = short;
      headRow.appendChild(cell);
    });
    const thead = document.createElement("thead");
    thead.appendChild(headRow);

    instance.body = document.createElement("tbody");
    grid.append(thead, instance.body);

    const foot = document.createElement("div");
    foot.className = "jp-foot";
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "jp-clear";
    clear.textContent = CLEAR_LABEL;
    instance.clearButton = clear;
    foot.appendChild(clear);

    panel.append(head, grid, foot);
    return panel;
  }

  function activeCell(instance) {
    return instance.body.querySelector('td[tabindex="0"]');
  }

  function focusCell(instance, iso) {
    const cell = iso ? instance.body.querySelector(`td[data-iso="${iso}"]`) : null;
    const target = cell || activeCell(instance) || instance.body.querySelector("td.jp-day");
    if (!target) return;
    for (const other of instance.body.querySelectorAll('td[tabindex="0"]')) other.tabIndex = -1;
    target.tabIndex = 0;
    target.focus();
  }

  function render(instance) {
    const view = instance.view;
    const grid = monthGrid(view.year, view.month);
    instance.title.textContent = monthTitle(view.year, view.month);
    instance.body.replaceChildren();

    for (const week of grid.weeks) {
      const row = document.createElement("tr");
      for (const cell of week) {
        if (!cell) {
          row.appendChild(document.createElement("td"));
          continue;
        }
        const td = document.createElement("td");
        td.className = "jp-day";
        td.setAttribute("role", "gridcell");
        td.tabIndex = -1;
        td.dataset.iso = cell.iso;
        td.textContent = toPersianDigits(String(cell.day));
        td.setAttribute("aria-label", `${toPersianDigits(String(cell.day))} ${JALALI_MONTHS[view.month - 1]} ${toPersianDigits(String(view.year))}`);
        if (cell.friday) td.classList.add("jp-day--friday");
        if (cell.iso === instance.todayIso) {
          td.classList.add("jp-day--today");
          td.setAttribute("aria-current", "date");
        }
        const selected = cell.iso === instance.iso;
        td.setAttribute("aria-selected", selected ? "true" : "false");
        if (selected) td.classList.add("jp-day--selected");
        if (!withinBounds(cell.iso, instance.bounds)) {
          td.setAttribute("aria-disabled", "true");
          td.classList.add("jp-day--disabled");
        }
        row.appendChild(td);
      }
      instance.body.appendChild(row);
    }

    if (instance.focusIso) {
      const target = instance.body.querySelector(`td[data-iso="${instance.focusIso}"]`);
      if (target) target.tabIndex = 0;
    }
    if (!activeCell(instance)) {
      const fallback =
        instance.body.querySelector("td.jp-day--selected") ||
        instance.body.querySelector("td.jp-day--today") ||
        instance.body.querySelector("td.jp-day");
      if (fallback) fallback.tabIndex = 0;
    }

    for (const [direction, button] of Object.entries(instance.navButtons)) {
      button.disabled = !monthHasSelectableDay(instance, direction);
    }
    instance.clearButton.disabled = !instance.input.value;
  }

  function neighbourMonth(year, month, months, years) {
    const total = year * 12 + (month - 1) + years * 12 + months;
    return [Math.floor(total / 12), (total % 12) + 1];
  }

  function monthFirstIso(year, month) {
    return isoFromJalali(year, month, 1);
  }

  function monthLastIso(year, month) {
    return isoFromJalali(year, month, daysInJalaliMonth(year, month));
  }

  /** A month is reachable while at least one of its days is inside the bounds. */
  function monthHasSelectableDay(instance, direction) {
    const step = { "prev-year": [-1, 0], "prev-month": [0, -1], "next-month": [0, 1], "next-year": [1, 0] }[direction];
    if (!step) return false;
    const [year, month] = neighbourMonth(instance.view.year, instance.view.month, step[1], step[0]);
    if (year < MIN_JALALI_YEAR || year > MAX_JALALI_YEAR) return false;
    const min = instance.bounds.minIso;
    const max = instance.bounds.maxIso;
    if (min && monthLastIso(year, month) < min) return false;
    if (max && monthFirstIso(year, month) > max) return false;
    return true;
  }

  function showView(instance, year, month) {
    const clampedYear = Math.min(Math.max(year, MIN_JALALI_YEAR), MAX_JALALI_YEAR);
    instance.view = { year: clampedYear, month: Math.min(Math.max(month, 1), 12) };
    render(instance);
  }

  function moveView(instance, months, years) {
    const [year, month] = neighbourMonth(instance.view.year, instance.view.month, months, years);
    showView(instance, year, month);
  }

  function showError(instance, message) {
    instance.error.textContent = message;
    instance.error.hidden = false;
    instance.input.setAttribute("aria-invalid", "true");
  }

  function clearError(instance) {
    instance.error.textContent = "";
    instance.error.hidden = true;
    instance.input.removeAttribute("aria-invalid");
  }

  function emitChange(instance) {
    instance.input.dispatchEvent(new Event("input", { bubbles: true }));
    instance.input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /** Write a validated ISO day (or "") into the field, the hidden field and the visible text. */
  function setValue(instance, iso, options) {
    const emit = Boolean(options && options.emit);
    const next = iso || "";
    if (next && !jalaliFromIso(next)) throw new RangeError("invalid ISO date");
    const changed = next !== instance.iso;
    instance.iso = next;
    instance.input.value = next ? jalaliText(next) : "";
    if (instance.hidden) instance.hidden.value = next;
    clearError(instance);
    if (instance.panel && !instance.panel.hidden) render(instance);
    if (emit && changed) emitChange(instance);
  }

  function open(instance, options) {
    const today = tehranTodayJalali(Date.now());
    instance.todayIso = jalaliIsoOf(today);
    const bounds = instance.bounds;
    let view = jalaliFromIso(instance.iso);
    if (!view && bounds.maxIso && instance.todayIso > bounds.maxIso) view = jalaliFromIso(bounds.maxIso);
    if (!view && bounds.minIso && instance.todayIso < bounds.minIso) view = jalaliFromIso(bounds.minIso);
    if (!view) view = today;
    instance.focusIso = instance.iso || "";
    showView(instance, view[0], view[1]);
    instance.panel.hidden = false;
    instance.input.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", instance.onOutsidePointerDown, true);
    if (options && options.focusGrid) focusCell(instance, instance.iso || instance.todayIso);
  }

  function close(instance, options) {
    if (instance.panel.hidden) return;
    instance.panel.hidden = true;
    instance.input.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", instance.onOutsidePointerDown, true);
    if (options && options.focusInput) instance.input.focus();
  }

  function jalaliIsoOf(jalali) {
    return isoFromJalali(jalali[0], jalali[1], jalali[2]);
  }

  function toggle(instance) {
    if (instance.panel.hidden) open(instance);
    else close(instance);
  }

  /** Read the text field into a value; returns true when what is there is a usable date. */
  function commitText(instance) {
    const result = parseJalaliInput(instance.input.value, instance.bounds);
    if (result.ok) {
      setValue(instance, result.iso, { emit: true });
      return true;
    }
    if (result.empty) {
      setValue(instance, "", { emit: true });
      return true;
    }
    instance.iso = "";
    if (instance.hidden) instance.hidden.value = "";
    showError(instance, result.error);
    return false;
  }

  function selectDay(instance, iso) {
    if (!withinBounds(iso, instance.bounds)) return;
    setValue(instance, iso, { emit: true });
    close(instance, { focusInput: true });
  }

  function clearValue(instance) {
    setValue(instance, "", { emit: true });
    close(instance, { focusInput: true });
  }

  function onGridKeydown(instance, event) {
    const cell = event.target.closest ? event.target.closest("td.jp-day") : null;
    if (!cell) return;
    const steps = { ArrowLeft: 1, ArrowRight: -1, ArrowUp: -7, ArrowDown: 7 };
    const step = steps[event.key];

    if (step !== undefined) {
      event.preventDefault();
      const target = addDaysIso(cell.dataset.iso, step);
      const jalali = jalaliFromIso(target);
      if (!jalali) return;
      instance.focusIso = target;
      if (jalali[0] !== instance.view.year || jalali[1] !== instance.view.month) {
        showView(instance, jalali[0], jalali[1]);
      }
      focusCell(instance, target);
      return;
    }

    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const day = event.key === "Home" ? 1 : daysInJalaliMonth(instance.view.year, instance.view.month);
      instance.focusIso = isoFromJalali(instance.view.year, instance.view.month, day);
      focusCell(instance, instance.focusIso);
      return;
    }

    if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      const direction = event.key === "PageUp" ? -1 : 1;
      if (event.shiftKey) moveView(instance, 0, direction);
      else moveView(instance, direction, 0);
      focusCell(instance, instance.focusIso || instance.todayIso);
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectDay(instance, cell.dataset.iso);
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      close(instance, { focusInput: true });
    }
  }

  function onInputKeydown(instance, event) {
    if (event.key === "ArrowDown" && !event.altKey) {
      event.preventDefault();
      open(instance, { focusGrid: true });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (!commitText(instance)) return;
      close(instance);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      close(instance);
    }
  }

  function defaultIso(instance) {
    const raw = String(instance.defaultText || "");
    if (!raw) return "";
    const folded = foldDigits(raw).replace(INVISIBLE_MARKS, "").trim();
    if (/^\d{1,4}-\d{1,2}-\d{1,2}$/.test(folded)) {
      const gregorian = jalaliFromIso(folded);
      return gregorian ? jalaliIsoOf(gregorian) : "";
    }
    const parsed = parseJalaliInput(raw, instance.bounds);
    return parsed.ok ? parsed.iso : "";
  }

  /** Turn a plain input into a Jalali date field; safe to call twice on the same input. */
  function attach(input) {
    if (!input || input.tagName !== "INPUT") return null;
    const existing = instances.get(input);
    if (existing) return existing;

    const field = document.createElement("div");
    field.className = "jp-field";
    input.parentNode.insertBefore(field, input);
    field.appendChild(input);

    input.classList.add("jp-input");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("aria-haspopup", "dialog");
    input.setAttribute("aria-expanded", "false");
    if (!input.getAttribute("placeholder")) input.setAttribute("placeholder", PLACEHOLDER);
    if (!input.type || input.type === "date" || input.type === "text") input.type = "text";

    const instance = {
      id: (sequence += 1),
      input,
      field,
      hidden: null,
      panel: null,
      error: null,
      todayIso: "",
      iso: "",
      focusIso: "",
      view: { year: 0, month: 0 },
      bounds: {
        minIso: parseBound(input.getAttribute("data-min") || input.getAttribute("min")),
        maxIso: parseBound(input.getAttribute("data-max") || input.getAttribute("max")),
      },
      defaultText: String(input.value || ""),
    };
    instances.set(input, instance);

    const name = input.getAttribute("name");
    if (name) {
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      hidden.setAttribute("data-jalali-iso", "");
      input.removeAttribute("name");
      field.appendChild(hidden);
      instance.hidden = hidden;
    }

    const error = document.createElement("p");
    error.className = "jp-error";
    error.id = `jp-error-${instance.id}`;
    error.hidden = true;
    input.insertAdjacentElement("afterend", error);
    input.setAttribute("aria-describedby", error.id);
    instance.error = error;

    const panel = buildPanel(instance);
    field.appendChild(panel);
    instance.panel = panel;
    input.setAttribute("aria-controls", panel.id);

    instance.onOutsidePointerDown = (event) => {
      if (!field.contains(event.target)) close(instance);
    };

    const initial = defaultIso(instance);
    const typed = String(input.value || "").trim();
    if (initial) {
      setValue(instance, initial, { emit: false });
    } else if (typed) {
      showError(instance, parseJalaliInput(typed, instance.bounds).error || SHAPE_MESSAGE);
    }

    input.addEventListener("click", () => toggle(instance));
    input.addEventListener("keydown", (event) => onInputKeydown(instance, event));
    input.addEventListener("input", () => {
      if (!instance.error.hidden) clearError(instance);
    });
    // A picked day keeps focus inside the field; anything else means the person left the
    // field and the text they typed is read then, so a half-typed date never travels on.
    input.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (!field.contains(document.activeElement)) commitText(instance);
      }, 0);
    });

    for (const button of Object.values(instance.navButtons)) {
      button.addEventListener("click", () => {
        const steps = { "prev-year": [0, -1], "prev-month": [-1, 0], "next-month": [1, 0], "next-year": [0, 1] };
        const step = steps[button.dataset.jpDirection];
        moveView(instance, step[0], step[1]);
        focusCell(instance, instance.focusIso);
      });
    }
    instance.clearButton.addEventListener("click", () => clearValue(instance));
    instance.body.addEventListener("keydown", (event) => onGridKeydown(instance, event));
    instance.body.addEventListener("click", (event) => {
      const cell = event.target.closest ? event.target.closest("td.jp-day") : null;
      if (cell) selectDay(instance, cell.dataset.iso);
    });
    instance.body.addEventListener("focusin", (event) => {
      const cell = event.target.closest ? event.target.closest("td.jp-day") : null;
      if (!cell) return;
      instance.focusIso = cell.dataset.iso;
      for (const other of instance.body.querySelectorAll('td[tabindex="0"]')) other.tabIndex = -1;
      cell.tabIndex = 0;
    });
    field.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !field.contains(event.relatedTarget)) close(instance);
    });
    field.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(instance, { focusInput: true });
      }
    });

    const form = input.form;
    if (form) {
      // The browser refills the input from its attribute after the reset event, so resync after it.
      form.addEventListener("reset", () => {
        window.setTimeout(() => {
          instance.defaultText = String(input.value || "");
          const iso = defaultIso(instance);
          setValue(instance, iso, { emit: false });
          if (!iso && instance.defaultText.trim()) commitText(instance);
        }, 0);
      });
    }

    return instance;
  }

  function init(root) {
    const scope = root && root.querySelectorAll ? root : document;
    for (const input of scope.querySelectorAll("input[data-jalali-date]")) attach(input);
  }

  function getIso(input) {
    const instance = input ? instances.get(input) : null;
    return instance ? instance.iso : "";
  }

  function setIso(input, iso) {
    const instance = input ? instances.get(input) : null;
    if (!instance) throw new Error("setIso needs an attached input");
    setValue(instance, iso, { emit: true });
  }

  function openPanel(input) {
    const instance = input ? instances.get(input) : null;
    if (instance) open(instance);
  }

  function closePanel(input) {
    const instance = input ? instances.get(input) : null;
    if (instance) close(instance);
  }

  const api = { attach, init, getIso, setIso, open: openPanel, close: closePanel };

  if (typeof window !== "undefined") {
    window.ShiftJalaliDatepicker = api;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => init(document));
    } else {
      init(document);
    }
    // Wizards draw their fields after load, so a date input that appears later is picked up too.
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node.nodeType !== 1) continue;
          if (node.matches("input[data-jalali-date]")) attach(node);
          else init(node);
        }
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      JALALI_MONTHS,
      WEEKDAY_NAMES,
      WEEKDAY_SHORT,
      FRIDAY,
      SHAPE_MESSAGE,
      MIN_JALALI_YEAR,
      MAX_JALALI_YEAR,
      foldDigits,
      toPersianDigits,
      gregorianDayNumber,
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
      api,
    };
  }
})();
