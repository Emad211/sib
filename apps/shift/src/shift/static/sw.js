/**
 * Sib Shift - Service Worker
 * The cache name embeds the app version (injected by routes_web.py), so every
 * deploy busts it. Pre-caches the shell including the manifest start_url,
 * serves navigations network-first with a cache/offline fallback, and keeps
 * static assets cache-first.
 *
 * Background Sync only nudges open clients; the page owns the IndexedDB outbox
 * and its retry timer. With no client open the browser retries the sync later.
 */

const VERSION = "__SHIFT_VERSION__";
const CACHE_NAME = `shift-cache-v${VERSION}`;

const PRECACHE_URLS = [
  "/",
  "/?src=pwa",
  "/offline.html",
  "/manifest.webmanifest",
  "/static/app.css",
  "/static/app.js",
  "/static/handover.css",
  "/static/handover.js",
  "/static/home.js",
  "/static/offline.js",
  "/static/outbox-idb.js",
  "/static/csrf.js",
  "/static/fonts/Vazirmatn-Regular.woff2",
  "/static/fonts/Vazirmatn-Medium.woff2",
  "/static/fonts/Vazirmatn-Bold.woff2",
  "/static/icons/192.png",
  "/static/icons/512.png",
  "/static/icons/maskable-512.png",
];

async function precacheShell() {
  const cache = await caches.open(CACHE_NAME);
  const results = await Promise.allSettled(PRECACHE_URLS.map((url) => cache.add(url)));
  const failed = PRECACHE_URLS.filter((_, index) => results[index].status === "rejected");
  if (failed.length > 0) {
    // Fail install loudly: silently installing an empty cache would disable
    // offline mode after a deploy that renamed or removed an asset.
    throw new Error(`SW precache failed for: ${failed.join(", ")}`);
  }
}

// Install: precache every shell asset; a failure rejects the whole install.
self.addEventListener("install", (event) => {
  event.waitUntil(precacheShell().then(() => self.skipWaiting()));
});

// Activate: bust old caches
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

// Fetch handling
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

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

  // Same-origin navigations: Network-first with cache fallback. ignoreSearch
  // makes "/" serve the installed start_url "/?src=pwa" (and vice versa).
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((response) => {
          if (response.ok) {
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
          const offlinePage = await caches.match("/offline.html");
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

  // Static assets (/static/, fonts, icons, manifest): Cache-first with network fallback
  if (
    url.pathname.startsWith("/static/") ||
    url.pathname === "/manifest.webmanifest" ||
    url.pathname.endsWith(".woff2") ||
    url.pathname.endsWith(".png")
  ) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) {
          // Revalidate in background
          fetch(req)
            .then((networkResp) => {
              if (networkResp.ok) {
                caches.open(CACHE_NAME).then((cache) => cache.put(req, networkResp));
              }
            })
            .catch(() => {});
          return cached;
        }
        return fetch(req).then((response) => {
          if (response.ok) {
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
