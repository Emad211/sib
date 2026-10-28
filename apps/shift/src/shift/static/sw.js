/**
 * Sib Shift - Service Worker
 * Cache-name embeds version for atomic cache busting on deploy.
 * Pre-caches shell assets, serves network-first navigations with offline fallback,
 * static cache-first, and supports Background Sync outbox replay.
 */

const VERSION = "0.1.0";
const CACHE_NAME = `shift-cache-v${VERSION}`;

const PRECACHE_URLS = [
  "/",
  "/offline.html",
  "/manifest.webmanifest",
  "/static/app.css",
  "/static/app.js",
  "/static/outbox-idb.js",
  "/static/csrf.js",
  "/static/fonts/Vazirmatn-Regular.woff2",
  "/static/fonts/Vazirmatn-Bold.woff2",
  "/static/icons/192.png",
  "/static/icons/512.png",
  "/static/icons/maskable-512.png",
];

// Install: precache essential shell assets
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_URLS).catch((err) => {
        // Individual missing asset shouldn't crash SW install
        console.warn("SW precache partial warning:", err);
      });
    }).then(() => self.skipWaiting())
  );
});

// Activate: bust old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME && key.startsWith("shift-cache-")) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch handling
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Ignore non-HTTP/HTTPS and non-GET requests for standard caching
  if (req.method !== "GET") {
    // Intercept POST /api/v1/events if offline: return structured offline status
    if (url.pathname.startsWith("/api/v1/events") && !navigator.onLine) {
      event.respondWith(
        new Response(
          JSON.stringify({
            status: "queued",
            offline: true,
            message: "دستگاه آفلاین است؛ در صف ارسال ذخیره شد.",
          }),
          {
            status: 503,
            headers: { "Content-Type": "application/json" },
          }
        )
      );
    }
    return;
  }

  // Same-origin navigations: Network-first with cache fallback
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
          const cached = await caches.match(req);
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
          fetch(req).then((networkResp) => {
            if (networkResp.ok) {
              caches.open(CACHE_NAME).then((cache) => cache.put(req, networkResp));
            }
          }).catch(() => {});
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
  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});

// Background Sync handler
self.addEventListener("sync", (event) => {
  if (event.tag === "replay-outbox") {
    event.waitUntil(
      self.clients.matchAll().then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: "SYNC_REPLAY_OUTBOX" });
        });
      })
    );
  }
});
