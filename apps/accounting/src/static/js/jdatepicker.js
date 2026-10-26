/* Jalali date picker (FB-24). No network, no dependencies besides jalaali.min.js.
 *
 * Any <input data-jdp> becomes a picker: clicking it opens a month calendar, choosing a day writes
 * 'YYYY/MM/DD' (Latin digits, what the server expects). Typing still works. Works in RTL pages and
 * on light and dark backgrounds (it draws its own white panel).
 */
(function () {
  'use strict';
  if (!window.jalaali) { return; }
  var J = window.jalaali;
  var MONTHS = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
  var WEEK = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج'];
  var FA = '۰۱۲۳۴۵۶۷۸۹';
  function fa(n) { return String(n).replace(/[0-9]/g, function (d) { return FA[+d]; }); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function today() { var d = new Date(); return J.toJalaali(d.getFullYear(), d.getMonth() + 1, d.getDate()); }
  function parse(v) {
    var m = String(v || '').replace(/[۰-۹]/g, function (c) { return FA.indexOf(c); }).match(/^(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})$/);
    if (!m) { return null; }
    var jy = +m[1], jm = +m[2], jd = +m[3];
    if (jm < 1 || jm > 12 || jd < 1 || jd > J.jalaaliMonthLength(jy, jm)) { return null; }
    return { jy: jy, jm: jm, jd: jd };
  }

  var panel = null, current = null, view = null;

  function close() { if (panel) { panel.remove(); panel = null; current = null; } }

  function render() {
    panel.innerHTML = '';
    var head = document.createElement('div');
    head.className = 'jdp-head';
    var prev = document.createElement('button'); prev.type = 'button'; prev.textContent = '›'; prev.title = 'ماه قبل';
    var next = document.createElement('button'); next.type = 'button'; next.textContent = '‹'; next.title = 'ماه بعد';
    var title = document.createElement('span'); title.textContent = MONTHS[view.jm - 1] + ' ' + fa(view.jy);
    prev.onclick = function () { view.jm -= 1; if (view.jm < 1) { view.jm = 12; view.jy -= 1; } render(); };
    next.onclick = function () { view.jm += 1; if (view.jm > 12) { view.jm = 1; view.jy += 1; } render(); };
    head.appendChild(prev); head.appendChild(title); head.appendChild(next);
    panel.appendChild(head);

    var grid = document.createElement('div'); grid.className = 'jdp-grid';
    WEEK.forEach(function (w) { var c = document.createElement('span'); c.className = 'jdp-w'; c.textContent = w; grid.appendChild(c); });
    var g = J.toGregorian(view.jy, view.jm, 1);
    var first = new Date(g.gy, g.gm - 1, g.gd).getDay(); // 0 = Sunday
    var offset = (first + 1) % 7; // Saturday first
    for (var i = 0; i < offset; i++) { grid.appendChild(document.createElement('span')); }
    var chosen = parse(current.value), now = today();
    for (var d = 1; d <= J.jalaaliMonthLength(view.jy, view.jm); d++) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = fa(d);
      if (now.jy === view.jy && now.jm === view.jm && now.jd === d) { b.className = 'jdp-today'; }
      if (chosen && chosen.jy === view.jy && chosen.jm === view.jm && chosen.jd === d) { b.className = 'jdp-sel'; }
      (function (day) {
        b.onclick = function () {
          current.value = view.jy + '/' + pad(view.jm) + '/' + pad(day);
          current.dispatchEvent(new Event('change', { bubbles: true }));
          close();
        };
      })(d);
      grid.appendChild(b);
    }
    panel.appendChild(grid);
    var foot = document.createElement('div'); foot.className = 'jdp-foot';
    var t = document.createElement('button'); t.type = 'button'; t.textContent = 'امروز';
    t.onclick = function () { var n = today(); view = { jy: n.jy, jm: n.jm }; current.value = n.jy + '/' + pad(n.jm) + '/' + pad(n.jd); current.dispatchEvent(new Event('change', { bubbles: true })); close(); };
    foot.appendChild(t);
    panel.appendChild(foot);
  }

  function open(input) {
    close();
    current = input;
    var v = parse(input.value) || today();
    view = { jy: v.jy, jm: v.jm };
    panel = document.createElement('div'); panel.className = 'jdp-panel';
    document.body.appendChild(panel);
    var r = input.getBoundingClientRect();
    panel.style.top = (window.scrollY + r.bottom + 4) + 'px';
    panel.style.left = Math.max(8, window.scrollX + r.right - 252) + 'px';
    render();
  }

  var css = document.createElement('style');
  css.textContent =
    '.jdp-panel{position:absolute;z-index:9999;width:252px;background:#fff;color:#1f2937;border:1px solid #d1d5db;border-radius:12px;' +
    'box-shadow:0 10px 30px rgba(0,0,0,.25);padding:8px;font-family:Vazirmatn,Tahoma,sans-serif;direction:rtl;font-size:13px}' +
    '.jdp-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-weight:700}' +
    '.jdp-head button,.jdp-foot button{border:0;background:#eef2ff;color:#4338ca;border-radius:8px;padding:2px 10px;cursor:pointer;font-family:inherit;font-size:14px}' +
    '.jdp-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;text-align:center}' +
    '.jdp-w{color:#6b7280;font-size:11px;padding:2px 0}' +
    '.jdp-grid button{border:0;background:transparent;border-radius:6px;padding:4px 0;cursor:pointer;font-family:inherit;color:#111827}' +
    '.jdp-grid button:hover{background:#e0e7ff}.jdp-grid .jdp-today{outline:1px solid #6366f1}.jdp-grid .jdp-sel{background:#4f46e5;color:#fff}' +
    '.jdp-foot{margin-top:6px;text-align:center}';
  document.head.appendChild(css);

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t && t.matches && t.matches('input[data-jdp]')) { open(t); return; }
    if (panel && !panel.contains(t)) { close(); }
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); } });
  window.jdpParse = parse;
})();
