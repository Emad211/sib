# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Hesabdari Sib**: a single-clinic accounting and reception desktop app built on Flask, SQLite and Jinja, served on port **8080**. It covers reception (patients, invoices, visits, injections/nursing services, procedures, consumables, per-item payments) and a manager panel (attention dashboard, reports with CSV/Excel export, tariffs, insurance arrears, payroll, users, activity logs, settings and backups). The UI is Persian, RTL and Jalali. Most comments and all UI strings are Persian.

> Project-wide rules and the roadmap: `../../CLAUDE.md` and `../../docs/00_README.md` (read them first).
> This app lives in the `sib` monorepo since 2026-10-26 (ADR-001); `webapp/` paths below now mean `apps/accounting/`.

## Commands

Use `./dev.ps1 setup|test|test-prod|golden|synth|run|build` (see docs/engineering/E03). Dependencies are pinned in `requirements.lock` / `requirements-dev.lock`.

```powershell
python start.py                          # http://127.0.0.1:8080 (or run.bat); opens a browser after ~1.5s
python -m pytest tests                   # full suite
python -m pytest tests/test_billing_characterization.py::<test_name>   # single test
flask --app src.app init-db              # re-run schema.sql against the configured DB
flask --app src.app create-user <username> <password> [manager|reception]
pyinstaller HesabdariSib.spec            # single-file HesabdariSib.exe (console=False)
```

`scripts/seed_clinic_data.py` seeds sample data. Lint with `ruff check .` (CI runs it); `ruff.toml` lists the files held to the new standard, so add each cleaned file there.

## Data safety: the real DB is hard-wired

`core.get_db()` reads the **class attribute** `Config.DATABASE_PATH`, which always points to `clinic_new.db` next to the app. It does **not** read `app.config`, so passing `DATABASE_PATH` in `test_config` has no effect on repositories. No `.db` file is tracked in the sib repo (CI enforces it); real data only ever lives in `sib/_data/` (ADR-006).

- Prefer the `synthetic_app` fixture (a private copy of a production-shaped synthetic DB, `tools/make_synthetic_db.py`) and `login_as(client, app, username)` for route-level tests.
- `tests/test_route_matrix.py` pins every route × role outcome in `tests/route_matrix.json`; `tests/test_prod_snapshot.py` (marker `prod_snapshot`, local only) is the production-copy gate.

- Tests must use the `webapp_db` fixture in `tests/conftest.py`. The fixture patches `Config.DATABASE_PATH` to a temp file and resets `core._migrations_done` **before** `create_app`/`get_db`. A session-scoped autouse guard SHA-256 checks `clinic_new.db` and fails the run if any byte changed.
- Seed test data with raw inserts into `webapp_db` (see the helpers at the top of `test_billing_characterization.py`).
- `TESTING=True` skips the backup scheduler and the `FLASK_ENV` handling.
- `test_billing_characterization.py` contains **golden-master** tests. They pin the *current* billing behavior, not the intended behavior. A failure there means money logic changed and needs review. Do not "fix" the expectation.

## Architecture

```
start.py → src/app.py:create_app()       registers blueprints, Jinja filters, CLI, scheduler
src/api/*.py          Blueprints: auth (/auth), dashboard (/), reception (/reception); src/api/manager/ (package, /manager)
src/services/         auth_service, reception_service, activity_logger, backup, scheduler, exports, manager_insights
src/web/csrf.py       CSRF for every state-changing request (token injected by static/js/csrf.js)
src/adapters/sqlite/  core.py (connection + schema), migrations/ (versioned), and one repo per aggregate
src/common/           jalali.py, utils.py (iran_now, shift/work_date helpers), validators.py
src/templates/<role>/ Jinja pages per role (auth, reception, manager); static/ holds vendored Vazirmatn, chart.min.js, jalaali.min.js (offline, no CDN)
```

**The layering is aspirational, not enforced.** The `api/manager/` package (split from the old 3.7k-line `manager.py`, ACC-2.6; modules: dashboard, reports, exports, arrears, tariffs, users, payroll, logs, settings, `_common`) and `api/reception.py` (~1.9k lines) run hundreds of `db.execute` calls inline, and several `services/` and `domain/` files are empty stubs. New code should go route → service → repo, with SQL in `src/adapters/sqlite/`. Read the existing route before you assume a repo owns a query; many report and payroll queries live only in `api/manager/*.py` (e.g. `reports.py`, `payroll.py`).

### Auth and roles
- Roles are `manager` and `reception` (`USER_ROLES` in `auth_service.py`). The old `doctor` login was retired (ADR-013): doctors exist only as `medical_staff` rows, and a session or account with any other role is rejected. The login form posts a `role`, and `AuthService` validates the user against that role.
- There is a single `login_required` decorator (`api/auth.py`). Manager routes are guarded by `manager_required` (HTML: redirect to reception, optional `flash_message=`) or `manager_api_required` (JSON/CSV: 403, optional `body=`) from `api/manager/_common.py`, applied below `@login_required`; `tests/test_manager_authz.py` fails if a `manager.*` route lacks one. Report date ranges share `_common.jalali_to_start_end`. (A general `@require_role` is still planned, ACC-2.1.)
- Every POST/PUT/DELETE needs the CSRF token. Pages get it automatically; tests use `CsrfClient`.
- Passwords are hashed with bcrypt. Legacy werkzeug hashes are migrated to bcrypt on a successful login. After 5 failed attempts the account locks for 15 minutes (`locked_until`).
- The session secret is a per-install file `.hesabdari_secret_key` next to the DB (created on first start, never committed). `PRODUCTION=1` (or `FLASK_ENV`/`APP_ENV=production`) sets secure cookies and binds to 127.0.0.1; otherwise the app binds to `0.0.0.0` for LAN use. `SIB_HOST` overrides the bind address in both run paths (`start.py`, `src/app.py`) when it is a valid IPv4 — see `resolve_listen_host()` in `src/config/settings.py` and A07 §6 (plain HTTP, private network only). `tests/test_security_config.py` pins the cookie behavior; `tests/test_listen_host.py` pins the bind rule.

### Shifts and `work_date` (core domain concept)
- Shifts (`morning`/`evening`/`night`) are stored per user in `user_active_shift`. A person **starts** the shift the schedule says (`domain/shifts.py`: 07:30 / 13:30 / 19:30, settings-overridable, 30-min early takeover) via `services/shift_handover.py`; it is refused while an invoice of another shift is open, and nobody is ever switched automatically (late handovers keep working; a stale shift cannot open new invoices).
- `reception.py`'s `@bp.before_app_request` runs on *every* request. It fills `g.user_shift_status` (active_shift, work_date, shift_started_at, open_invoices_count, …).
- `utils.get_work_date_for_datetime()` and `get_current_shift_name()` read from that `g` value.
- Operational rows (`invoices`, `visits`, `injections`, `procedures`, `consumables_ledger`) store `work_date` (and invoices also store `shift`). This keeps a night shift that crosses midnight on one date. Reports and ledgers filter on `work_date`, **not** `DATE(timestamp)`.

### Billing model (`invoices_repo.py`)
- An invoice is `open` → `closed`. Items are visits, injections (nursing services), procedures and consumables, each linked by `invoice_id`, with doctor/nurse IDs pointing at `medical_staff`.
- `get_invoice_items()` **computes** the shares on the fly:
  - **Visits:** the recorded price is the base tariff (`is_base_tariff`, or `آزاد`). The patient share comes from the insurance tariff and is overridden by the supplementary-insurance tariff when there is one.
  - **Nursing items:** covered when the insurance has `nursing_covers` (with legacy fallback `nursing_tariff == 0`), unless the item is listed in `insurance_nursing_exclusions`.
  - **Procedures:** nurse procedures follow the same coverage rule as nursing items.
  - **Consumables:** always paid by the patient.
- `invoices.total_amount` is the sum of `patient_share`. `update_invoice_totals`/`close_invoice` write that total.
- **Consumables are excluded from revenue**: revenue = visits + injections + procedures. Keep that rule in every report.
- Payments are tracked per item in `invoice_item_payments` (`payment_type` card/cash, `is_paid`).

### Schema and migrations
- `src/adapters/sqlite/schema.sql` is idempotent (`IF NOT EXISTS`). `core._prepare()` loads it only when the `users` table is missing, then runs the versioned migrations.
- Migrations live in `src/adapters/sqlite/migrations/` (`m0001_baseline`, `m0002_performance_indexes`, ...), tracked by `PRAGMA user_version`. Each step runs in its own transaction after a `pre_migration` backup; a failure raises `MigrationError` and the app does not start. **Schema changes are additive only** (ADR-002): the previous exe must still run on a migrated DB. Add a new `mNNNN_*.py`, register it in `MIGRATIONS`, and add the column or table to `schema.sql` too.
- If you add a bundled data file, also add it to `datas` in `HesabdariSib.spec`. In frozen mode the DB and `backups/` sit next to the exe; templates and static files come from `sys._MEIPASS`. Keep both code paths when you edit `app.py`, `settings.py` or `start.py`.

### Conventions
- **Time:** store Tehran local time with `utils.iran_now()` or SQLite `datetime('now','+3 hours','+30 minutes')`. Never use naive `datetime.now()` or UTC.
- **Dates:** Jalali (`YYYY/MM/DD`) in the UI, converted to Gregorian on the server. Templates use the Jinja filters `jalali_datetime`, `jalali_local` and `fa_num` (Persian digits plus `،` thousands separator).
- **Activity logging:** log state-changing actions with `services/activity_logger.log_activity(...)`, using the `ActionType`/`ActionCategory` constants. The manager can view these at `/manager/logs`.
- **Backups:** `services/backup.py` (SQLite online backup + integrity check, safe names, free-disk guard) and `services/scheduler.py` (daily after 04:00 and at startup if the newest is older than 20 h). Retention is 7 daily / 4 weekly / 6 monthly. The manager dashboard warns when the newest backup is older than 24 h.
- **Exports:** every report export goes through `services/exports.py` (`table_response`): CSV with BOM and formula neutralisation, or `?format=xlsx`. Never write CSV by hand in a route.
- **Manager dashboard rules:** revenue = visits + nursing + procedures of closed invoices; consumables are never revenue (owner decision, Q-05). Thresholds for the attention list come from real data and are documented in `docs/accounting/A06_MANAGER_AREA_SPEC.md` §6.
