/**
 * Sib Shift - Main Client Application Logic
 * SW registration, network status monitoring, stale badge, and Outbox UI sync.
 */
(function () {
  "use strict";

  // Service Worker Registration
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then((reg) => {
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

    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data && event.data.type === "SYNC_REPLAY_OUTBOX") {
        if (window.ShiftOutbox && window.ShiftOutbox.replayOutbox) {
          window.ShiftOutbox.replayOutbox();
        }
      }
    });
  }

  // Network Status Monitoring & Banners
  function updateNetworkStatus() {
    const isOnline = navigator.onLine;
    const banner = document.getElementById("network-banner");
    const statusBadge = document.getElementById("connection-status-badge");

    if (banner) {
      if (!isOnline) {
        banner.textContent = "ارتباط با سرور قطع است — ذخیره در صف آفلاین فعال شد";
        banner.className = "network-banner offline";
      } else {
        banner.textContent = "اتصال برقرار شد — در حال همگام‌سازی...";
        banner.className = "network-banner online";
        setTimeout(() => {
          if (navigator.onLine && banner) {
            banner.className = "network-banner hidden";
          }
        }, 3000);
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

    // Trigger outbox replay when connection returns
    if (isOnline && window.ShiftOutbox && window.ShiftOutbox.replayOutbox) {
      window.ShiftOutbox.replayOutbox();
    }
  }

  window.addEventListener("online", updateNetworkStatus);
  window.addEventListener("offline", updateNetworkStatus);

  // Stale-as-of badge detection
  function checkStaleCache() {
    const staleBadge = document.getElementById("stale-badge");
    if (!staleBadge) return;

    if (!navigator.onLine) {
      const now = new Date().toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
      staleBadge.textContent = `داده‌های آفلاین — بازخوانی در ساعت ${now}`;
      staleBadge.classList.remove("hidden");
    } else {
      staleBadge.classList.add("hidden");
    }
  }

  // Outbox UI Binding
  async function refreshOutboxUi() {
    if (!window.ShiftOutbox || !window.ShiftOutbox.getQueuedItems) return;

    const items = await window.ShiftOutbox.getQueuedItems();
    const countBadge = document.getElementById("outbox-count-badge");
    const listEl = document.getElementById("outbox-list");
    const container = document.getElementById("outbox-card");

    if (countBadge) {
      if (items.length > 0) {
        countBadge.textContent = `${items.length} در صف ارسال`;
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
        title.textContent = item.body && item.body.type ? item.body.type : "رویداد ثبت‌شده";

        const time = document.createElement("span");
        time.className = "outbox-item-time";
        const d = new Date(item.created_at);
        time.textContent = `${d.toLocaleTimeString("fa-IR")} (${item.tries} تلاش)`;

        info.appendChild(title);
        info.appendChild(time);
        li.appendChild(info);

        const actions = document.createElement("div");
        if (item.status === "failed") {
          const badge = document.createElement("span");
          badge.className = "badge badge-failed";
          badge.textContent = "ارسال نشد";

          const retryBtn = document.createElement("button");
          retryBtn.className = "btn btn-secondary";
          retryBtn.style.padding = "0.25rem 0.5rem";
          retryBtn.style.fontSize = "0.75rem";
          retryBtn.textContent = "تلاش مجدد";
          retryBtn.onclick = () => window.ShiftOutbox.retryFailedItem(item.idempotency_key);

          actions.appendChild(badge);
          actions.appendChild(retryBtn);
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
  window.addEventListener("outbox:sent", () => {
    refreshOutboxUi();
  });

  // Client Event Dispatch Helper
  // Writes NEVER claim success offline (show queued state)
  window.submitShiftEvent = async function (eventData) {
    const feedback = document.getElementById("submission-feedback");
    try {
      if (!eventData.client_event_id && !eventData.event_id) {
        eventData.client_event_id = window.ShiftOutbox.mintUlid();
      }
      if (!eventData.idempotency_key) {
        eventData.idempotency_key = eventData.client_event_id || eventData.event_id;
      }
      if (!eventData.client_at) {
        eventData.client_at = new Date().toISOString();
      }

      await window.ShiftOutbox.enqueueOutboxItem(eventData);

      if (feedback) {
        if (!navigator.onLine) {
          feedback.textContent = "رویداد در صف آفلاین ذخیره شد (در صف ارسال).";
          feedback.className = "badge badge-queued";
        } else {
          feedback.textContent = "رویداد ثبت و به سرور ارسال شد.";
          feedback.className = "badge badge-queued";
        }
        feedback.classList.remove("hidden");
      }
    } catch (err) {
      if (feedback) {
        feedback.textContent = "خطا در ثبت رویداد: " + err.message;
        feedback.className = "badge badge-failed";
        feedback.classList.remove("hidden");
      }
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    updateNetworkStatus();
    checkStaleCache();
    refreshOutboxUi();
  });
})();
