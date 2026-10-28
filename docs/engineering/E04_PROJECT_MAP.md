# Sib Clinic Platform — Project Map

## 1. Platform Purpose
Monorepo for Sib 24-Hour Clinic (`درمانگاه شبانه‌روزی سیب`). Key components:
- **Accounting & Reception Desktop App (`apps/accounting/`)**: Offline Flask/SQLite application (port 8080) for reception, billing, tariffs, visits, procedures, payments, and manager administration. Packaged as a single-file Windows executable (`apps/accounting/HesabdariSib.spec`, `console=False`).
- **Shift Management App (Planned, `docs/shift/`)**: Independent service reading accounting via read-only bridge (`mode=ro`, `PRAGMA query_only=1`, versioned `v_shift_*` views per S04) with its own database, blind shift handovers, and append-only event log (ADR-004, ADR-010). No code yet: `apps/` contains only `accounting/`.
- **Clinic Landing (`clinic-landing/`)**: Vite + TypeScript + three.js marketing/3D web app deployed to Vercel (`clinic-landing/vercel.json`).
- *Parallel Note*: `graphify-out/` was built by a DeepSeek/DSH worker (`.master/graphify-dsh.md`); the folder is gitignored and rebuilt with `graphify-out/rebuild.ps1` from git-tracked files only. Start with `graphify-out/GRAPH_REPORT.md`.

## 2. Directory Structure
- `apps/accounting/`: Accounting Flask application codebase, tests, packaging specs, and configs.
- `clinic-landing/`: Marketing landing page and 3D visualizer web app.
- `docs/`: Comprehensive roadmap, architecture decision records, engineering, and ops documentation.
- `docs/accounting/`: Accounting audit, target architecture, migrations, and test strategy (A01–A08).
- `docs/shift/`: Shift application domain model, logic specifications, and delivery plans (S01–S05).
- `docs/engineering/`: Engineering standards (E01), security and privacy rules (E02), dev setup (E03).
- `docs/ops/`: Backup and disaster recovery (O01), incident runbook (O02), staff communication (O03), release logs (`docs/ops/releases/`).
- `.github/workflows/accounting.yml`: Windows CI pipeline (lint, synthetic test suite, PyInstaller exe build).
- `.master/`: Multi-agent coordination artifacts and working notes.
- `graphify-out/`: AST-indexed code and documentation knowledge graph (gitignored).
- `_data/` *(untracked / excluded)*: Real patient database snapshots (STRICTLY CONFIDENTIAL, ADR-006).

## 3. Accounting App Architecture Layers (`apps/accounting/src/`)
Refactoring follows the Strangler pattern (ADR-003): route -> service -> repository. **The layering is aspirational, not enforced** — `api/reception.py` (~98 KB) and `api/manager/*.py` still hold many `db.execute` calls inline; there is no repo per aggregate for every query.
- **Entry point**: `apps/accounting/start.py` -> `apps/accounting/src/app.py:create_app()` (registers blueprints, Jinja filters, CLI, backup scheduler).
- **Domain (`apps/accounting/src/domain/`)**: Pure business models and logic:
  - `apps/accounting/src/domain/billing.py`: Invoice item share computations (`Shares(recorded, patient, insurance, covered)`) and the "consumables are not revenue" rule.
  - `apps/accounting/src/domain/shifts.py`: Shift windows (morning 07:30 / evening 13:30 / night 19:30, 30-min early takeover) and handover rules.
  - `apps/accounting/src/domain/money.py`: Amount parsing/formatting (DB amounts are Toman).
  - `apps/accounting/src/domain/identity.py`: National ID and mobile validation plus digit normalization.
  - `apps/accounting/src/domain/jalali.py`: Jalali <-> Gregorian conversion helpers.
  - `apps/accounting/src/domain/patients.py`: `Patient` dataclass only (no logic).
  - `apps/accounting/src/domain/visits.py`: `Visit` dataclass only (no logic).
  - `apps/accounting/src/domain/user.py`: `User` dataclass.
  - `apps/accounting/src/domain/reports.py`: **empty placeholder (0 bytes)** — no reporting domain code yet.
- **Services (`apps/accounting/src/services/`)**: Application business orchestration:
  - `apps/accounting/src/services/auth_service.py`: Authentication, bcrypt verification (legacy werkzeug hash migration), role enforcement (`USER_ROLES = manager/reception`), lockout.
  - `apps/accounting/src/services/reception_service.py`: Reception domain workflows.
  - `apps/accounting/src/services/manager_finance.py`: Month-to-date tiles and expenses-ledger calculations.
  - `apps/accounting/src/services/manager_insights.py`: Manager attention list and insight queries.
  - `apps/accounting/src/services/shift_handover.py`: Shift start/handover verification.
  - `apps/accounting/src/services/backup.py`: SQLite online backup API integration + integrity check.
  - `apps/accounting/src/services/scheduler.py`: Automatic backup scheduling (daily after 04:00 Tehran, startup catch-up).
  - `apps/accounting/src/services/exports.py`: CSV/Excel (`.xlsx`) export helpers with BOM and formula neutralisation.
  - `apps/accounting/src/services/patient_matching.py`: Duplicate/conflict checks (same national ID, shared phone).
  - `apps/accounting/src/services/activity_logger.py`: Activity log writer (`ActionType`/`ActionCategory`).
  - `apps/accounting/src/services/staff_performance.py`, `apps/accounting/src/services/tariff_audit.py`: Staff performance and tariff-change audit helpers.
  - `apps/accounting/src/services/manager_service.py`: **empty placeholder (0 bytes)**.
- **Adapters (`apps/accounting/src/adapters/sqlite/`)**: SQLite persistence layer:
  - `apps/accounting/src/adapters/sqlite/core.py`: Database connection factory, first-run `schema.sql` load, migration runner.
  - `apps/accounting/src/adapters/sqlite/schema.sql`: Baseline table definitions (`CREATE TABLE IF NOT EXISTS`, 25 tables).
  - Repositories (one per aggregate): `apps/accounting/src/adapters/sqlite/invoices_repo.py`, `patients_repo.py`, `payments_repo.py`, `tariffs_repo.py`, `shift_staff_repo.py`, `visits_repo.py`, `auth_repo.py`, `consumables_repo.py`, `injections_repo.py`, `procedures_repo.py`, `nursing_services_repo.py`, `user_shift_repo.py` and the `*_tariffs_repo.py` pair. `apps/accounting/src/adapters/sqlite/reports_repo.py` is an empty placeholder.
  - Migrations: `apps/accounting/src/adapters/sqlite/migrations/` — `m0001_baseline.py` through `m0006_invoice_voids.py`, registered in `MIGRATIONS`.
- **API (`apps/accounting/src/api/`)**: Presentation layer / Flask Blueprints:
  - `apps/accounting/src/api/auth.py`: Blueprint `/auth` — login, logout, single `login_required` (roles: manager, reception).
  - `apps/accounting/src/api/dashboard.py`: Blueprint `/` — role-based redirect (manager or reception index).
  - `apps/accounting/src/api/reception.py`: Blueprint `/reception` and `@bp.before_app_request` shift injection on every request.
  - `apps/accounting/src/api/manager/` (package, `/manager`): dashboard, reports, exports, arrears, expenses, payroll, tariffs, users, logs, settings, `_common` (route guards + Jalali range helper).
- **Web/CSRF**: `apps/accounting/src/web/csrf.py` requires a token on every state-changing request (injected by `apps/accounting/src/static/js/csrf.js`).
- **Templates & Static Assets**:
  - `apps/accounting/src/templates/`: Jinja2 templates (`apps/accounting/src/templates/auth/`, `apps/accounting/src/templates/reception/`, `apps/accounting/src/templates/manager/`, `apps/accounting/src/templates/errors/`).
  - `apps/accounting/src/static/`: Static assets (`apps/accounting/src/static/js/chart.min.js`, `apps/accounting/src/static/js/jalaali.min.js`, `apps/accounting/src/static/js/jdatepicker.js`, `apps/accounting/src/static/js/csrf.js`).
  - `apps/accounting/src/common/`: Common helpers (`apps/accounting/src/common/jalali.py`, `apps/accounting/src/common/utils.py`, `apps/accounting/src/common/validators.py`).
  - `apps/accounting/src/config/`: App configuration (`apps/accounting/src/config/settings.py`, `apps/accounting/src/config/logging_setup.py`).

## 4. Database & Migration Rules
- **Additive Only (ADR-002)**: Tables, nullable/defaulted columns, indexes, and views only. Never DROP, RENAME, or change types of existing columns/tables. Old executables must stay compatible with migrated DBs.
- **Migration Tracking**: Managed via `PRAGMA user_version` (`migrations.get_version` / `run_migrations`). Each step is applied in its own transaction (explicit `COMMIT`/`ROLLBACK`); a failure raises `MigrationError` and the app does not start.
- **Pre-Migration Backups (ADR-008)**: SQLite Online Backup API creates a verified backup before pending migrations (`backup()` is called once before the first pending step) and daily after 04:00 Tehran time.
- **Shift Business Dates**: Ledgers filter on `work_date` (spanning overnight shifts), never naive UTC timestamps.

## 5. Test, Golden-Master, and CI Rules
- **Privacy & Safety (ADR-006)**: Real patient records (`_data/`, `*.db`) must NEVER be committed, tracked, or sent to external tools.
- **Synthetic Test DB**: CI and route matrix tests run against a synthetic database generated via `apps/accounting/tools/make_synthetic_db.py` (the `synthetic_app` fixture in `apps/accounting/tests/conftest.py`).
- **Golden-Master Tests**: `apps/accounting/tests/test_billing_characterization.py` locks existing financial calculations. Discrepancies indicate business logic divergence.
- **Route Matrix**: `apps/accounting/tests/test_route_matrix.py` enforces permissions across all endpoints and roles against `apps/accounting/tests/route_matrix.json`.
- **GitHub Actions (`.github/workflows/accounting.yml`)**: Windows runner verifies no DB is tracked, executes ruff lint & format checks, runs pytest with coverage, builds the PyInstaller executable, and runs a smoke test.
- **Verified 2026-10-28**: 491 tests pass, 6 deselected (marker `prod_snapshot`, excluded by `pytest.ini`).

## 6. Key Documentation (Read First)
1. `docs/00_README.md`: Master documentation roadmap and reading guide.
2. `docs/01_PROJECT_CHARTER.md`: Platform vision, success metrics, and stakeholders.
3. `docs/02_ROADMAP.md`: Implementation phases (Phases 0-4) and gate requirements.
4. `docs/03_DECISIONS.md`: Architectural Decision Records (ADR-001 through ADR-013).
5. `CLAUDE.md` & `apps/accounting/CLAUDE.md`: Repository-wide invariants and accounting development conventions.
6. Shift App Specs: `docs/shift/S01_PRODUCT_LOGIC.md`, `docs/shift/S02_ARCHITECTURE.md`, `docs/shift/S03_DOMAIN_MODEL.md`, `docs/shift/S04_ACCOUNTING_INTEGRATION_CONTRACT.md`, `docs/shift/S05_DELIVERY_PLAN.md`, and root `اپ شیفت درمانگاه سیب — نسخه نهایی منطق.md`.

## 7. Exact Test and Lint Commands
Execute from `apps/accounting/`.

### Lint & Formatting (verified 2026-10-28)
- Lint check: `uvx ruff check .` → `All checks passed!`
- Code format check: `uvx ruff format --check .` → `67 files already formatted`

### Test Suite Execution (verified 2026-10-28: 491 passed, 6 deselected)
Host `python` on PATH is 3.8, so call Python 3.13 explicitly; a PATH without `GTK3-Runtime` keeps the DLL shadowing away:
- Run full synthetic pytest suite:
  `powershell -Command "$env:PATH=($env:PATH.Split(';')|Where-Object {$_ -notmatch 'GTK'}) -join ';'; $env:PYTHONPATH='C:/Users/Emad Karimi/Desktop/sib/.venv/Lib/site-packages;C:/Users/Emad Karimi/Desktop/sib/apps/accounting'; & 'C:/Users/Emad Karimi/AppData/Local/Programs/Python/Python313/python.exe' -m pytest"`
- Run single test module (verified: 82 passed):
  `powershell -Command "$env:PATH=($env:PATH.Split(';')|Where-Object {$_ -notmatch 'GTK'}) -join ';'; $env:PYTHONPATH='C:/Users/Emad Karimi/Desktop/sib/.venv/Lib/site-packages;C:/Users/Emad Karimi/Desktop/sib/apps/accounting'; & 'C:/Users/Emad Karimi/AppData/Local/Programs/Python/Python313/python.exe' -m pytest tests/test_domain.py"`
- `powershell -File dev.ps1 test` (ruff + pytest through `.venv`) also passes on this host (491 passed, 2026-10-28). The earlier "GTK3-Runtime / ruff launcher" failure note was stale.

### Environment-Specific Commands (not run in this review)
- `powershell -File dev.ps1 test-prod`: production-snapshot gate; needs a copy in `_data/prod_snapshot/` (deselected by `pytest.ini` `addopts` and in CI).
- `python tools/golden_master.py compare --db <snapshot.db>`: `--db` is **required** (modes: `record` | `compare`); needs a production database copy.
- `.venv/Scripts/python.exe -m PyInstaller --noconfirm HesabdariSib.spec`: `pyinstaller.exe` is not on PATH (use the venv module form or `dev.ps1 build`); the build and smoke test run in GitHub Actions CI.
