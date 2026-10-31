# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Shift app (اپ شیفت سیب)**: a FastAPI + SQLAlchemy Core service on SQLite (WAL) with an installable Persian RTL PWA. A shift starts when one person takes over from the previous one: blind counts of the cash box and shelves, checklists, parcels in transit and a two-sided signature write an append-only event log, and stock, cash and discrepancies are projections of that log. It serves the clinic LAN on port **8443** behind Caddy with an internal CA for HTTPS (ADR-015) — port 8080 belongs to the accounting app. Dates are Jalali, every timestamp is Tehran time (UTC+03:30). The accounting database is read-only, through the bridge (`mode=ro` + `PRAGMA query_only`, versioned `v_shift_*` views, S04).

> Project-wide rules and roadmap: `../../CLAUDE.md` and `../../docs/00_README.md` (read them first). Clinic behaviour is `../../docs/shift/S01_PRODUCT_LOGIC.md`; architecture and domain model are S02/S03; the plan is S05.

## Commands

Run everything from `apps/shift`. On this Windows host a `GTK3-Runtime` entry on PATH shadows the venv's Python and ruff DLLs, so strip it first:

```powershell
$env:PATH = ($env:PATH.Split(';') | Where-Object { $_ -notmatch 'GTK' }) -join ';'
```

```powershell
./dev.ps1 setup     # create .venv and install requirements-dev.lock
./dev.ps1 test      # what CI runs: ruff check, ruff format --check, pytest, coverage gates, node:test
./dev.ps1 lint      # ruff check
./dev.ps1 fmt       # ruff format
./dev.ps1 run       # uvicorn on 127.0.0.1:8443 (SHIFT_HOST / SHIFT_PORT)
./dev.ps1 verify    # event hash chain of SHIFT_DB_PATH (default ./shift.db)
./dev.ps1 rebuild   # rebuild projections from the event log
```

- One module: `./.venv/Scripts/python.exe -m pytest tests/integration/test_device_enrollment.py -q`. **Run pytest from `apps/shift`**: `pytest.ini` sets `pythonpath = src tests/data tools` and some tests call `tools/*.py` by relative path.
- Settings come from `SHIFT_*` variables (`src/shift/config/settings.py`; `SHIFT_ENV` defaults to `production`, tests that use plain HTTP pass `environment="development"`): `SHIFT_HOME`, `SHIFT_DB_PATH`, `SHIFT_LOG_DIR`, `SHIFT_AUDIT_LOG`, `SHIFT_SECRET_FILE`, `SHIFT_HOST`/`SHIFT_PORT`, `SHIFT_HASH_CHAIN`, `SHIFT_ACCOUNTING_DB`, `SHIFT_BACKUP_*`, `SHIFT_BACKGROUND_JOBS`.
- `shift.db`, `logs/`, `backups/` and `.shift_secret_key` are gitignored; CI fails if a `*.db` file is tracked (ADR-006).

## Folder map

```
start.py                       uvicorn entry point: create_app(Settings.from_env())
src/shift/app.py               app factory and lifespan wiring (database, events, projections, auth, audit, devices, catalog, settings, scheduler)
src/shift/api/                 routes_auth, routes_devices, routes_events, routes_catalog, routes_settings, routes_web, routes_manager; deps.py holds the session, CSRF and require(permission) guards
src/shift/domain/              pure rules: policy, cutoff, money, permissions, pin_policy, shift_clock, event_hash, payloads, daily_card
src/shift/services/            event_store, event_chain, projections, auth, devices, audit, catalog, settings, daily_card, scheduler, snapshot, backup, outbox, accounting_bridge
src/shift/adapters/db/         SQLAlchemy Core connection and one repo per aggregate (card_repo for the daily card); migrations m0001.. (newest m0011_daily_cards); schema.sql, sqlite/ddl.sql, sqlite/triggers.sql
src/shift/adapters/accounting/ read-only bridge (mode=ro + PRAGMA query_only) and the v_shift_* reads
src/shift/common/              clock (Tehran), jalali, ids (ULID), hashing
src/shift/templates|static/    Jinja pages incl. manager_*.html, PWA shell, service worker, vendored Vazirmatn — no CDN
tests/                         unit, integration, contract, property, security, e2e, data (synthetic fixtures), js/outbox.test.js
tools/                         operator CLIs: backup_now, verify_event_chain, rebuild_projections, restore_check, seed_reference_data, make_synthetic_acc_db, print_enroll_qr, create_staff (first manager, PIN asked at a prompt)
```

## Hard rules

- **The reference number never leaves the server.** Blind-count targets are compared server-side; the API answers only `match` / `recount` / `escalate_outgoing` / `joint`, and the number may appear only in `audit_log` and `logs/audit.log` — never in a normal log, a response or an error (ADR-011, R-02). A test that searches every response for the reference numbers is mandatory.
- **Accounting is read-only.** Reach it only through `adapters/accounting/readonly.py` (`mode=ro` plus `PRAGMA query_only`); never write to it and never change its schema from here (ADR-002, ADR-004, S04). Tests use the synthetic accounting DB from `tools/make_synthetic_acc_db.py`.
- **Events are append-only.** Never `UPDATE` or `DELETE` `events`; a correction is a new event with `ref_event_id`, and triggers reject the rest (ADR-010). Every projection must be rebuildable: `rebuild` has to equal the live state.
- **No real patient data** here, in tests, CI, logs or any external service: synthetic data only (ADR-006). Never open, copy or query `_data/` or a production database copy.
- **Migrations go forward only and are additive:** add `mNNNN_*` under `adapters/db/migrations/`, register it in `MIGRATIONS`, never edit an applied migration. The runner snapshots the database before the first pending step.
- **Secrets stay out of files and logs:** PINs are argon2id digests, the session secret is a per-install file, enrollment tokens are stored only as SHA-256. Never log a PIN, token or secret.
- New architecture decisions go to the owner through the coordinator; clinic behaviour comes from S01 and is never invented in code.

## Testing (lean policy)

Owner decision, 2026-10-31 (ADR-017, item 5). Ship fast, test what can hurt the clinic:

- Full depth (unit, property, integration, security) only where a bug costs money, leaks the reference number, loses events, breaks auth or breaks a migration.
- Everything else: one integration test on the behaviour through the service or route. No tests of getters, wiring, templates or framework behaviour, and none added just to raise coverage.
- E2E: one Playwright journey per user flow, not per case.
- The only coverage gates are the existing CI ones (95% domain, 85% total). Do not add a target or a test kind because it is on a list.
