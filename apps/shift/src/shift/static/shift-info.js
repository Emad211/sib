/* The running shift and who is on it (GET /api/v1/shift/now).
 *
 * The server decides; this file only paints: a small «صبح · ۰۷:۳۰–۱۳:۳۰» under the logo on every page,
 * and on the home screen a card with the people on shift and whether you are one of them. A failed read
 * keeps what is already shown, so a phone that drops off the Wi-Fi never claims the shift changed. */
(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var brandShift = document.getElementById("brand-shift");
  var strip = document.getElementById("shift-strip");
  if (!brandShift && !strip) return;

  function fa(text) {
    return String(text).replace(/\d/g, function (digit) {
      return "۰۱۲۳۴۵۶۷۸۹"[Number(digit)];
    });
  }

  function chip(person) {
    var item = document.createElement("li");
    item.className = "shift-person" + (person.me ? " shift-person-me" : "");
    var initial = document.createElement("span");
    initial.className = "shift-initial";
    initial.setAttribute("aria-hidden", "true");
    initial.textContent = Array.from(person.name.trim())[0] || "؟";
    var text = document.createElement("span");
    text.className = "shift-person-text";
    var name = document.createElement("strong");
    name.textContent = person.me ? person.name + " (شما)" : person.name;
    var role = document.createElement("span");
    role.className = "shift-person-role";
    role.textContent = person.roles.join("، ");
    text.appendChild(name);
    text.appendChild(role);
    item.appendChild(initial);
    item.appendChild(text);
    return item;
  }

  function paint(card) {
    var shift = card.shift;
    var hours = fa(shift.from) + "–" + fa(shift.to);
    if (brandShift) brandShift.textContent = "شیفت " + shift.name + " · " + hours;
    if (!strip) return;
    document.getElementById("shift-name").textContent = "شیفت " + shift.name;
    document.getElementById("shift-when").textContent = fa(shift.date) + " · " + hours;
    var list = document.getElementById("shift-people");
    list.replaceChildren();
    card.people.forEach(function (person) {
      list.appendChild(chip(person));
    });
    var empty = document.getElementById("shift-empty");
    if (empty) empty.classList.toggle("hidden", card.people.length > 0);
    var me = document.getElementById("shift-me");
    if (me) {
      var on = card.me.on_shift;
      me.classList.toggle("hidden", on === null);
      me.classList.toggle("shift-me-on", on === true);
      me.textContent = on === true ? "شما در شیفت هستید" : on === false ? "شما هنوز در شیفت نیستید" : "";
    }
    strip.classList.remove("hidden");
  }

  function refresh() {
    if (document.hidden) return Promise.resolve();
    return fetch("/api/v1/shift/now", { credentials: "same-origin", cache: "no-store" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (card) {
        if (card && card.shift) paint(card);
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
