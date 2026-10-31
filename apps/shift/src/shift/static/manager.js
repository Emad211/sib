/**
 * Sib Shift — the manager's settings screen (SHF-6.1).
 *
 * One job per form: send the value through the existing settings API and answer in Persian. `csrf.js`
 * patches `fetch`, so the PUT carries the double-submit token in `X-CSRFToken`; a saved change reloads
 * the page, which then shows the value waiting for the next shift (R-09).
 */
(function () {
  "use strict";

  function reason(payload) {
    var message = payload && payload.error && payload.error.message;
    return message ? "مقدار پذیرفته نشد: " + message : "مقدار پذیرفته نشد؛ دوباره تلاش کنید.";
  }

  function setStatus(form, text, kind) {
    var status = form.querySelector(".mg-status");
    if (!status) return;
    status.textContent = text;
    status.className = "mg-status" + (kind ? " is-" + kind : "");
  }

  document.querySelectorAll("form[data-setting]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var field = form.querySelector(".mg-input");
      var button = form.querySelector("button[type=submit]");
      if (!field || !button) return;
      button.disabled = true;
      setStatus(form, "", "");
      fetch("/api/v1/settings/" + encodeURIComponent(form.dataset.setting), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: field.value }),
      })
        .then(function (response) {
          return response
            .json()
            .catch(function () {
              return {};
            })
            .then(function (payload) {
              if (!response.ok) throw new Error(reason(payload));
              return payload;
            });
        })
        .then(function () {
          window.location.reload();
        })
        .catch(function (error) {
          setStatus(form, error.message, "error");
          button.disabled = false;
        });
    });
  });
  // F5: a held record is released or rejected by the manager; both answers are Persian and the page
  // reloads only after the server confirmed the decision.
  document.querySelectorAll("[data-held-release], [data-held-reject]").forEach(function (button) {
    button.addEventListener("click", function () {
      var release = button.hasAttribute("data-held-release");
      var id = button.getAttribute(release ? "data-held-release" : "data-held-reject");
      if (!id) return;
      if (!release && !window.confirm("این رویداد رد شود؟ دیگر روی صندوق و قفسه اثر نمی‌کند.")) return;
      button.disabled = true;
      fetch("/api/v1/manager/held-events/" + encodeURIComponent(id) + "/" + (release ? "release" : "reject"), {
        method: "POST",
      })
        .then(function (response) {
          if (!response.ok) throw new Error("پاسخ " + response.status);
          window.location.reload();
        })
        .catch(function () {
          button.disabled = false;
          window.alert("ثبت تصمیم انجام نشد؛ دوباره تلاش کنید.");
        });
    });
  });

  // C1: the card's own decision buttons. A record that waits for the manager is approved or rejected
  // through the phone's one decision endpoint, an order through the purchase chain's one action
  // endpoint; the page reloads only after the server confirmed, and a refusal stays in Persian.
  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (payload) {
          if (!response.ok) {
            var message = payload && payload.error && payload.error.message;
            throw new Error(message || "ثبت تصمیم انجام نشد؛ دوباره تلاش کنید.");
          }
          return payload;
        });
    });
  }

  function settle(button, request) {
    button.disabled = true;
    request
      .then(function () {
        window.location.reload();
      })
      .catch(function (error) {
        button.disabled = false;
        window.alert(error.message);
      });
  }

  document.querySelectorAll("button[data-decide]").forEach(function (button) {
    button.addEventListener("click", function () {
      var approve = button.getAttribute("data-decide") === "approve";
      var eventId = button.getAttribute("data-event-id");
      if (!eventId) return;
      var warning = button.getAttribute("data-reject-note");
      if (!approve && !window.confirm(warning || "این مورد رد شود؟")) return;
      var id = window.ShiftOutbox.mintUlid();
      settle(
        button,
        postJson("/api/v1/events", {
          type: approve ? "event.approved" : "event.rejected",
          ref_event_id: eventId,
          payload: {},
          client_at: new Date().toISOString(),
          client_event_id: id,
          idempotency_key: id,
        })
      );
    });
  });

  document.querySelectorAll("button[data-order-decision]").forEach(function (button) {
    button.addEventListener("click", function () {
      var holder = button.closest("[data-order-id]");
      if (!holder) return;
      var decision = button.getAttribute("data-order-decision");
      if (decision === "rejected" && !window.confirm("این سفارش رد شود؟")) return;
      var data = { order_id: holder.getAttribute("data-order-id"), decision: decision };
      var note = holder.querySelector("[data-order-note]");
      if (decision === "changed" && note && note.value.trim()) data.note = note.value.trim();
      settle(button, postJson("/api/v1/purchase/action", { action: "order.decide", data: data }));
    });
  });
})();
