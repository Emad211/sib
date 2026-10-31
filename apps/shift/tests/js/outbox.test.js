/**
 * Unit tests for outbox-idb pure functions using node:test.
 * Zero external dependencies; no package.json needed.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const outboxPath = path.resolve(__dirname, "../../src/shift/static/outbox-idb.js");
const {
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
} = require(outboxPath);

const EVENT_ID = "01JABCDEFGHJKMNPQRSTVWXYZ1";

describe("Outbox Exponential Backoff Logic", () => {
  it("caps exponential backoff at min(2^tries, 300) seconds", () => {
    assert.strictEqual(calculateBackoffSeconds(0, 0), 1);
    assert.strictEqual(calculateBackoffSeconds(1, 0), 2);
    assert.strictEqual(calculateBackoffSeconds(2, 0), 4);
    assert.strictEqual(calculateBackoffSeconds(5, 0), 32);
    assert.strictEqual(calculateBackoffSeconds(8, 0), 256);
    assert.strictEqual(calculateBackoffSeconds(9, 0), 300);
    assert.strictEqual(calculateBackoffSeconds(10, 0), 300);
    assert.strictEqual(calculateBackoffSeconds(50, 0), MAX_BACKOFF_SECONDS);
  });

  it("applies jitter strictly as an additive factor", () => {
    const delay = calculateBackoffSeconds(3, 0.42);
    assert.strictEqual(Math.round(delay * 100) / 100, 8.42);
    assert.strictEqual(calculateBackoffSeconds(2, -0.5), 4);
  });

  it("honours an explicit zero jitter and randomises only when omitted", () => {
    assert.strictEqual(calculateBackoffMs(0, 0), 1000);
    assert.strictEqual(calculateBackoffMs(2, 0.5), 4500);
    const randomMs = calculateBackoffMs(3);
    assert.ok(randomMs >= 8000 && randomMs <= 9000);
  });
});

describe("Outbox Manual Retry Threshold (10 tries)", () => {
  it("requires manual retry only after 10 attempts", () => {
    assert.strictEqual(MAX_TRIES, 10);
    assert.strictEqual(isManualRetryNeeded(0), false);
    assert.strictEqual(isManualRetryNeeded(9), false);
    assert.strictEqual(isManualRetryNeeded(10), true);
    assert.strictEqual(isManualRetryNeeded(15), true);
  });
});

describe("Outbox Record Creation & Invariants", () => {
  it("initializes a queued record for an offline-allowed event", () => {
    const record = createOutboxRecord({
      body: {
        type: "stock.waste_reported",
        client_event_id: EVENT_ID,
        payload: { item_id: "i", location_id: "l", quantity: 1 },
      },
    });

    assert.strictEqual(record.status, "queued");
    assert.strictEqual(record.tries, 0);
    assert.strictEqual(record.url, "/api/v1/events");
    assert.strictEqual(record.idempotency_key, EVENT_ID);
    assert.ok(Date.parse(record.created_at) > 0);
  });

  it("uses a custom idempotency_key when provided", () => {
    const record = createOutboxRecord({
      idempotency_key: "custom-idempotency-key-123",
      body: { type: "stock.waste_reported" },
    });

    assert.strictEqual(record.idempotency_key, "custom-idempotency-key-123");
  });

  it("rejects arbitrary URLs and online-only event types", () => {
    assert.strictEqual(isAllowedOutboxUrl("/api/v1/events"), true);
    assert.strictEqual(isAllowedOutboxUrl("/api/v1/auth/login"), false);
    assert.strictEqual(isAllowedOutboxUrl("https://evil.example/api/v1/events"), false);

    assert.throws(
      () => createOutboxRecord({ url: "/api/v1/auth/login", body: { type: "stock.waste_reported" } }),
      /non-idempotent URL/
    );
    assert.throws(
      () => createOutboxRecord({ body: { type: "handover.cash_counted" } }),
      /online-only event type/
    );
    assert.strictEqual(isOfflineEventTypeAllowed("stock.waste_reported"), true);
    assert.strictEqual(isOfflineEventTypeAllowed("cash.withdrawn"), true);
    assert.strictEqual(isOfflineEventTypeAllowed("event.approved"), true);
    assert.strictEqual(isOfflineEventTypeAllowed("handover.cash_counted"), false);
  });
});

describe("Server response validation (no silent delete on a false 200)", () => {
  it("accepts the documented ingest response", () => {
    assert.strictEqual(
      isValidIngestResponse({ seq: 1, event_id: EVENT_ID, created: true, status: "recorded" }, EVENT_ID),
      true
    );
    assert.strictEqual(
      isValidIngestResponse({ seq: 1, event_id: EVENT_ID, created: false, status: "replayed" }, EVENT_ID),
      true
    );
  });

  it("rejects HTML/login pages and mismatched or malformed bodies", () => {
    assert.strictEqual(isValidIngestResponse("<html>login</html>", EVENT_ID), false);
    assert.strictEqual(isValidIngestResponse(null, EVENT_ID), false);
    assert.strictEqual(isValidIngestResponse({}, EVENT_ID), false);
    assert.strictEqual(
      isValidIngestResponse({ seq: 1, event_id: "01JABCDEFGHJKMNPQRSTVWXYZ2", created: true, status: "recorded" }, EVENT_ID),
      false
    );
    assert.strictEqual(
      isValidIngestResponse({ seq: 1, event_id: EVENT_ID, created: "yes", status: "recorded" }, EVENT_ID),
      false
    );
    assert.strictEqual(
      isValidIngestResponse({ seq: 0, event_id: EVENT_ID, created: true, status: "recorded" }, EVENT_ID),
      false
    );
  });

  it("reads the expected id from client_event_id or event_id", () => {
    assert.strictEqual(expectedEventIdFor({ body: { client_event_id: EVENT_ID, event_id: "other" } }), EVENT_ID);
    assert.strictEqual(expectedEventIdFor({ body: { event_id: EVENT_ID } }), EVENT_ID);
    assert.strictEqual(expectedEventIdFor({ body: {} }), null);
    assert.strictEqual(expectedEventIdFor(null), null);
  });

  it("classifies terminal 4xx separately from recoverable statuses", () => {
    for (const status of [400, 404, 410, 422]) {
      assert.strictEqual(classifyHttpStatus(status), "terminal", String(status));
    }
    for (const status of [401, 403, 408, 409, 425, 429, 500, 503]) {
      assert.strictEqual(classifyHttpStatus(status), "retry", String(status));
    }
  });
});

describe("Retry scheduling", () => {
  it("returns the earliest delay and ignores failed items", () => {
    const now = 1_000_000;
    assert.strictEqual(nextRetryDelayMs([], now), null);
    assert.strictEqual(nextRetryDelayMs([{ status: "failed", next_try_at: now }], now), null);
    assert.strictEqual(
      nextRetryDelayMs(
        [
          { status: "queued", next_try_at: now + 5000 },
          { status: "queued", next_try_at: now + 2000 },
        ],
        now
      ),
      2000
    );
    assert.strictEqual(nextRetryDelayMs([{ status: "queued", next_try_at: now - 1 }], now), 0);
  });
});

describe("Client-side ULID and Tehran business date", () => {
  it("generates valid 26-character Crockford Base32 ULIDs", () => {
    const ulid = mintUlid();
    assert.strictEqual(ulid.length, 26);
    assert.match(ulid, /^[0-9A-HJKMNP-TV-Z]{26}$/);
  });

  it("is monotonically increasing across timestamps", () => {
    const u1 = mintUlid(1700000000000, new Uint8Array(16));
    const u2 = mintUlid(1700000001000, new Uint8Array(16));
    assert.ok(u1 < u2, `Expected ${u1} < ${u2}`);
  });

  it("produces distinct ULIDs for two calls in the same millisecond", () => {
    const first = mintUlid(1700000000000, new Uint8Array(16).fill(1));
    const second = mintUlid(1700000000000, new Uint8Array(16).fill(2));
    assert.notStrictEqual(first, second);
  });

  it("puts the night shift on its opening date", () => {
    // 2026-10-29T01:00+03:30 == 2026-10-28T21:30Z
    assert.strictEqual(tehranBusinessDate(Date.UTC(2026, 9, 28, 21, 30)), "2026-10-28");
    // 2026-10-28T07:29+03:30 is still the night shift that opened on the 27th.
    assert.strictEqual(tehranBusinessDate(Date.UTC(2026, 9, 28, 3, 59)), "2026-10-27");
    // 2026-10-28T12:00+03:30 is the day shift.
    assert.strictEqual(tehranBusinessDate(Date.UTC(2026, 9, 28, 8, 30)), "2026-10-28");
  });
});
