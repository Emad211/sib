/**
 * Offline fallback page: retry reload and return to the shell when online.
 * Kept external so the page runs under the CSP (script-src 'self').
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", () => {
    const retryButton = document.getElementById("retry-btn");
    if (retryButton) {
      retryButton.addEventListener("click", () => window.location.reload());
    }
  });

  window.addEventListener("online", () => {
    window.location.href = "/";
  });
})();
