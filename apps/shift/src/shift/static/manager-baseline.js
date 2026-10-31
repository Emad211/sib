/**
 * Sib Shift — the manager's opening count (SHF-7.1).
 *
 * One flow, one form: the box (reception only), its shelves and who holds them now. The count goes
 * through `HandoverService.record_baseline` behind the same API the CLI uses, so the CLI stays as the
 * operator's fallback and the event is written once. A saved baseline reloads the page, which then
 * shows the recorded note instead of the form.
 */
(function () {
  "use strict";

  function reason(payload) {
    var message = payload && payload.error && payload.error.message;
    return message ? "ثبت نشد: " + message : "ثبت نشد؛ دوباره تلاش کنید.";
  }

  function setStatus(form, text, kind) {
    var status = form.querySelector(".mg-status");
    if (!status) return;
    status.textContent = text;
    status.className = "mg-status" + (kind ? " is-" + kind : "");
  }

  document.querySelectorAll("form[data-baseline-flow]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var button = form.querySelector("button[type=submit]");
      var custodian = form.querySelector("select[name=custodian_id]");
      var cash = form.querySelector("input[name=opening_cash]");
      var body = {
        flow: form.dataset.baselineFlow,
        custodian_id: custodian ? custodian.value : "",
        opening_cash: cash && cash.value !== "" ? Number(cash.value) : null,
        items: Array.prototype.map
          .call(form.querySelectorAll(".bl-count"), function (field) {
            return {
              item_id: field.dataset.item,
              location_id: field.dataset.location,
              quantity: field.value === "" ? 0 : Number(field.value),
            };
          })
          .filter(function (entry) {
            return entry.quantity >= 0;
          }),
      };
      if (!body.custodian_id) {
        setStatus(form, "نگه‌دارنده را انتخاب کنید.", "error");
        return;
      }
      button.disabled = true;
      setStatus(form, "در حال ثبت…", "");
      fetch("/api/v1/manager/baseline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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
