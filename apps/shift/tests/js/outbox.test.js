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
  MAX_TRIES,
  MAX_BACKOFF_SECONDS,
} = require(outboxPath);

describe("Outbox Exponential Backoff Logic", () => {
  it("caps exponential backoff at min(2^tries, 300) seconds", () => {
    // 2^0 = 1
    assert.strictEqual(calculateBackoffSeconds(0, 0), 1);
    // 2^1 = 2
    assert.strictEqual(calculateBackoffSeconds(1, 0), 2);
    // 2^2 = 4
    assert.strictEqual(calculateBackoffSeconds(2, 0), 4);
    // 2^5 = 32
    assert.strictEqual(calculateBackoffSeconds(5, 0), 32);
    // 2^8 = 256
    assert.strictEqual(calculateBackoffSeconds(8, 0), 256);
    // 2^9 = 512 -> capped at 300
    assert.strictEqual(calculateBackoffSeconds(9, 0), 300);
    // 2^10 = 1024 -> capped at 300
    assert.strictEqual(calculateBackoffSeconds(10, 0), 300);
    // Large tries remains capped
    assert.strictEqual(calculateBackoffSeconds(50, 0), MAX_BACKOFF_SECONDS);
  });

  it("applies jitter strictly as an additive factor", () => {
    const delay = calculateBackoffSeconds(3, 0.42); // 8 + 0.42 = 8.42
    assert.strictEqual(Math.round(delay * 100) / 100, 8.42);

    // Negative jitter is clamped
    assert.strictEqual(calculateBackoffSeconds(2, -0.5), 4);
  });

  it("calculates backoff in milliseconds within expected range", () => {
    const ms = calculateBackoffMs(2, 0.5); // (4 + 0.5) * 1000 = 4500
    assert.strictEqual(ms, 4500);

    const randomMs = calculateBackoffMs(3); // 8s + [0, 1000) ms
    assert.ok(randomMs >= 8000 && randomMs <= 9000);
  });
});

describe("Outbox Manual Retry Threshold (10 tries)", () => {
  it("requires manual retry only after 10 attempts", () => {
    assert.strictEqual(MAX_TRIES, 10);
    assert.strictEqual(isManualRetryNeeded(0), false);
    assert.strictEqual(isManualRetryNeeded(1), false);
    assert.strictEqual(isManualRetryNeeded(5), false);
    assert.strictEqual(isManualRetryNeeded(9), false);

    // At attempt 10: transition to manual retry ("ارسال نشد")
    assert.strictEqual(isManualRetryNeeded(10), true);
    assert.strictEqual(isManualRetryNeeded(15), true);
  });
});

describe("Outbox Record Creation & Invariants", () => {
  it("initializes record with queued status and tries=0", () => {
    const record = createOutboxRecord({
      body: {
        type: "handover.cash_counted",
        payload: { counted: 1000 },
        client_event_id: "01JABCDEFGHJKMNPQRSTVWXYZ1",
      },
    });

    assert.strictEqual(record.status, "queued");
    assert.strictEqual(record.tries, 0);
    assert.strictEqual(record.url, "/api/v1/events");
    assert.strictEqual(record.idempotency_key, "01JABCDEFGHJKMNPQRSTVWXYZ1");
    assert.ok(Date.parse(record.created_at) > 0);
  });

  it("uses custom idempotency_key when provided", () => {
    const record = createOutboxRecord({
      idempotency_key: "custom-idempotency-key-123",
      body: { type: "stock.waste_reported" },
    });

    assert.strictEqual(record.idempotency_key, "custom-idempotency-key-123");
  });
});

describe("Client-side ULID Minting", () => {
  it("generates valid 26-character Crockford Base32 ULIDs", () => {
    const ulid = mintUlid();
    assert.strictEqual(ulid.length, 26);
    // Regex for 26 Crockford characters (0-9, A-Z excluding I, L, O, U)
    assert.match(ulid, /^[0-9A-HJKMNP-TV-Z]{26}$/);
  });

  it("is monotonically increasing across timestamps", () => {
    const t1 = 1700000000000;
    const t2 = 1700000001000;
    const u1 = mintUlid(t1);
    const u2 = mintUlid(t2);
    assert.ok(u1 < u2, `Expected ${u1} < ${u2}`);
  });
});
