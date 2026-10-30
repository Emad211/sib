/**
 * Development quick-event form (home.html renders it only when
 * SHIFT_ENV=development). Identity is never collected here: the server derives
 * actor/device/shift from the session principal.
 */
(function () {
  "use strict";

  function showError(message) {
    const feedback = document.getElementById("submission-feedback");
    if (!feedback) return;
    feedback.textContent = message;
    feedback.className = "badge badge-failed";
    feedback.classList.remove("hidden");
  }

  document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("quick-event-form");
    if (!form) return;

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!window.ShiftOutbox) {
        showError("صف ارسال در دسترس نیست.");
        return;
      }

      const typeField = document.getElementById("event-type");
      const itemField = document.getElementById("event-item");
      const locationField = document.getElementById("event-location");
      const quantityField = document.getElementById("event-quantity");
      const itemId = itemField ? itemField.value.trim() : "";
      const locationId = locationField ? locationField.value.trim() : "";
      const quantity = quantityField ? parseInt(quantityField.value, 10) : NaN;

      if (!itemId || !locationId || !Number.isInteger(quantity) || quantity < 0) {
        showError("شناسه کالا، قفسه و مقدار معتبر لازم است.");
        return;
      }

      const eventData = {
        client_event_id: window.ShiftOutbox.mintUlid(),
        type: typeField.value,
        payload: { item_id: itemId, location_id: locationId, quantity: quantity },
        client_at: new Date().toISOString(),
        work_date: window.ShiftOutbox.tehranBusinessDate(),
      };
      eventData.idempotency_key = eventData.client_event_id;
      await window.submitShiftEvent(eventData);
    });
  });
})();
