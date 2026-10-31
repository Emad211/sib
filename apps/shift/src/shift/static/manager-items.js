/**
 * Sib Shift — the clinic's items and their accounting codes (SHF-7.1, Q-17).
 *
 * One row, one save: the manager types the accounting code (and the minimum) of the item in front of
 * them; the row goes through the existing versioned catalog API, so the change applies from the next
 * shift and never in the middle of one (R-09). A saved row reloads the page, which re-sorts the list
 * and moves the item out of the missing-code group.
 */
(function () {
  "use strict";

  function reason(payload) {
    var message = payload && payload.error && payload.error.message;
    return message ? "ذخیره نشد: " + message : "ذخیره نشد؛ دوباره تلاش کنید.";
  }

  function setStatus(form, text, kind) {
    var status = form.querySelector(".mg-status");
    if (!status) return;
    status.textContent = text;
    status.className = "mg-status" + (kind ? " is-" + kind : "");
  }

  function value(form, name) {
    var field = form.querySelector("[data-field='" + name + "']");
    return field ? field.value.trim() : "";
  }

  document.querySelectorAll("form[data-item]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var button = form.querySelector("button[type=submit]");
      var minimum = value(form, "minimum");
      var code = value(form, "acc_code");
      var body = {
        name: form.dataset.name,
        kind: form.dataset.kind,
        location_id: form.dataset.location,
        unit: form.dataset.unit,
        minimum: minimum === "" ? 0 : Number(minimum),
        acc_code: code === "" ? null : code,
        stock_factor: Number(form.dataset.stockFactor || 1),
        retired: false,
      };
      button.disabled = true;
      setStatus(form, "در حال ذخیره…", "");
      fetch("/api/v1/catalog/items/" + encodeURIComponent(form.dataset.item), {
        method: "PUT",
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
