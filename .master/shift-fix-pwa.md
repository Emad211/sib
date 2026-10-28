# Shift PWA review fixes — `review-pwa.md` (B1 + S1–S13)

Date: 2026-10-28 · Branch: `Emad211/shift-pwa` · Base HEAD: `6d700a1`
Worktree: `C:/Users/Emad Karimi/orca/workspaces/sib/shift-pwa`
Scope: the PWA slice only (`static/**`, `templates/{base,home}.html`, `api/routes_web.py`,
`api/routes_events.py`, `api/principal.py`, `services/outbox.py`, their tests, `shift.yml`).
No `_data/`, `backups/`, `logs/` or `*.db` was opened; every test database is synthetic
under `tmp_path`.

## Verdict

B1 and S1–S13 are fixed with tests that fail without the change. Local gates, run from
`apps/shift` with the GTK3-Runtime entries stripped from `PATH`:

| Command | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `68 files already formatted` |
| `pytest -q` | `187 passed` (includes the 4 Playwright tests) |
| `pytest tests/e2e -v` | `4 passed` (run twice for stability) |
| `node --test tests/js/outbox.test.js` | `16 passed` |
| `coverage report --include="*/shift/domain/*" --fail-under=95` | 100% |

## BLOCKER B1 — fail-closed, principal-owned event ingest

`api/principal.py` (new, owned contract):

- `Principal(actor_id, device_id, shift_id=None, work_date=None)`.
- `PrincipalGuard` Protocol; `as_principal()` coerces a guard result and accepts a session
  object exposing `actor_id`/`staff_id`, `device_id`, `shift_id`, `work_date`.
- `require_principal()` FastAPI dependency: **no guard installed → 503**; guard returns
  `None` → 401; wrong object → 500; async guards are awaited.
- `install_principal_guard(app, require(Permission.EVENTS_APPEND))` is the one-line wiring
  for the coordinator; `app.dependency_overrides[require_principal] = require(...)` is also
  supported and tested with a session object.
- CSRF constants/helpers live here too (`shift_csrf`, `X-CSRFToken`, double-submit check).

`api/routes_events.py`:

- `EventIn` no longer owns identity: `actor_id`/`device_id`/`shift_id` are accepted only so a
  PWA build cached before this deploy does not 422, and every value is discarded. The route
  builds the submission from the `Principal`.
- `extra="forbid"` catches typos; identifiers are capped at 128 chars; a route class rejects
  a declared body above 64 KiB **before** FastAPI parses it (chunked bodies remain the reverse
  proxy's responsibility).
- Unsafe POST requires the double-submit CSRF header; responses are `no-store`/`nosniff`.

`services/outbox.py`:

- `work_date` is derived from `Principal.work_date` when the session carries it, otherwise from
  the explicit Tehran business-date helper (`business_date_for`): the night shift that crosses
  midnight belongs to its opening date. A client-supplied `work_date` must match; a backdated
  value is 422.
- After every append (including replay) the service runs `ProjectionEngine.sync()`, so the only
  production write path no longer leaves `proj_stock`/`proj_cash` stale.

Tests (`tests/integration/test_idempotency_api.py`): 503 without a guard, 401 for anonymous /
`None`, 500 for a wrong guard result, async guard, session-object coercion, one-line dependency
override, forged body identity ignored, CSRF missing/mismatch, `idempotency_key` wins over a new
client ULID, nine parametrized invalid submissions, night-shift work-date derivation, backdating
rejected, principal work date authoritative, projection update without double apply, 413 body cap.

## SHOULD fixes

| # | Fix | Test |
|---|---|---|
| S1 | Precache both `/` and `/?src=pwa`; navigation lookup uses `ignoreSearch:true`. | `test_manifest_start_url_is_precached...`; e2e cold offline launch of both URLs |
| S2 | After enqueue the UI always says "در صف ارسال"; a sent state is shown only from a validated `outbox:sent` event. | e2e aborted-POST test asserts queued and never "همگام" until success |
| S3 | `triggerReplay()` calls `replayOutbox()` directly; Background Sync registration is best-effort with a 3 s `serviceWorker.ready` timeout. | e2e with `service_workers="block"` still posts and retries |
| S4 | `scheduleNextReplay()` keeps one `setTimeout` for the earliest `next_try_at`; SW sync only nudges open clients (page-only limitation documented in `sw.js`). | e2e retry without reload/online event; node `nextRetryDelayMs` tests |
| S5 | A 2xx deletes an item only after the JSON has `seq`, matching `event_id`, boolean `created` and `status` recorded/replayed. 400/404/410/422 are terminal; 401/403/408/409/425/429/5xx retry. | node validation/classification tests; e2e 200-HTML captive-portal test keeps the item |
| S6 | Server issues `shift_csrf` on `GET /` and validates `X-CSRFToken` on POST (missing/mismatch → 403). Header spelling matches accounting. | CSRF integration tests; `csrf.js` header test |
| S7 | Workflow now has `js` (node:test) and `e2e` (`playwright install chromium`) jobs; the `e2e` marker is registered in `tests/conftest.py`. | `tests/integration/test_ci_gates.py` |
| S8 | Projection sync on ingest/replay. | integration projection test; e2e asserts `proj_stock` |
| S9 | Production `/` is a shell (no demo identities, no handover offline); the dev form is gated by `SHIFT_ENV=development`, collects no identity and only offers current-shift stock events. The outbox rejects non-allowlisted types and non-events URLs. | `test_pwa_shell.py`; node allowlist tests |
| S10 | Separate `--color-primary-button`/hover tokens keep white button text ≥4.5:1 in both themes; `color-scheme: light dark`; `100dvh` and safe-area padding; 44px brand link. | `tests/unit/test_pwa_styles.py` |
| S11 | Each precache URL is added individually with `Promise.allSettled`; a missing asset fails install loudly instead of silently caching nothing. | test GETs every precache URL and asserts no `cache.addAll` |
| S12 | Removed the SW's synthetic "queued" 503 POST branch. | source assertion + e2e offline POST must fail |
| S13 | Client `tehranBusinessDate()` mirrors the server helper; server derives/validates `work_date`. | node date tests; integration night-shift/backdate tests |

## NICE items

Fixed: 1 (queued/failed counts, real last-sync time), 2 (URL allowlist), 3 (`tx.oncomplete`
durability), 4 (`storage.persist()`), 5 (`dvh`/safe-area), 6 (44px brand), 7 (precache every
declared font weight incl. Medium), 8 (`__version__` injected into the SW cache name),
9 (inline JS moved to `home.js`/`offline.js`; CSP + baseline headers on shell routes),
10 (extra=forbid, max lengths, body cap), 12 (dead SW branch removed), 14 (ephemeral port,
explicit selectors, try/finally, thread join, no `networkidle`), 18 (`id`, `display_override`).

Not fixed / left for their owners: 11 (one csrf client still by design), 13 (`outbox-idb.js`
still mixes pure and runtime code; the pure seam is exported and unit-tested), 15 (the old
`.master/shift-pwa.md` overclaims are superseded by this report; that file is outside this
task's ownership), 16 (no `app.py` edit), 17 (`Settings.host` default is `app.py`/config scope).

## Disagreements / explicit choices

- The review's S4 offered "SW drains IndexedDB itself **or** keep the page-only limitation
  explicit". I kept the page-only limitation: the page runs the real replay immediately, a
  timer owns backoff, and the SW sync handler only messages open clients. This avoids a second
  copy of the replay/idempotency logic in the worker.
- The review's S9 offered replacing the demo form or gating it behind `SHIFT_ENV=development`.
  I gated it and additionally removed all identity inputs and handover event types, so even the
  dev form cannot forge actor/device/shift.
- S7's `requirements-dev.lock` and `dev.ps1` changes are outside the stated PWA-slice ownership.
  The CI e2e job pins/install Playwright explicitly, and the marker is registered in the owned
  `tests/conftest.py`. Follow-up for the tooling lane: add `playwright==1.64.1` to the lock and
  `node --test tests/js/outbox.test.js` to `dev.ps1 test`.

## Coordinator wiring

In `create_app` (shared `app.py` hunk):

```python
from shift.api.deps import require
from shift.api.principal import install_principal_guard

install_principal_guard(app, require(Permission.EVENTS_APPEND))
```

Until that line lands, `POST /api/v1/events` returns 503 — never anonymous. The route also
accepts `app.dependency_overrides[require_principal] = require(...)`; `as_principal` coerces a
session object with `staff_id`/`device_id`/`shift_id`/`work_date` into the `Principal`.

## Not done

`git push` was not run. No repository database was opened. The old `.master/shift-pwa.md` was
not edited (ownership); this file is the factual status for the fix dispatch.
