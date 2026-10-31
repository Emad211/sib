/**
 * Sib Shift — the campaign calendar page (SHF-3, S01 §3 «ت»).
 *
 * One job: send the campaign through the manager API and reload, so the list the manager sees is the
 * list the phone will offer. `csrf.js` patches `fetch`, so the PUT carries the double-submit token in
 * `X-CSRFToken`; a refused range or amount keeps the form filled and shows the reason in Persian.
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

  function field(form, id) {
    return form.querySelector("#" + id);
  }

  var form = document.querySelector("form[data-campaign-form]");
  if (!form) return;

  // "ویرایش" fills the one form instead of opening a second screen: a manager fixes a typo in place.
  document.querySelectorAll("button[data-campaign-edit]").forEach(function (button) {
    button.addEventListener("click", function () {
      var row = button.dataset;
      field(form, "campaign-id").value = row.campaignEdit;
      field(form, "campaign-name").value = row.campaignName;
      field(form, "campaign-service").value = row.campaignService;
      field(form, "campaign-amount").value = row.campaignAmount;
      window.ShiftJalaliDatepicker.setIso(field(form, "campaign-starts"), row.campaignStarts);
      window.ShiftJalaliDatepicker.setIso(field(form, "campaign-ends"), row.campaignEnds);
      setStatus(form, "", "");
      form.scrollIntoView({ block: "center" });
    });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var campaignId = field(form, "campaign-id").value.trim();
    var button = form.querySelector("button[type=submit]");
    if (!campaignId || !button) return;
    button.disabled = true;
    setStatus(form, "", "");
    fetch("/api/v1/campaigns/" + encodeURIComponent(campaignId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: field(form, "campaign-name").value,
        service: field(form, "campaign-service").value,
        amount_toman: Number(field(form, "campaign-amount").value),
        starts_on: window.ShiftJalaliDatepicker.getIso(field(form, "campaign-starts")),
        ends_on: window.ShiftJalaliDatepicker.getIso(field(form, "campaign-ends")),
      }),
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
})();
