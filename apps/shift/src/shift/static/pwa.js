/**
 * Sib Shift - PWA install and update layer.
 * Additive to app.js, which owns registration: this file only offers the home-screen
 * install (once, never after a dismissal, never inside the installed app) and the
 * "نسخهٔ تازه آماده است" reload. The outbox lives in IndexedDB, so a reload for a new
 * version never drops an unsent record.
 */
(function () {
  "use strict";

  var DISMISS_KEY = "shift:install-dismissed";

  var DIGITS = {
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
  };

  // Persian and Arabic-Indic digits become ASCII before any number reaches the API.
  function normalizeDigits(value) {
    return String(value == null ? "" : value).replace(/[۰-۹٠-٩]/g, function (digit) {
      return DIGITS[digit] || digit;
    });
  }

  window.ShiftPWA = { normalizeDigits: normalizeDigits };

  function isStandalone() {
    if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) {
      return true;
    }
    return navigator.standalone === true;
  }

  function isHomeScreen() {
    return window.location.pathname === "/";
  }

  function isDismissed() {
    try {
      return window.localStorage.getItem(DISMISS_KEY) === "1";
    } catch (error) {
      // Storage blocked: the offer simply stays available for this visit.
      return false;
    }
  }

  function rememberDismissal() {
    try {
      window.localStorage.setItem(DISMISS_KEY, "1");
    } catch (error) {
      // Nothing to remember; the banner stays hidden for this page anyway.
    }
  }

  // ---- Install ----
  var installEvent = null;
  var installBanner = document.getElementById("install-banner");

  // The bars are fixed over the content: while one is up, the page keeps room under its last tile.
  function syncBottomPadding() {
    var showing = document.querySelectorAll(".install-banner:not(.hidden), .update-toast:not(.hidden)");
    document.body.classList.toggle("has-bottom-banner", showing.length > 0);
  }

  function hideInstall() {
    if (installBanner) {
      installBanner.classList.add("hidden");
    }
    syncBottomPadding();
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    installEvent = event;
    if (installBanner && isHomeScreen() && !isStandalone() && !isDismissed()) {
      installBanner.classList.remove("hidden");
      syncBottomPadding();
    }
  });

  var installButton = document.getElementById("install-btn");
  if (installButton) {
    installButton.addEventListener("click", function () {
      if (!installEvent) {
        hideInstall();
        return;
      }
      installEvent.prompt();
      installEvent.userChoice
        .then(function (choice) {
          if (choice && choice.outcome === "accepted") {
            rememberDismissal();
            hideInstall();
          }
        })
        .catch(function () {
          // The prompt was closed; the button stays for another try.
        })
        .then(function () {
          installEvent = null;
        });
    });
  }

  var dismissButton = document.getElementById("install-dismiss");
  if (dismissButton) {
    dismissButton.addEventListener("click", function () {
      rememberDismissal();
      hideInstall();
    });
  }

  window.addEventListener("appinstalled", function () {
    rememberDismissal();
    installEvent = null;
    hideInstall();
  });

  if (isStandalone() || isDismissed() || !isHomeScreen()) {
    hideInstall();
  }

  // ---- Update ----
  var updateToast = document.getElementById("update-toast");
  var waitingWorker = null;
  var pendingReload = false;

  function showUpdate(worker) {
    waitingWorker = worker;
    if (updateToast) {
      updateToast.classList.remove("hidden");
    }
    syncBottomPadding();
  }

  function watchRegistration(registration) {
    if (!registration || typeof registration.addEventListener !== "function") {
      return;
    }
    if (registration.waiting && navigator.serviceWorker.controller) {
      showUpdate(registration.waiting);
    }
    registration.addEventListener("updatefound", function () {
      var worker = registration.installing;
      if (!worker) {
        return;
      }
      worker.addEventListener("statechange", function () {
        if (worker.state === "installed" && navigator.serviceWorker.controller) {
          showUpdate(worker);
        }
      });
    });
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (!pendingReload) {
        return;
      }
      pendingReload = false;
      window.location.reload();
    });
    window.addEventListener("shift:sw-registered", function (event) {
      watchRegistration(event.detail);
    });
    navigator.serviceWorker
      .getRegistration()
      .then(function (registration) {
        if (registration) {
          watchRegistration(registration);
        }
      })
      .catch(function () {});
  }

  var reloadButton = document.getElementById("update-reload");
  if (reloadButton) {
    reloadButton.addEventListener("click", function () {
      if (updateToast) {
        updateToast.classList.add("hidden");
      }
      syncBottomPadding();
      var worker = waitingWorker;
      if (!worker) {
        pendingReload = true;
        window.location.reload();
        return;
      }
      pendingReload = true;
      worker.addEventListener("statechange", function () {
        if (worker.state === "activated" && pendingReload) {
          pendingReload = false;
          window.location.reload();
        }
      });
      worker.postMessage({ type: "SKIP_WAITING" });
    });
  }

  var laterButton = document.getElementById("update-later");
  if (laterButton) {
    laterButton.addEventListener("click", function () {
      if (updateToast) {
        updateToast.classList.add("hidden");
      }
      syncBottomPadding();
    });
  }

  // ---- Numeric fields ----
  // Rewrite Persian/Arabic digits when the field settles (change/blur), never on every
  // keystroke, so the caret never jumps while a number is being typed.
  function normalizeField(target) {
    if (!target || !target.matches || !target.matches("input[inputmode='numeric'], input[inputmode='decimal'], input[type='number'], input[type='tel']")) {
      return;
    }
    var normalized = normalizeDigits(target.value);
    if (normalized !== target.value) {
      target.value = normalized;
      target.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  document.addEventListener("change", function (event) {
    normalizeField(event.target);
  });
})();
