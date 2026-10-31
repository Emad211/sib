/**
 * Sib Shift - Service Worker
 * The cache name embeds the app version (injected by routes_web.py), so every
 * deploy busts it. Pre-caches the static shell, never a page (the signed-in home
 * is stored when it is first opened, and a guest answer is no-store), serves navigations network-first with a cache/offline fallback, and keeps
 * static assets cache-first.
 *
 * One PRECACHE_URLS list: a new static file is one line here. A missing entry is
 * skipped instead of failing the whole install, so a partially deployed shell
 * still works and the asset is fetched on demand.
 *
 * Updates never apply silently: a waiting worker only takes over when the page
 * answers the Persian "نسخهٔ تازه آماده است" banner with SKIP_WAITING and reloads.
 * The outbox lives in IndexedDB, so a reload keeps every unsent record.
 *
 * Background Sync only nudges open clients; the page owns the IndexedDB outbox
 * and its retry timer. With no client open the browser retries the sync later.
 */

const VERSION = "__SHIFT_VERSION__";
const CACHE_NAME = `shift-cache-v${VERSION}`;
const OFFLINE_URL = "/offline.html";

const PRECACHE_URLS = [
  "/offline.html",
  "/manifest.webmanifest",
  "/static/app.css",
  "/static/app.js",
  "/static/pwa.js",
  "/static/accountant.js",
  "/static/csrf.js",
  "/static/jalali-datepicker.css",
  "/static/jalali-datepicker.js",
  "/static/handover.css",
  "/static/handover.js",
  "/static/handover-baseline.js",
  "/static/manager.css",
  "/static/manager.js",
  "/static/manager-baseline.js",
  "/static/manager-campaigns.js",
  "/static/manager-items.js",
  "/static/item-icons.js",
  "/static/offline.js",
  "/static/outbox-idb.js",
  "/static/purchase.css",
  "/static/purchase.js",
  "/static/rewards.css",
  "/static/rewards.js",
  "/static/csrf.js",
  "/static/presence.js",
  "/static/manager-staff.js",
  "/static/shift-events.css",
  "/static/shift-events.js",
  "/static/fonts/Vazirmatn-Light.woff2",
  "/static/fonts/Vazirmatn-Regular.woff2",
  "/static/fonts/Vazirmatn-Medium.woff2",
  "/static/fonts/Vazirmatn-SemiBold.woff2",
  "/static/fonts/Vazirmatn-Bold.woff2",
  "/static/fonts/Vazirmatn-ExtraBold.woff2",
  "/static/fonts/Vazirmatn-Black.woff2",
  "/static/fonts/Vazirmatn-Thin.woff2",
  "/static/icons/apple-180.png",
  "/static/icons/192.png",
  "/static/icons/maskable-192.png",
  "/static/icons/512.png",
  "/static/icons/maskable-512.png",
];

async function precacheShell() {
  const cache = await caches.open(CACHE_NAME);
  const results = await Promise.allSettled(PRECACHE_URLS.map((url) => cache.add(url)));
  const failed = PRECACHE_URLS.filter((_, index) => results[index].status === "rejected");
  if (failed.length > 0) {
    // Never reject the install for one renamed asset: the rest of the shell must
    // still work offline, and the missing file is retried by the fetch handler.
    console.warn("[sw] precache skipped:", failed.join(", "));
  }
}

// Install: fill the versioned cache, then wait. The page decides when to switch.
self.addEventListener("install", (event) => {
  event.waitUntil(precacheShell());
});

// Activate: drop every older shell cache and take over the open pages.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => {
        return Promise.all(
          keys.map((key) => {
            if (key !== CACHE_NAME && key.startsWith("shift-cache-")) {
              return caches.delete(key);
            }
            return undefined;
          })
        );
      })
      .then(() => self.clients.claim())
  );
});

// The page asks for the switch only after its Persian update banner is accepted.
self.addEventListener("message", (event) => {
  const data = event.data;
  if (data === "SKIP_WAITING" || (data && data.type === "SKIP_WAITING")) {
    self.skipWaiting();
  }
});

function isStorable(response) {
  if (!response || !response.ok || response.type !== "basic") {
    return false;
  }
  const cacheControl = response.headers.get("Cache-Control") || "";
  return !cacheControl.includes("no-store");
}

// Fetch handling
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  if (url.origin !== self.location.origin) {
    return;
  }

  // The outbox owns POSTs: never fabricate a "queued" response here.
  if (req.method !== "GET") {
    return;
  }

  // API answers are live-only (ADR-016). A handover status replayed from a cache would be a
  // stale screen next morning, and R-02 keeps every count answer out of any store on the phone.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(req));
    return;
  }

  // The manager's card is manager-only (S01 §6): it is never stored on the phone and never answered
  // from a cache, so a second person holding the same handset cannot read yesterday's card offline.
  if (url.pathname === "/manager" || url.pathname.startsWith("/manager/")) {
    event.respondWith(fetch(req));
    return;
  }

  // Same-origin navigations: Network-first with cache fallback. ignoreSearch
  // makes "/" serve the installed start_url "/?src=pwa" (and vice versa).
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((response) => {
          if (isStorable(response)) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
          }
          return response;
        })
        .catch(async () => {
          const cached = await caches.match(req, { ignoreSearch: true });
          if (cached) {
            return cached;
          }
          const offlinePage = await caches.match(OFFLINE_URL);
          if (offlinePage) {
            return offlinePage;
          }
          return new Response("دستگاه آفلاین است.", {
            status: 503,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          });
        })
    );
    return;
  }

  // Static assets (/static/, fonts, icons, manifest): Cache-first with a quiet revalidation.
  if (
    url.pathname.startsWith("/static/") ||
    url.pathname === "/manifest.webmanifest" ||
    url.pathname.endsWith(".woff2") ||
    url.pathname.endsWith(".png")
  ) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) {
          // Revalidate in background, but never replace the cached shell with a no-store answer.
          fetch(req)
            .then((networkResp) => {
              if (isStorable(networkResp)) {
                caches.open(CACHE_NAME).then((cache) => cache.put(req, networkResp));
              }
            })
            .catch(() => {});
          return cached;
        }
        return fetch(req).then((response) => {
          if (isStorable(response)) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Default: Network with cache fallback
  event.respondWith(fetch(req).catch(() => caches.match(req, { ignoreSearch: true })));
});

// Background Sync handler (best effort: the page replays the outbox itself)
self.addEventListener("sync", (event) => {
  if (event.tag === "replay-outbox") {
    event.waitUntil(
      self.clients.matchAll({ includeUncontrolled: true, type: "window" }).then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: "SYNC_REPLAY_OUTBOX" });
        });
      })
    );
  }
});
