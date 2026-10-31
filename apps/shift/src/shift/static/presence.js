/**
 * Sib Shift — «حضور در شیفت» on the home screen (ADR-021).
 *
 * The second receptionist and the shift doctor do not take the box over, so their shift opens with
 * this one button instead: it records `shift.presence_declared` through the real API, tells them in
 * Persian that it is done, and reloads so the tiles and the notice come from the new answer.
 * `csrf.js` already adds the CSRF header to every unsafe fetch.
 */
(function () {
  "use strict";

  var DONE = "حضور شما در این شیفت ثبت شد.";
  var FAILED = "ثبت حضور انجام نشد؛ دوباره تلاش کنید.";

  function feedback(text) {
    var box = document.getElementById("presence-feedback");
    if (!box) return;
    box.textContent = text;
    box.classList.remove("hidden");
  }

  function declare(button) {
    button.disabled = true;
    return fetch("/api/v1/shift/presence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: button.getAttribute("data-presence-role") }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json().catch(function () {
          return {};
        });
      })
      .then(function () {
        feedback(DONE);
        window.location.reload();
      })
      .catch(function () {
        button.disabled = false;
        feedback(FAILED);
      });
  }

  document.querySelectorAll("[data-presence-role]").forEach(function (button) {
    button.addEventListener("click", function () {
      declare(button);
    });
  });
})();
