/* Report date filters (FB-24): replaces the day / month / year dropdowns of every report page with one
 * Jalali date picker per date. The old selects stay in the page (hidden) so each page's own code that
 * builds the 'from' / 'to' query values keeps working; this script only keeps them in step with the picker.
 */
(function () {
  'use strict';
  var SCHEMES = [
    function (p) { return [p + '-year', p + '-month', p + '-day']; },
    function (p) { return [p + 'Y', p + 'M', p + 'D']; }
  ];
  var LABELS = { from: 'از تاریخ', to: 'تا تاریخ' };
  var pickers = [];

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function fromSelects(els) {
    var y = parseInt(els[0].value, 10), m = parseInt(els[1].value, 10), d = parseInt(els[2].value, 10);
    return (y && m && d) ? y + '/' + pad(m) + '/' + pad(d) : '';
  }

  function toSelects(els, text) {
    var v = window.jdpParse ? window.jdpParse(text) : null;
    if (!v) { return false; }
    [v.jy, v.jm, v.jd].forEach(function (val, i) {
      var el = els[i];
      if (el.tagName === 'SELECT' && !Array.prototype.some.call(el.options, function (o) { return +o.value === val; })) {
        var o = document.createElement('option'); o.value = val; o.textContent = val; el.appendChild(o);
      }
      el.value = val;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
    return true;
  }

  function build(prefix, els) {
    var input = document.createElement('input');
    input.type = 'text'; input.setAttribute('data-jdp', ''); input.autocomplete = 'off'; input.dir = 'ltr';
    input.placeholder = '1405/07/01'; input.className = 'report-date-input';
    input.style.cssText = 'width:120px;padding:.45rem .6rem;border-radius:8px;border:1px solid rgba(139,92,246,.3);' +
      'background:rgba(30,41,59,.8);color:#e5e7eb;font-family:inherit;font-size:.9rem;text-align:center;cursor:pointer';
    var groups = els.map(function (e) { return e.closest('.filter-group'); });
    if (groups[0]) {
      var wrap = document.createElement('div'); wrap.className = 'filter-group';
      var label = document.createElement('label'); label.textContent = LABELS[prefix];
      wrap.appendChild(label); wrap.appendChild(input);
      groups[0].parentNode.insertBefore(wrap, groups[0]);
      groups.forEach(function (g) { if (g) { g.style.display = 'none'; } });
    } else {
      var box = els[0].parentNode;
      Array.prototype.forEach.call(box.children, function (c) { c.style.display = 'none'; });
      box.appendChild(input);
    }
    input.value = fromSelects(els);
    input.addEventListener('change', function () { if (!toSelects(els, input.value)) { input.value = fromSelects(els); } });
    els.forEach(function (e) { e.addEventListener('change', function () { input.value = fromSelects(els); }); });
    pickers.push({ input: input, els: els });
  }

  function refresh() { pickers.forEach(function (p) { var v = fromSelects(p.els); if (v) { p.input.value = v; } }); }

  function init() {
    ['from', 'to'].forEach(function (p) {
      for (var i = 0; i < SCHEMES.length; i++) {
        var els = SCHEMES[i](p).map(function (id) { return document.getElementById(id); });
        if (els.every(Boolean)) { build(p, els); break; }
      }
    });
    if (!pickers.length) { return; }
    // quick-range chips set the old selects directly: show the result in the pickers afterwards
    document.addEventListener('click', function () { setTimeout(refresh, 0); });
    // a date typed by hand and submitted with Enter: push it into the old fields first
    document.addEventListener('submit', function () {
      pickers.forEach(function (p) { toSelects(p.els, p.input.value); });
      ['syncHiddenDates', 'updateHiddenDates'].forEach(function (n) { if (typeof window[n] === 'function') { window[n](); } });
    }, true);
  }

  window.addEventListener('load', init);
})();
