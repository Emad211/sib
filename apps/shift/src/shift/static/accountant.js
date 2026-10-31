/**
 * Sib Shift — the accountant's queue (S01 §3 «ک», «د», «ب»).
 *
 * One job: approve or reject a queue row through the phone's own decision event. The POST goes to the
 * existing `/api/v1/events` endpoint with `event.approved` or `event.rejected` and the row's own
 * `ref_event_id`, so the clinic keeps exactly one decision path and this page adds no rule of its own —
 * a row the server would refuse never carries a button, because the page only draws the decisions
 * `pending_for` already said the signed-in person may take.
 *
 * `csrf.js` adds `X-CSRFToken` on the patched fetch from the shell's meta tag; the token is read the same
 * way here so the call carries it even if the patch is ever removed. The page reloads only after the
 * server confirmed the decision, and a refusal is answered in Persian and leaves the row in place.
 */
(function () {
  "use strict";

  var ENDPOINT = "/api/v1/events";
  var APPROVED = "event.approved";
  var REJECTED = "event.rejected";

  function csrfHeaders() {
    var headers = { "Content-Type": "application/json" };
    var meta = document.querySelector('meta[name="csrf-token"]');
    var token = window.getCsrfToken ? window.getCsrfToken() : meta && meta.getAttribute("content");
    if (token) headers["X-CSRFToken"] = token;
    return headers;
  }

  // The event needs an id of its own so a double click cannot write two decisions. It comes from the
  // shell's own minter (`outbox-idb.js`, loaded before this file): the server accepts nothing but its
  // 26-character ULID (`common.ids.is_ulid`).
  function clientEventId() {
    return window.ShiftOutbox.mintUlid();
  }

  function reason(response, payload) {
    var message = payload && payload.error && payload.error.message;
    if (message) return message;
    return response.status === 403 ? "نقش شما اجازهٔ این تصمیم را ندارد." : "ثبت تصمیم انجام نشد؛ دوباره تلاش کنید.";
  }

  document.querySelectorAll("button[data-decision]").forEach(function (button) {
    button.addEventListener("click", function () {
      var eventId = button.getAttribute("data-event-id");
      var approve = button.getAttribute("data-decision") === "approve";
      if (!eventId) return;
      if (!approve && !window.confirm("این مورد رد شود؟ رد شدن به کارت مدیر می‌رود.")) return;
      button.disabled = true;
      var id = clientEventId();
      fetch(ENDPOINT, {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          type: approve ? APPROVED : REJECTED,
          ref_event_id: eventId,
          payload: {},
          client_at: new Date().toISOString(),
          client_event_id: id,
          idempotency_key: id,
        }),
      })
        .then(function (response) {
          return response
            .json()
            .catch(function () {
              return {};
            })
            .then(function (payload) {
              if (!response.ok) throw new Error(reason(response, payload));
              return payload;
            });
        })
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
