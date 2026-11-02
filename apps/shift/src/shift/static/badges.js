/* The red number on a home tile: how many open jobs wait for this person (GET /api/v1/badges).
 *
 * The server counts, this file only paints. A failed read keeps the numbers already on screen, so a
 * phone that drops off the Wi-Fi never shows a false «nothing to do». */
(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var CAP = 99;
  var summary = document.getElementById("badge-summary");
  var badges = document.querySelectorAll("[data-badge]");
  if (!badges.length) return;

  function persian(number) {
    return number.toLocaleString("fa-IR");
  }

  function paint(counts) {
    var total = 0;
    Array.prototype.forEach.call(badges, function (badge) {
      var count = counts[badge.getAttribute("data-badge")] || 0;
      total += count;
      badge.classList.toggle("hidden", count === 0);
      if (count === 0) {
        badge.textContent = "";
        badge.removeAttribute("aria-label");
        return;
      }
      badge.textContent = count > CAP ? persian(CAP) + "+" : persian(count);
      badge.setAttribute("aria-label", persian(count) + " کار باز");
    });
    if (!summary) return;
    summary.classList.toggle("hidden", total === 0);
    summary.textContent = total === 0 ? "" : "الان " + persian(total) + " کار باز دارید";
  }

  function refresh() {
    if (document.hidden) return Promise.resolve();
    return fetch("/api/v1/badges", { credentials: "same-origin", cache: "no-store" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (body) {
        if (body && body.badges) paint(body.badges);
      })
      .catch(function () {
        /* offline: keep what is shown */
      });
  }

  refresh();
  window.setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", refresh);
  window.addEventListener("online", refresh);
})();
