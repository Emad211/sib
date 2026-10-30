/**
 * Sib Shift - Offline Outbox (IndexedDB)
 * Pure functions exported for node:test; browser runtime for IndexedDB & replay.
 */

const MAX_TRIES = 10;
const MAX_BACKOFF_SECONDS = 300;
const CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const EVENTS_URL = "/api/v1/events";
const SYNC_TAG = "replay-outbox";
const SW_READY_TIMEOUT_MS = 3000;

// ADR-016: handover and blind counts are online-only; only current-shift
// stock events may enter the offline queue.
const OFFLINE_EVENT_TYPES = new Set([
  "stock.waste_reported",
  "stock.emergency_withdrawal",
  "stock.shelf_restocked",
]);

// These statuses describe the request itself, not a transient server state.
const TERMINAL_HTTP_STATUSES = new Set([400, 404, 410, 422]);

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
 * Calculate backoff in milliseconds. An omitted jitter means "random jitter";
 * passing 0 is an explicit zero-jitter request (deterministic tests).
 * @param {number} tries
 * @param {number} [jitterRatio] - Jitter factor [0, 1)
 * @returns {number} Delay in ms
 */
function calculateBackoffMs(tries, jitterRatio) {
  const jitterSec = jitterRatio === undefined ? Math.random() : Math.max(0, jitterRatio);
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
 * Shift business date in Tehran (UTC+03:30): the night shift that crosses
 * midnight belongs to the date on which it opened. Mirrors
 * services/outbox.business_date_for.
 * @param {number} nowMs
 * @returns {string} YYYY-MM-DD
 */
function tehranBusinessDate(nowMs = Date.now()) {
  const local = new Date(Number(nowMs) + (3 * 60 + 30) * 60 * 1000);
  const minutes = local.getUTCHours() * 60 + local.getUTCMinutes();
  if (minutes < 7 * 60 + 30) {
    local.setUTCDate(local.getUTCDate() - 1);
  }
  const year = local.getUTCFullYear();
  const month = String(local.getUTCMonth() + 1).padStart(2, "0");
  const day = String(local.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * @param {string} url
 * @returns {boolean} True only for the idempotent events endpoint.
 */
function isAllowedOutboxUrl(url) {
  return url === EVENTS_URL;
}

/**
 * @param {string} type
 * @returns {boolean} True when ADR-016 permits the event in the offline queue.
 */
function isOfflineEventTypeAllowed(type) {
  return OFFLINE_EVENT_TYPES.has(type);
}

/**
 * Validate the ingest response before deleting a queued item. A captive portal
 * or login redirect can answer 200 with HTML; that must never delete an event.
 * @param {unknown} body
 * @param {string|null} expectedEventId
 * @returns {boolean}
 */
function isValidIngestResponse(body, expectedEventId) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return false;
  if (!Number.isInteger(body.seq) || body.seq < 1) return false;
  if (typeof body.event_id !== "string" || body.event_id !== expectedEventId) return false;
  if (typeof body.created !== "boolean") return false;
  return body.status === "recorded" || body.status === "replayed";
}

/**
 * @param {Object|null} item
 * @returns {string|null} The event ULID stored in the queued body.
 */
function expectedEventIdFor(item) {
  const body = item && item.body;
  if (!body) return null;
  return body.client_event_id || body.event_id || null;
}

/**
 * @param {number} status
 * @returns {"terminal"|"retry"} Whether a non-2xx status is retryable.
 */
function classifyHttpStatus(status) {
  if (TERMINAL_HTTP_STATUSES.has(status)) return "terminal";
  // 401/403 (expired session or unenrolled device), 408, 409, 425, 429 and 5xx
  // are all recoverable with a fresh session or later attempt.
  return "retry";
}

/**
 * Earliest delay until a due replay attempt, or null when nothing is queued.
 * @param {Array<Object>} items
 * @param {number} now
 * @returns {number|null} Delay in ms (0 means "now").
 */
function nextRetryDelayMs(items, now = Date.now()) {
  let delay = null;
  for (const item of items) {
    if (!item || item.status === "failed") continue;
    const at = Number(item.next_try_at) || 0;
    const wait = at > now ? at - now : 0;
    if (delay === null || wait < delay) delay = wait;
  }
  return delay;
}

/**
 * Construct an outbox record.
 * @param {Object} params
 * @returns {Object} Clean outbox record
 */
function createOutboxRecord({ idempotency_key, url = EVENTS_URL, body, now = Date.now() }) {
  if (!isAllowedOutboxUrl(url)) {
    throw new Error(`outbox rejected non-idempotent URL: ${url}`);
  }
  if (!body || typeof body !== "object") {
    throw new Error("outbox body must be an object");
  }
  if (!isOfflineEventTypeAllowed(body.type)) {
    throw new Error(`outbox rejected online-only event type: ${body.type}`);
  }
  const key =
    idempotency_key || body.idempotency_key || body.client_event_id || body.event_id || mintUlid(now);
  return {
    idempotency_key: key,
    url: url,
    body: body,
    created_at: new Date(now).toISOString(),
    tries: 0,
    next_try_at: now,
    status: "queued",
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
let replayTimer = null;
let isReplaying = false;

function getDb() {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("IndexedDB not available"));
  }
  if (!idbPromise) {
    idbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
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

function transactionDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
  });
}

async function withStore(mode, mutate) {
  const db = await getDb();
  const tx = db.transaction(STORE_NAME, mode);
  const result = mutate(tx.objectStore(STORE_NAME));
  await transactionDone(tx);
  return result;
}

async function enqueueOutboxItem(body, url = EVENTS_URL) {
  const record = createOutboxRecord({ body, url });
  await withStore("readwrite", (store) => store.put(record));
  dispatchOutboxChanged();
  triggerReplay();
  return record;
}

async function getQueuedItems() {
  const db = await getDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    let items = [];
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => {
      items = req.result || [];
    };
    tx.oncomplete = () => resolve(items);
    tx.onerror = () => reject(tx.error || new Error("IndexedDB read failed"));
    tx.onabort = () => reject(tx.error || new Error("IndexedDB read aborted"));
  });
}

async function getOutboxItem(idempotencyKey) {
  const db = await getDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    let item = null;
    const req = tx.objectStore(STORE_NAME).get(idempotencyKey);
    req.onsuccess = () => {
      item = req.result || null;
    };
    tx.oncomplete = () => resolve(item);
    tx.onerror = () => reject(tx.error || new Error("IndexedDB read failed"));
    tx.onabort = () => reject(tx.error || new Error("IndexedDB read aborted"));
  });
}

async function removeOutboxItem(idempotencyKey) {
  await withStore("readwrite", (store) => store.delete(idempotencyKey));
}

async function updateOutboxItem(record) {
  await withStore("readwrite", (store) => store.put(record));
}

function scheduleRetry(item, reason) {
  item.tries += 1;
  item.last_error = reason;
  if (isManualRetryNeeded(item.tries)) {
    item.status = "failed";
    item.last_error = "تعداد تلاش‌ها از حد مجاز گذشت";
    return;
  }
  item.next_try_at = Date.now() + calculateBackoffMs(item.tries);
}

async function replayOne(item) {
  const expectedEventId = expectedEventIdFor(item);
  try {
    const resp = await fetch(item.url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify(item.body),
    });

    if (resp.ok) {
      const body = await resp.json().catch(() => null);
      if (isValidIngestResponse(body, expectedEventId)) {
        await removeOutboxItem(item.idempotency_key);
        dispatchOutboxSent(item);
        return;
      }
      scheduleRetry(item, "پاسخ سرور تأییدکننده نبود");
      await updateOutboxItem(item);
      return;
    }

    if (classifyHttpStatus(resp.status) === "terminal") {
      item.status = "failed";
      item.last_error = `Server error ${resp.status}`;
      await updateOutboxItem(item);
      return;
    }

    scheduleRetry(item, `Server error ${resp.status}`);
    await updateOutboxItem(item);
  } catch (err) {
    scheduleRetry(item, "خطای شبکه");
    await updateOutboxItem(item);
  }
}

async function replayOutbox() {
  if (isReplaying) return;
  if (typeof navigator !== "undefined" && !navigator.onLine) {
    scheduleNextReplay();
    return;
  }
  isReplaying = true;
  try {
    const items = await getQueuedItems();
    const now = Date.now();
    const due = items.filter((item) => item.status !== "failed" && item.next_try_at <= now);
    for (const item of due) {
      try {
        await replayOne(item);
      } catch (err) {
        console.warn("Outbox replay item failed:", err);
      }
    }
  } finally {
    isReplaying = false;
    dispatchOutboxChanged();
    scheduleNextReplay();
  }
}

function scheduleNextReplay() {
  if (typeof window === "undefined") return;
  if (replayTimer !== null) {
    clearTimeout(replayTimer);
    replayTimer = null;
  }
  if (typeof navigator !== "undefined" && !navigator.onLine) return;
  getQueuedItems()
    .then((items) => {
      const delay = nextRetryDelayMs(items, Date.now());
      if (delay === null) return;
      replayTimer = setTimeout(() => {
        replayTimer = null;
        replayOutbox();
      }, delay);
    })
    .catch(() => {});
}

async function retryFailedItem(idempotencyKey) {
  const item = await getOutboxItem(idempotencyKey);
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

function registerBackgroundSync() {
  if (typeof window === "undefined" || typeof navigator === "undefined") return;
  if (!("serviceWorker" in navigator) || !("SyncManager" in window)) return;
  const ready = navigator.serviceWorker.ready;
  const timeout = new Promise((_, reject) => {
    setTimeout(() => reject(new Error("service worker ready timeout")), SW_READY_TIMEOUT_MS);
  });
  Promise.race([ready, timeout])
    .then((registration) => registration.sync.register(SYNC_TAG))
    .catch(() => {
      // The page-level replay already ran; Background Sync is only an extra path.
    });
}

function triggerReplay() {
  if (typeof navigator !== "undefined" && !navigator.onLine) return;
  replayOutbox();
  registerBackgroundSync();
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
    tehranBusinessDate,
    isAllowedOutboxUrl,
    isOfflineEventTypeAllowed,
    isValidIngestResponse,
    expectedEventIdFor,
    classifyHttpStatus,
    nextRetryDelayMs,
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
    tehranBusinessDate,
    isOfflineEventTypeAllowed,
    enqueueOutboxItem,
    getQueuedItems,
    replayOutbox,
    retryFailedItem,
    triggerReplay,
  };
}
