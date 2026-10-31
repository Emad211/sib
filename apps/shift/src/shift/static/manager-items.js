/**
 * Sib Shift — the clinic's items and checklists, edited without a programmer (A4, SHF-7.1, Q-17).
 *
 * One row, one save: the manager changes the name, kind, place, responsible role, frequency, unit,
 * minimum, accounting code or shelf position of an item, or adds a new line, and the row goes through
 * the existing versioned catalog API, so the change applies from the next shift and never in the
 * middle of one (R-09). Every refusal is the service's own Persian sentence; the page never shows a
 * raw id or English text.
 */
(function () {
  "use strict";

  var SLUG = /^[a-z][a-z0-9_-]{0,47}$/;

  function reason(payload) {
    var message = payload && payload.error && payload.error.message;
    return message ? message : "ذخیره نشد؛ دوباره تلاش کنید.";
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

  /** The request body the catalog API takes; `retired` is the state the row is being set to. */
  function body(form, retired) {
    var minimum = value(form, "minimum");
    var code = value(form, "acc_code");
    var sort = value(form, "sort_order");
    var data = {
      name: value(form, "name"),
      kind: value(form, "kind"),
      location_id: value(form, "location_id"),
      unit: value(form, "unit"),
      minimum: minimum === "" ? 0 : Number(minimum),
      acc_code: code === "" ? null : code,
      stock_factor: Number(form.dataset.stockFactor || 1),
      retired: retired,
      responsible_role: value(form, "responsible_role"),
      frequency: value(form, "frequency"),
    };
    if (sort !== "") data.sort_order = Number(sort);
    return data;
  }

  function save(form, itemId, retired) {
    var button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    setStatus(form, "در حال ذخیره…", "");
    fetch("/api/v1/catalog/items/" + encodeURIComponent(itemId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body(form, retired)),
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
        if (button) button.disabled = false;
      });
  }

  document.querySelectorAll("form[data-item]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      save(form, form.dataset.itemId, form.dataset.retired === "true");
    });
    var retire = form.querySelector("[data-retire]");
    if (retire) {
      retire.addEventListener("click", function () {
        var sure = window.confirm("این قلم از شیفت بعد از فهرست‌ها برداشته می‌شود. مطمئنید؟");
        if (sure) save(form, form.dataset.itemId, true);
      });
    }
  });

  var fresh = document.querySelector("form[data-new-item]");
  if (fresh) {
    fresh.addEventListener("submit", function (event) {
      event.preventDefault();
      var itemId = value(fresh, "item_id");
      if (!SLUG.test(itemId)) {
        setStatus(fresh, "شناسه باید با یک حرف لاتین کوچک شروع شود و فقط حرف، رقم، خط تیره یا زیرخط داشته باشد.", "error");
        return;
      }
      save(fresh, itemId, false);
    });
  }
})();
