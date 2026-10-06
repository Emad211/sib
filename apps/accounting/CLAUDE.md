# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Hesabdari Sib**: a single-clinic accounting and reception desktop app built on Flask, SQLite and Jinja, served on port **8080**. It covers reception (patients, invoices, visits, injections/nursing services, procedures, consumables, per-item payments), a doctor room, and a manager panel (reports, tariffs, insurance arrears, payroll, users, activity logs). The UI is Persian, RTL and Jalali. Most comments and all UI strings are Persian.

## Commands

Run all commands from `webapp/`. Dependencies are listed in `requirements.txt` (flask, werkzeug, bcrypt, jdatetime, pytest).

```powershell
python start.py                          # http://127.0.0.1:8080 (or run.bat); opens a browser after ~1.5s
python -m pytest tests                   # full suite
python -m pytest tests/test_billing_characterization.py::<test_name>   # single test
flask --app src.app init-db              # re-run schema.sql against the configured DB
flask --app src.app create-user <username> <password> [manager|reception|doctor]
pyinstaller HesabdariSib.spec            # single-file HesabdariSib.exe (console=False)
```

Helper scripts live in `scripts/`: `create_doctor_user.py` and `seed_clinic_data.py`. No linter or formatter is configured.

## Data safety: the real DB is hard-wired

`core.get_db()` reads the **class attribute** `Config.DATABASE_PATH`, which always points to `webapp/clinic_new.db`. It does **not** read `app.config`, so passing `DATABASE_PATH` in `test_config` has no effect on repositories. `clinic_new.db` holds real clinic data and is committed to git, as are `dist/`, `build/` and the `.spec` file, even though `.gitignore` lists them.

- Tests must use the `webapp_db` fixture in `tests/conftest.py`. The fixture patches `Config.DATABASE_PATH` to a temp file and resets `core._migrations_done` **before** `create_app`/`get_db`. A session-scoped autouse guard SHA-256 checks `clinic_new.db` and fails the run if any byte changed.
- Seed test data with raw inserts into `webapp_db` (see the helpers at the top of `test_billing_characterization.py`).
- `TESTING=True` skips the backup scheduler and the `FLASK_ENV` handling.
- `test_billing_characterization.py` contains **golden-master** tests. They pin the *current* billing behavior, not the intended behavior. A failure there means money logic changed and needs review. Do not "fix" the expectation.

## Architecture

```
start.py → src/app.py:create_app()       registers blueprints, Jinja filters, CLI, scheduler
src/api/*.py          Blueprints: auth (/auth), dashboard (/), reception (/reception), doctor (/doctor), manager (/manager)
src/services/         auth_service, reception_service, activity_logger, scheduler
src/adapters/sqlite/  core.py (connection + schema + migrations) and one repo per aggregate
src/common/           jalali.py, utils.py (iran_now, shift/work_date helpers), validators.py
src/templates/<role>/ Jinja pages per role; static/ holds vendored Vazirmatn, chart.min.js, jalaali.min.js (offline, no CDN)
```

**The layering is aspirational, not enforced.** `api/manager.py` (~3.7k lines) and `api/reception.py` (~1.9k lines) run hundreds of `db.execute` calls inline, and several `services/` and `domain/` files are empty stubs. New code should go route → service → repo, with SQL in `src/adapters/sqlite/`. Read the existing route before you assume a repo owns a query; many report and payroll queries live only in `manager.py`.

### Auth and roles
- Roles are `manager`, `reception` and `doctor`. The login form posts a `role`, and `AuthService` validates the user against that role.
- There is a single `login_required` decorator (`api/auth.py`). Manager and doctor routes check `g.user['role']` **inline** and redirect when it doesn't match. Copy that check on new manager routes.
- Passwords are hashed with bcrypt. Legacy werkzeug hashes are migrated to bcrypt on a successful login. After 5 failed attempts the account locks for 15 minutes (`locked_until`).
- `PRODUCTION=1` (or `FLASK_ENV`/`APP_ENV=production`) does three things: it refuses to start with the default `SECRET_KEY`, it sets secure cookies, and it binds to 127.0.0.1. Otherwise the app binds to `0.0.0.0` for LAN use. `tests/test_security_config.py` pins this behavior.

### Shifts and `work_date` (core domain concept)
- Shifts (`morning`/`evening`/`night`) are **switched manually** per user and stored in `user_active_shift`. They are not derived from clock time.
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
- `src/adapters/sqlite/schema.sql` is idempotent (`IF NOT EXISTS`). `get_db()` loads it only when the `users` table is missing. It looks for the file via `pkgutil`, then next to the module, then `_MEIPASS`, then the exe directory.
- Schema changes to existing DBs are additive runtime migrations in `core.py`: `_ensure_column`, backfills, `_ensure_indexes`, `_ensure_settings_table`. They run **once per process**, guarded by the module flag `_migrations_done`, and they swallow errors. Add new migrations there, and add the column or table to `schema.sql` too.
- If you add a bundled data file, also add it to `datas` in `HesabdariSib.spec`. In frozen mode the DB and `backups/` sit next to the exe; templates and static files come from `sys._MEIPASS`. Keep both code paths when you edit `app.py`, `settings.py` or `start.py`.

### Conventions
- **Time:** store Tehran local time with `utils.iran_now()` or SQLite `datetime('now','+3 hours','+30 minutes')`. Never use naive `datetime.now()` or UTC.
- **Dates:** Jalali (`YYYY/MM/DD`) in the UI, converted to Gregorian on the server. Templates use the Jinja filters `jalali_datetime`, `jalali_local` and `fa_num` (Persian digits plus `،` thousands separator).
- **Activity logging:** log state-changing actions with `services/activity_logger.log_activity(...)`, using the `ActionType`/`ActionCategory` constants. The manager can view these at `/manager/logs`.
- **Backups:** `services/scheduler.py` runs a daemon thread that copies the DB into `backups/` weekly (Saturday 03:00) and keeps the last 4 copies.
