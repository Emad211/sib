/**
 * Sib Shift - Main Client Application Logic
 * SW registration, network status monitoring, stale badge, and Outbox UI sync.
 * Writes never claim success: a sent state is only shown after the server
 * response was validated and the outbox emitted `outbox:sent`.
 */
(function () {
  "use strict";

  var LAST_SYNC_KEY = "shift:last-online-at";

  // Service Worker Registration
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then((reg) => {
          // pwa.js owns the Persian update banner; hand it the live registration.
          window.dispatchEvent(new CustomEvent("shift:sw-registered", { detail: reg }));
          // Listen for updates
          reg.addEventListener("updatefound", () => {
            const newWorker = reg.installing;
            if (newWorker) {
              newWorker.addEventListener("statechange", () => {
                if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
                  console.info("Shift PWA update available.");
                }
              });
            }
          });
        })
        .catch((err) => console.warn("SW registration failed:", err));
    });

    if (navigator.serviceWorker && typeof navigator.serviceWorker.addEventListener === "function") {
      navigator.serviceWorker.addEventListener("message", (event) => {
        if (event.data && event.data.type === "SYNC_REPLAY_OUTBOX") {
          if (window.ShiftOutbox && window.ShiftOutbox.replayOutbox) {
            window.ShiftOutbox.replayOutbox();
          }
        }
      });
    }
  }

  function rememberOnlineSync() {
    if (!navigator.onLine) return;
    try {
      window.localStorage.setItem(LAST_SYNC_KEY, new Date().toISOString());
    } catch (err) {
      // Private mode or disabled storage: the badge simply stays generic.
    }
  }

  function requestPersistentStorage() {
    // iOS can evict IndexedDB after days of non-use; ask the browser to keep it.
    if (navigator.storage && typeof navigator.storage.persist === "function") {
      navigator.storage.persist().catch(() => {});
    }
  }

  // Network Status Monitoring & Banners
  // Wi-Fi being up says nothing about the clinic server, so the badge asks the server itself.
  let serverReachable = true;
  async function probeServer() {
    let reachable = navigator.onLine;
    if (reachable) {
      try {
        await fetch("/api/v1/auth/me", { credentials: "same-origin", cache: "no-store" });
      } catch {
        reachable = false;
      }
    }
    if (reachable !== serverReachable) {
      serverReachable = reachable;
      updateNetworkStatus(reachable ? { type: "online" } : undefined);
    }
  }

  function updateNetworkStatus(event) {
    const isOnline = navigator.onLine && serverReachable;
    const banner = document.getElementById("network-banner");
    const statusBadge = document.getElementById("connection-status-badge");

    if (banner) {
      if (!isOnline) {
        banner.textContent = "ارتباط با سرور قطع است — ذخیره در صف آفلاین فعال شد";
        banner.className = "network-banner offline";
      } else {
        rememberOnlineSync();
        // Only a real reconnect deserves the banner, not every page load.
        if (event && event.type === "online") {
          banner.textContent = "اتصال برقرار شد — در حال همگام‌سازی...";
          banner.className = "network-banner online";
          setTimeout(() => {
            if (navigator.onLine && banner) {
              banner.className = "network-banner hidden";
            }
          }, 3000);
        } else {
          banner.className = "network-banner hidden";
        }
      }
    }

    if (statusBadge) {
      if (isOnline) {
        statusBadge.textContent = "آنلاین";
        statusBadge.className = "badge badge-online";
      } else {
        statusBadge.textContent = "آفلاین";
        statusBadge.className = "badge badge-offline";
      }
    }

    checkStaleCache();

    // Trigger outbox replay when connection returns
    if (isOnline && window.ShiftOutbox && window.ShiftOutbox.replayOutbox) {
      window.ShiftOutbox.replayOutbox();
    }
  }

  window.addEventListener("online", updateNetworkStatus);
  window.addEventListener("offline", updateNetworkStatus);

  // Stale-as-of badge uses the recorded last successful online load, not the clock.
  function checkStaleCache() {
    const staleBadge = document.getElementById("stale-badge");
    if (!staleBadge) return;

    if (navigator.onLine) {
      staleBadge.classList.add("hidden");
      return;
    }

    let text = "داده‌های آفلاین";
    try {
      const lastSync = window.localStorage.getItem(LAST_SYNC_KEY);
      if (lastSync) {
        const stamp = new Date(lastSync);
        const clock = stamp.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
        text += ` — آخرین همگام‌سازی ساعت ${clock}`;
      }
    } catch (err) {
      // Storage unavailable; keep the generic offline label.
    }
    staleBadge.textContent = text;
    staleBadge.classList.remove("hidden");
  }

  // Persian names of the event types the server accepts offline; a raw type id is never shown to staff.
  const outboxLabels = (() => {
    try {
      return JSON.parse(document.getElementById("outbox-labels").textContent) || {};
    } catch (error) {
      return {};
    }
  })();

  // Outbox UI Binding
  async function refreshOutboxUi() {
    if (!window.ShiftOutbox || !window.ShiftOutbox.getQueuedItems) return;

    const items = await window.ShiftOutbox.getQueuedItems();
    const countBadge = document.getElementById("outbox-count-badge");
    const listEl = document.getElementById("outbox-list");
    const container = document.getElementById("outbox-card");
    const queuedCount = items.filter((item) => item.status !== "failed").length;
    const failedCount = items.filter((item) => item.status === "failed").length;

    if (countBadge) {
      if (items.length > 0) {
        let label = `${queuedCount} در صف ارسال`;
        if (failedCount > 0) {
          label += ` · ${failedCount} ناموفق`;
        }
        countBadge.textContent = label;
        countBadge.classList.remove("hidden");
      } else {
        countBadge.classList.add("hidden");
      }
    }

    if (listEl) {
      listEl.innerHTML = "";
      if (items.length === 0) {
        if (container) container.classList.add("hidden");
        return;
      }
      if (container) container.classList.remove("hidden");

      items.forEach((item) => {
        const li = document.createElement("li");
        li.className = "outbox-item";

        const info = document.createElement("div");
        info.className = "outbox-item-info";

        const title = document.createElement("span");
        title.className = "outbox-item-type";
        title.textContent = (item.body && outboxLabels[item.body.type]) || "رویداد ثبت‌شده";

        const time = document.createElement("span");
        time.className = "outbox-item-time";
        const d = new Date(item.created_at);
        time.textContent = `${d.toLocaleTimeString("fa-IR")} (${item.tries} تلاش)`;

        info.appendChild(title);
        info.appendChild(time);

        if (item.status === "failed") {
          // F6: a refused record must say why, in the server's own words, and offer a way out.
          const reason = document.createElement("span");
          reason.className = "outbox-item-reason";
          reason.textContent = item.last_error || "سرور این رویداد را نپذیرفت.";
          info.appendChild(reason);
        }

        li.appendChild(info);

        const actions = document.createElement("div");
        if (item.status === "failed") {
          const badge = document.createElement("span");
          badge.className = "badge badge-failed";
          badge.textContent = "ارسال نشد";

          const retryBtn = document.createElement("button");
          retryBtn.className = "btn btn-secondary btn-compact";
          retryBtn.textContent = "تلاش مجدد";
          retryBtn.onclick = () => window.ShiftOutbox.retryFailedItem(item.idempotency_key);

          const dismissBtn = document.createElement("button");
          dismissBtn.className = "btn btn-secondary btn-compact";
          dismissBtn.textContent = "حذف";
          dismissBtn.onclick = () => {
            const sure = window.confirm("این رویداد از صف گوشی حذف شود؟ دیگر فرستاده نمی‌شود.");
            if (sure) window.ShiftOutbox.dismissOutboxItem(item.idempotency_key);
          };

          actions.appendChild(badge);
          actions.appendChild(retryBtn);
          actions.appendChild(dismissBtn);
        } else {
          const badge = document.createElement("span");
          badge.className = "badge badge-queued";
          badge.textContent = "در صف ارسال";
          actions.appendChild(badge);
        }

        li.appendChild(actions);
        listEl.appendChild(li);
      });
    }
  }

  window.addEventListener("outbox:change", refreshOutboxUi);
  window.addEventListener("outbox:sent", refreshOutboxUi);

  function setFeedback(text, badgeClass) {
    const feedback = document.getElementById("submission-feedback");
    if (!feedback) return;
    feedback.textContent = text;
    feedback.className = `badge ${badgeClass}`;
    feedback.classList.remove("hidden");
  }

  // Client Event Dispatch Helper
  // Writes NEVER claim success before an `outbox:sent` event confirms the server.
  window.submitShiftEvent = async function (eventData) {
    try {
      if (!window.ShiftOutbox) {
        throw new Error("صف ارسال در دسترس نیست.");
      }
      if (!eventData.client_event_id && !eventData.event_id) {
        eventData.client_event_id = window.ShiftOutbox.mintUlid();
      }
      if (!eventData.idempotency_key) {
        eventData.idempotency_key = eventData.client_event_id || eventData.event_id;
      }
      if (!eventData.client_at) {
        eventData.client_at = new Date().toISOString();
      }

      const key = eventData.idempotency_key;
      let sent = false;
      const onSent = (event) => {
        const detail = event.detail || {};
        if (detail.idempotency_key !== key) return;
        sent = true;
        setFeedback("رویداد با سرور همگام شد.", "badge-online");
        window.removeEventListener("outbox:sent", onSent);
      };
      window.addEventListener("outbox:sent", onSent);

      try {
        await window.ShiftOutbox.enqueueOutboxItem(eventData);
      } catch (err) {
        window.removeEventListener("outbox:sent", onSent);
        throw err;
      }

      if (!sent) {
        setFeedback("رویداد در صف ارسال قرار گرفت.", "badge-queued");
      }
    } catch (err) {
      setFeedback("خطا در ثبت رویداد: " + err.message, "badge-failed");
    }
  };

  const ROLE_NAMES = {
    reception: "پذیرش",
    nurse: "پرستار",
    storekeeper: "انباردار",
    purchaser: "مسئول خرید",
    accountant: "حسابدار",
    shift_doctor: "پزشک شیفت",
    manager: "مدیر",
  };

  // Who is signed in and how to leave: shown on every page, and simply absent offline or logged out.
  // A signed-out phone keeps the app shell but none of the pages the last person saw.
  async function forgetCachedPages() {
    if (!("caches" in window)) return;
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        const path = new URL(request.url).pathname;
        if (!path.startsWith("/static/") && path !== "/offline.html" && path !== "/manifest.webmanifest") {
          await cache.delete(request);
        }
      }
    }
  }

  async function showWhoAmI() {
    const who = document.getElementById("who-am-i");
    const logout = document.getElementById("logout-btn");
    if (!who || !logout) return;
    let me;
    try {
      const response = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
      if (!response.ok) return;
      me = await response.json();
    } catch {
      return;
    }
    // The session cookie is SameSite=Strict, so opening the installed app from the launcher sends the
    // first request without it and the server answers with the guest page. Same-origin reload sends it.
    if (document.getElementById("shift-overview-title") && !sessionStorage.getItem("shift:guest-reloaded")) {
      sessionStorage.setItem("shift:guest-reloaded", "1");
      location.reload();
      return;
    }
    const roles = (me.roles || []).map((role) => ROLE_NAMES[role] || role).join("، ");
    who.textContent = `${me.staff.name} · ${roles}`;
    who.classList.remove("hidden");
    logout.classList.remove("hidden");
    logout.addEventListener("click", async () => {
      await fetch("/api/v1/auth/logout", { method: "POST", headers: { "X-CSRFToken": me.csrf_token } });
      await forgetCachedPages();
      location.assign("/login");
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    showWhoAmI();
    requestPersistentStorage();
    updateNetworkStatus();
    refreshOutboxUi();
    probeServer();
    setInterval(probeServer, 20000);
  });
})();
