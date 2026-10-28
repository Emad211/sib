# Shift skeleton — design steps 1-5 (task_71074e3e7c5a)

Date: 2026-10-28 · Branch: `Emad211/shift-skeleton` · Base: `b148152`
Worktree: `C:/Users/Emad Karimi/orca/workspaces/sib/shift-skeleton`

## Commits

| # | Hash | Step |
|---|---|---|
| 1 | `b3e1a98` | skeleton, `/healthz`, pinned locks, CI with no-DB guard |
| 2 | `56dd50c` | WAL database core, migration runner, baseline schema, backup |
| 3 | `c7b28b4` | clock, Jalali, ULID, hashing, settings, public/audit logging |
| 4 | `e861710` | append-only event log, registry, hash chain, idempotency, verify CLI |
| 5 | `cfe9cad` | stock/cash projections, rebuild, rebuild CLI, property tests |
| — | `83b85c2` | integer-Toman contract test and adapter typing cleanup |

No work from steps 6+ (auth, sessions, devices, accounting bridge, PWA) is present.

## What exists

- `apps/shift/src/shift/` with `config/`, `common/`, `domain/`, `services/`, `adapters/db/`, and no
  `api/` routers yet beyond `/healthz` in `app.py`.
- `tools/verify_event_chain.py` and `tools/rebuild_projections.py`.
- Tests: unit, contract, integration, property, security; 141 tests total.
- CI: `.github/workflows/shift.yml` runs the no-`*.db` guard, ruff check/format, pytest with
  coverage, and the domain ≥95 % gate.

## Design reconciliation (deliberate, documented)

1. **S3 names vs design names.** The design's `event_id` is the offline-minted client ULID, so it is
   the S3 `client_event_id` in meaning. The event table keeps the design column `event_id` and adds
   `client_event_id` as a generated virtual alias; `server_at` is the S3 authoritative UTC instant
   and `at_utc` is the design name as a generated alias. No duplicate storage.
2. **Two clocks.** `client_at` preserves the device clock; `server_at` (UTC) and `at_tehran`
   (fixed +03:30) are server-authoritative. No naive datetimes are accepted.
3. **Idempotency.** `idempotency_key` is `NOT NULL UNIQUE`, defaults to the client event id, and a
   retry returns the original row with `created=False`; a batch duplicate inserts once.
4. **Baseline evolution before release.** Step 2's `m0001_baseline` started with the projection
   tables; step 4 extended the same baseline to execute `sqlite/ddl.sql` and `sqlite/triggers.sql`.
   This is safe because no shift database has been deployed; the v1 baseline is not frozen yet.
   Future changes must use `m0002+`, never edit v1 after release.
5. **Projection vocabulary (11 event types).** Cash: `handover.started` (N0 + opening count),
   `handover.cash_counted`, `cash.withdrawn`, `cash.deposited`, `acc.cash_receipt`. Stock:
   `handover.item_counted`, `stock.weekly_count`, `stock.shelf_restocked`, `acc.consumption`,
   `stock.emergency_withdrawal`, `stock.waste_reported`. Counts set the baseline; deltas add/subtract.
6. **Single writer (S2).** Read engine plus a write engine with `QueuePool(pool_size=1,
   max_overflow=0)`, `BEGIN IMMEDIATE` per batch, and `busy_timeout=5000`; every connection sets
   `journal_mode=WAL` and `synchronous=NORMAL`. Cross-process contention waits in SQLite; a FastAPI
   retry middleware is deferred because there is no write route yet (step 6+).
7. **Hash chain.** `sha256(prev_hash ‖ canonical_json(fields without seq/hash))`; `SHIFT_HASH_CHAIN`
   defaults on and is wired into the app's `EventStore`. `verify_event_chain.py --no-links` exists
   for a deliberately chain-off database.
8. **Projection rebuild.** `sync()` is incremental and per projection (a lagging projection cannot
   double-apply the other). `rebuild()` replays into `proj_*__rebuild`, compares checksums, and
   swaps in one transaction; dry-run uses a rollback so it never mutates live tables. Incremental
   sync clears `meta_projection.checksum`; a successful rebuild writes checksum and `built_at`.
9. **Domain purity.** `domain/` is AST-checked for IO/framework/adapter imports and `open()`, and
   every domain file is ≤150 lines (largest is 96).

## Verification evidence

Run from `apps/shift` with `.venv/Scripts/python.exe`:

```text
ruff check .                 -> All checks passed!
ruff format --check .        -> 57 files already formatted
coverage run -m pytest       -> 141 passed in 8.05s
coverage report --include="*/shift/domain/*" --fail-under=95 -> 100% (137 stmts)
coverage report --include="*/shift/*" -> 96% total (844 stmts, 24 missed)
```

CLI acceptance on a synthetic 500-event temp database (never a real DB):

```text
tools/verify_event_chain.py --db <test.db>        -> chain ok: 500 events (exit 0)
tools/rebuild_projections.py --db <test.db>       -> projections ok: 500 events (exit 0)
# injected projection drift: UPDATE proj_stock SET qty = qty + 1
tools/rebuild_projections.py --db <test.db>       -> drift detected: proj_stock (exit 1)
tools/rebuild_projections.py --db <test.db> --apply -> repaired (exit 0)
# injected event tamper: DROP TRIGGER events_no_update; UPDATE events ...
tools/verify_event_chain.py --db <test.db>        -> chain broken at seq 1: hash ... (exit 1)
```

The 10 000-event property test (`tests/property/test_rebuild_equals_live.py`) asserts
`rebuild == live` row by row and checksum by checksum.

## Open risks / next

- **Handover flow**: `proj_cash` takes N0 from `handover.started.opening_cash`; SHF-2.1 must emit it
  only after the outgoing blind count settles. The Q-09 second-count assumption remains as designed.
- **Auth**: the event store trusts caller `actor_id`/`device_id`; login, device binding and clock-skew
  warning are step 7/8, not implemented here.
- **API orchestration**: `app.state.events` and `app.state.projections` exist, but no route appends
  and syncs yet; step 6+ must call `projections.sync()` after append (or wire both in one service).
- **Retry middleware**: busy_timeout + single writer cover current load; add the S2 exponential
  retry with the first write API if measurements demand it.
- **Triggers are not immutable**: a DB owner can drop them; O01 backups plus the chain audit remain
  the real control (as the design states).
- Next design steps: 6 permissions/route matrix, 7 auth/sessions/CSRF, 8 device enrollment,
  9-10 accounting read bridge/watermark, 11 settings/catalog, 12-13 PWA/outbox, 14 backup/audit,
  15 hardening/docs.

## Safety

No `_data/`, real `*.db`, `backups/`, or `logs/` file was opened or committed. Every test fixture and
CLI acceptance database was synthetic and lived under pytest/temp paths. `git ls-files` contains no
database files.
