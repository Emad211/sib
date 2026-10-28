/**
 * Sib Shift - Offline Outbox (IndexedDB)
 * Pure functions exported for node:test; browser runtime for IndexedDB & replay.
 */

const MAX_TRIES = 10;
const MAX_BACKOFF_SECONDS = 300;
const CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/**
 * Pure backoff formula: min(2^tries, 300)s + jitter (seconds).
 * @param {number} tries - Current attempt number (0-indexed or 1-indexed)
 * @param {number} jitter - Jitter in seconds [0, 1)
 * @returns {number} Delay in seconds
 */
function calculateBackoffSeconds(tries, jitter = 0) {
  const safeTries = Math.max(0, Math.floor(tries));
  const exponential = Math.pow(2, safeTries);
  const base = Math.min(exponential, MAX_BACKOFF_SECONDS);
  return base + Math.max(0, jitter);
}

/**
 * Calculate backoff in milliseconds.
 * @param {number} tries
 * @param {number} jitterRatio - Jitter factor [0, 1)
 * @returns {number} Delay in ms
 */
function calculateBackoffMs(tries, jitterRatio = 0) {
  const jitterSec = jitterRatio > 0 ? jitterRatio : Math.random();
  return Math.round(calculateBackoffSeconds(tries, jitterSec) * 1000);
}

/**
 * Determine if manual intervention is required.
 * @param {number} tries
 * @returns {boolean} True if attempts reached or exceeded MAX_TRIES (10)
 */
function isManualRetryNeeded(tries) {
  return tries >= MAX_TRIES;
}

/**
 * Mint a 26-character Crockford Base32 ULID.
 * Compatible with shift.common.ids.is_ulid.
 * @param {number} timestampMs
 * @param {Uint8Array} [randomBytes]
 * @returns {string} 26-char ULID
 */
function mintUlid(timestampMs = Date.now(), randomBytes = null) {
  let timeStr = "";
  let time = timestampMs;
  for (let i = 9; i >= 0; i--) {
    const mod = time % 32;
    timeStr = CROCKFORD_BASE32[mod] + timeStr;
    time = Math.floor(time / 32);
  }

  let randStr = "";
  if (randomBytes && randomBytes.length >= 10) {
    for (let i = 0; i < 16; i++) {
      const byte = randomBytes[i % randomBytes.length];
      randStr += CROCKFORD_BASE32[byte % 32];
    }
  } else if (typeof crypto !== "undefined" && crypto.getRandomValues) {
    const buf = new Uint8Array(16);
    crypto.getRandomValues(buf);
    for (let i = 0; i < 16; i++) {
      randStr += CROCKFORD_BASE32[buf[i] % 32];
    }
  } else {
    for (let i = 0; i < 16; i++) {
      randStr += CROCKFORD_BASE32[Math.floor(Math.random() * 32)];
    }
  }

  return timeStr + randStr;
}

/**
 * Construct an outbox record.
 * @param {Object} params
 * @returns {Object} Clean outbox record
 */
function createOutboxRecord({
  idempotency_key,
  url = "/api/v1/events",
  body,
  now = Date.now(),
}) {
  const key = idempotency_key || (body && (body.idempotency_key || body.client_event_id || body.event_id)) || mintUlid(now);
  return {
    idempotency_key: key,
    url: url,
    body: body,
    created_at: new Date(now).toISOString(),
    tries: 0,
    next_try_at: now,
    status: "queued", // 'queued' | 'failed' | 'sent'
    last_error: null,
  };
}

// ==========================================
// Browser IndexedDB Runtime
// ==========================================
const DB_NAME = "shift_pwa_db";
const DB_VERSION = 1;
const STORE_NAME = "outbox";

let idbPromise = null;

function getDb() {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("IndexedDB not available"));
  }
  if (!idbPromise) {
    idbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (e) => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          const store = db.createObjectStore(STORE_NAME, { keyPath: "idempotency_key" });
          store.createIndex("status", "status", { unique: false });
          store.createIndex("next_try_at", "next_try_at", { unique: false });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return idbPromise;
}

async function enqueueOutboxItem(body, url = "/api/v1/events") {
  const db = await getDb();
  const record = createOutboxRecord({ body, url });
  await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const req = tx.objectStore(STORE_NAME).put(record);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  dispatchOutboxChanged();
  triggerReplay();
  return record;
}

async function getQueuedItems() {
  const db = await getDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function removeOutboxItem(idempotencyKey) {
  const db = await getDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const req = tx.objectStore(STORE_NAME).delete(idempotencyKey);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

async function updateOutboxItem(record) {
  const db = await getDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const req = tx.objectStore(STORE_NAME).put(record);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

let isReplaying = false;

async function replayOutbox() {
  if (isReplaying || (typeof navigator !== "undefined" && !navigator.onLine)) {
    return;
  }
  isReplaying = true;
  try {
    const items = await getQueuedItems();
    const now = Date.now();

    for (const item of items) {
      if (item.status === "failed" || item.next_try_at > now) {
        continue;
      }

      try {
        const resp = await fetch(item.url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "application/json",
          },
          body: JSON.stringify(item.body),
        });

        if (resp.ok) {
          // Exactly-once guarantee on server side (200 on replay)
          await removeOutboxItem(item.idempotency_key);
          dispatchOutboxSent(item);
        } else if (resp.status >= 400 && resp.status < 500 && resp.status !== 409) {
          // Unfixable validation error
          item.status = "failed";
          item.last_error = `Server error ${resp.status}`;
          await updateOutboxItem(item);
        } else {
          // 5xx server error or retryable status
          item.tries += 1;
          if (isManualRetryNeeded(item.tries)) {
            item.status = "failed";
            item.last_error = "تعداد تلاش‌ها از حد مجاز گذشت";
          } else {
            item.next_try_at = Date.now() + calculateBackoffMs(item.tries);
          }
          await updateOutboxItem(item);
        }
      } catch (err) {
        // Network failure
        item.tries += 1;
        if (isManualRetryNeeded(item.tries)) {
          item.status = "failed";
          item.last_error = "ارسال پس از ۱۰ بار تلاش ناموفق بود";
        } else {
          item.next_try_at = Date.now() + calculateBackoffMs(item.tries);
        }
        await updateOutboxItem(item);
      }
    }
  } finally {
    isReplaying = false;
    dispatchOutboxChanged();
  }
}

async function retryFailedItem(idempotencyKey) {
  const db = await getDb();
  const item = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).get(idempotencyKey);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  if (item) {
    item.status = "queued";
    item.tries = 0;
    item.next_try_at = Date.now();
    item.last_error = null;
    await updateOutboxItem(item);
    dispatchOutboxChanged();
    triggerReplay();
  }
}

function triggerReplay() {
  if (typeof navigator !== "undefined" && navigator.onLine) {
    if (typeof window !== "undefined" && "serviceWorker" in navigator && "SyncManager" in window) {
      navigator.serviceWorker.ready
        .then((reg) => reg.sync.register("replay-outbox"))
        .catch(() => replayOutbox());
    } else {
      replayOutbox();
    }
  }
}

function dispatchOutboxChanged() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("outbox:change"));
  }
}

function dispatchOutboxSent(item) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("outbox:sent", { detail: item }));
  }
}

// Browser lifecycle listeners
if (typeof window !== "undefined") {
  window.addEventListener("online", () => {
    triggerReplay();
  });

  window.addEventListener("load", () => {
    triggerReplay();
  });
}

// Module exports for node:test and browser global
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    calculateBackoffSeconds,
    calculateBackoffMs,
    isManualRetryNeeded,
    createOutboxRecord,
    mintUlid,
    MAX_TRIES,
    MAX_BACKOFF_SECONDS,
  };
}

if (typeof window !== "undefined") {
  window.ShiftOutbox = {
    calculateBackoffSeconds,
    calculateBackoffMs,
    isManualRetryNeeded,
    createOutboxRecord,
    mintUlid,
    enqueueOutboxItem,
    getQueuedItems,
    replayOutbox,
    retryFailedItem,
    triggerReplay,
  };
}
