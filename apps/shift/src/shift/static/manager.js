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
})();
