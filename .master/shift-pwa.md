# Shift PWA and Offline Outbox — design steps 12, 13 (task_488c62f0fc1e)

Date: 2026-10-28 · Branch: `Emad211/shift-pwa` · Base: `cdb5faf` (via `feat/fb-r1-final`)
Worktree: `C:/Users/Emad Karimi/orca/workspaces/sib/shift-pwa`

## Implementation Summary

### Step 12: Installable PWA Shell & Static Assets
1. **Manifest** (`src/shift/static/manifest.webmanifest`):
   - Standalone PWA configuration with RTL (`dir: "rtl"`, `lang: "fa-IR"`), clinic theme color `#0f766e`, `start_url: "/?src=pwa"`.
   - Icons: 192x192, 512x512, and 512x512 maskable (with 80% inner safe circle).
2. **Icon Generator** (`tools/make_icons.py`):
   - Self-contained pure Python stdlib PNG generator (using `zlib` and `struct`) with Pillow fallback.
   - Generated `192.png`, `512.png`, and `maskable-512.png`.
3. **Fonts & Typography**:
   - Vendored Vazirmatn WOFF2 fonts (`Vazirmatn-Regular.woff2`, `Vazirmatn-Bold.woff2`, `Vazirmatn-Medium.woff2`, etc.) copied directly from `apps/accounting/src/static/fonts`.
   - Embedded `@font-face` definitions with system font fallbacks; zero network dependencies.
4. **Styles** (`src/shift/static/app.css`):
   - Mobile-first, RTL-native, accessible, dark-mode aware (via `prefers-color-scheme`).
   - Strictly enforces minimum 44px touch targets on buttons, inputs, links, and list items.
   - Status badges: online, offline, queued (`در صف ارسال`), failed (`ارسال نشد`), stale cache.
5. **CSRF Enforcement** (`src/shift/static/csrf.js`):
   - Attaches `X-CSRF-Token` to state-changing requests and hidden form fields.
6. **Service Worker** (`src/shift/static/sw.js`):
   - Pre-caches core shell (`/`, `/offline.html`, manifest, CSS, JS, fonts, icons).
   - Serves navigations network-first with cache and offline fallback.
   - Serves static assets cache-first.
   - Intercepts offline `/api/v1/events` and coordinates Background Sync.
7. **Jinja2 Templates** (`src/shift/templates/{base.html, home.html}`):
   - Mobile-first semantic HTML, ARIA status announcements, network connection banner, and live outbox queue display.
8. **Web Router** (`src/shift/api/routes_web.py`):
   - Serves `/` (home shell), `/offline.html`, `/manifest.webmanifest`, and `/sw.js` with root scope (`Service-Worker-Allowed: /`) and `Cache-Control: no-cache`.

### Step 13: Offline Outbox & Idempotent API Ingest
1. **Client Outbox** (`src/shift/static/outbox-idb.js`):
   - IndexedDB queue storing `{ idempotency_key, url, body, created_at, tries, next_try_at, status }`.
   - Pure functions exported for testability:
     - `calculateBackoffSeconds(tries, jitter)`: exponential backoff `min(2^tries, 300)s + jitter`.
     - `isManualRetryNeeded(tries)`: triggers manual retry UI after 10 attempts.
     - `mintUlid()`: 26-char Crockford Base32 client ULID minting.
   - Writes never claim success offline: immediate transition to "در صف ارسال" (queued state).
   - Replay triggered automatically on `online`, app load, and Background Sync (`replay-outbox`).
2. **Outbox Service** (`src/shift/services/outbox.py`):
   - Server-side validation against `domain/events.py` and `domain/payloads.py`.
   - Idempotent event append via `EventStore`.
3. **Events Ingest Endpoint** (`src/shift/api/routes_events.py`):
   - `POST /api/v1/events` validates payload, calls `event_store.append(...)`.
   - Idempotent replay returns existing `seq` with HTTP 200.
   - Bad payload returns HTTP 422.
   - Auth dependency guard wired via `api/deps.require` (with injected fallback dependency).
4. **App Integration** (`src/shift/app.py`):
   - Minimally mounted static assets (`/static`) and included `routes_events` and `routes_web` routers per coordinator instruction.

## Verification & Test Results

1. **JS Unit Tests** (`tests/js/outbox.test.js`):
   - Executed via `node --test tests/js/outbox.test.js` (no `package.json`).
   - 8 tests passing: backoff calculation, jitter bounds, 10-try failure threshold, ULID monotonicity.
2. **API & PWA Integration Tests** (`tests/integration/test_idempotency_api.py`):
   - Executed via `pytest`:
     - `test_idempotent_event_replay_twice_creates_single_record`: passes.
     - `test_event_submission_with_bad_payload_returns_422`: passes.
     - `test_auth_guard_blocks_unauthorized_requests`: passes.
     - `test_pwa_webmanifest_and_sw_headers`: passes.
3. **Playwright E2E Test** (`tests/e2e/test_offline_outbox.py`):
   - Executed with Chromium: offline submission queues in outbox -> UI confirms queued state -> restore connection -> replay drains queue -> exactly 1 event recorded in DB -> second replay converges.
4. **Suite Regressions & Quality Gates**:
   - `ruff check .` -> clean (0 errors).
   - `ruff format --check .` -> clean (64 files formatted).
   - Full test suite: 146 passed in 14.69s.
   - Domain coverage: 100% (gate: >= 95%).
