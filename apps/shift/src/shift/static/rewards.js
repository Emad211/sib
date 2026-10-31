/**
 * Sib Shift — the manager's reward screen (SHF-5).
 *
 * One job per control. A rate, an accrual, a revocation and a staff mapping are plain form posts:
 * the form is sent with `fetch`, `csrf.js` adds the double-submit token in `X-CSRFToken`, and the page
 * reloads only after the server accepted the change. An approve / reject button is not a second
 * approval endpoint: it posts SHF-3's own `/api/v1/events` with `event.approved` or `event.rejected`
 * and the reward's `ref_event_id`, exactly like the phone does.
 */
(function () {
  "use strict";

  var ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  var HEADER = "X-CSRFToken";
  var COOKIE = "shift_csrf";
  var APPROVED = "event.approved";
  var REJECTED = "event.rejected";

  function ulid() {
    // A time-ordered 26-character id the events API accepts; the first ten characters are the millis.
    var value = Date.now();
    var time = "";
    for (var index = 0; index < 10; index += 1) {
      time = ALPHABET[value % 32] + time;
      value = Math.floor(value / 32);
    }
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    var random = "";
    for (var byte = 0; byte < bytes.length; byte += 1) {
      random += ALPHABET[bytes[byte] & 31];
    }
    return time + random;
  }

  function csrfToken() {
    var match = document.cookie.match(new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]+)"));
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute("content")) return meta.getAttribute("content");
    return match ? decodeURIComponent(match[1]) : "";
  }

  function reason(payload, status) {
    var message = payload && payload.error && payload.error.message;
    if (message) return message;
    return status === 403
      ? "اجازهٔ این کار را ندارید یا صفحه منقضی شده است؛ صفحه را دوباره باز کنید."
      : "ثبت انجام نشد؛ دوباره تلاش کنید.";
  }

  function asJson(response) {
    return response
      .json()
      .catch(function () {
        return {};
      })
      .then(function (payload) {
        if (!response.ok) throw new Error(reason(payload, response.status));
        return payload;
      });
  }

  function status(form, text, kind) {
    var line = form.querySelector(".mg-status");
    if (!line) return;
    line.textContent = text;
    line.className = "mg-status" + (kind ? " is-" + kind : "");
  }

  document.querySelectorAll("form[data-rewards-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var button = form.querySelector("button[type=submit]");
      if (button) button.disabled = true;
      status(form, "", "");
      fetch(form.action, { method: "POST", body: new FormData(form) })
        .then(asJson)
        .then(function () {
          window.location.reload();
        })
        .catch(function (error) {
          status(form, error.message, "error");
          if (button) button.disabled = false;
        });
    });
  });

  function decide(target, type) {
    var id = ulid();
    return fetch("/api/v1/events", {
      method: "POST",
      headers: { "Content-Type": "application/json", [HEADER]: csrfToken() },
      body: JSON.stringify({
        type: type,
        payload: {},
        client_at: new Date().toISOString(),
        client_event_id: id,
        idempotency_key: id,
        ref_event_id: target
      })
    }).then(asJson);
  }

  document.querySelectorAll("[data-reward-approve], [data-reward-reject]").forEach(function (button) {
    button.addEventListener("click", function () {
      var approve = button.hasAttribute("data-reward-approve");
      var target = button.getAttribute(approve ? "data-reward-approve" : "data-reward-reject");
      if (!target) return;
      if (!approve && !window.confirm("این پاداش رد شود؟ ردشدن برای همیشه است و پرداخت نمی‌شود.")) return;
      button.disabled = true;
      decide(target, approve ? APPROVED : REJECTED)
        .then(function () {
          window.location.reload();
        })
        .catch(function (error) {
          button.disabled = false;
          window.alert(error.message);
        });
    });
  });
})();
