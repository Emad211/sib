# Shift auth — design steps 6, 7, 8 (task_0ddf38add844, SHF-1.1)

Date: 2026-10-28 · Branch: `Emad211/shift-auth` · Worktree: `C:/Users/Emad Karimi/orca/workspaces/sib/shift-auth`

## Base branch (action taken at start)

The worktree branched from `b148152`, which has **no `apps/shift`**. The finished skeleton
(`Emad211/shift-skeleton`, `2a7e5ff`, 141 tests) was missing, so this branch was
fast-forwarded onto it before any work: no merge commit, no skeleton file edited, and the
diff of this task is exactly the files below.

## Commits

| Commit | Content |
|---|---|
| `59617f5` | migration 2 `auth_and_devices` + one registry line |
| `47aebaa` | `staff_repo`, `devices_repo`, `sessions_repo` |
| `f92b2d3` | `domain/permissions.py`, `domain/pin_policy.py` |
| `460bd8a` | `services/auth.py`, `services/devices.py`, 4 templates, `tools/print_enroll_qr.py` |
| `e07e5a7` | `api/deps.py`, `api/errors.py`, `api/routes_{auth,devices}.py`, app wiring, all tests + `tests/data/route_matrix.json` |

Each commit was checked out and run: `141` (repos), `147` (services), `254` (HEAD) tests green.

## What exists

- **Schema (v2)**: `staff` (unique mobile, argon2id `pin_hash`, `failed_attempts`,
  `locked_until`, `active`), `staff_role` (PK `staff_id+role`, index on `role`), `devices`
  (staff FK, `pubkey`, `revoked_at/reason`), `enrollment_tokens` (digest PK, **unique**
  `device_id`, `expires_at`, `consumed_at`), `sessions` (digest PK, `csrf_token`, staff and
  device FKs, `expires_at`, `revoked_at/reason`, partial indexes on live staff/device).
- **Domain**: 7 roles (`purchaser` is a role, never a person field), 24 permissions,
  `ROLE_PERMISSIONS` read-only mapping, `permissions_for` union for multi-role staff;
  PIN policy (6 digits, no repeats, no ±1 run, no year-shaped window, Persian/Arabic digits).
- **Services**: `AuthService` (login, authenticate, logout, revoke-by-staff for shift close,
  unlock, reset PIN, directory), `DeviceService` (mint, enroll, revoke, list).
- **API**: `/api/v1/auth/{login,logout,me}`, `/api/v1/staff`,
  `/api/v1/staff/{id}/{unlock,pin-reset}`, `/api/v1/devices`,
  `/api/v1/devices/enrollment-tokens`, `/api/v1/devices/enroll`,
  `/api/v1/devices/{id}/revoke`; pages `/login`, `/enroll`, `/admin/devices`.
- **Guards**: `require(permission)` / `require_session()` / `require_page(permission)` stamp a
  `shift_guard` marker; `tests/integration/test_route_matrix.py` introspects every route's
  dependency tree and fails on an unguarded route, an unlisted route, a stale row, a guard
  that disagrees with the row, or a role that lacks the permission yet gets a success.
- **Route matrix**: 14 route cells × 9 roles (anon, 7 roles, `multi_role`), hand-written as
  the spec — not recorded from behaviour.

## Decisions that differ from the design (deliberate)

1. **argon2id (task) instead of bcrypt (design §3)** via `argon2-cffi==25.1.0`
   (`t=3, m=64 MiB, p=4`, RFC 9106 second recommendation), with the S02 §4 salt **and
   pepper**: the hashed value is `<secret-file value>\x1f<pin>`, so a stolen database alone
   cannot be searched offline. Operational consequence: the install secret
   (`.shift_secret_key` next to the database) must be backed up with the database — losing it
   makes every PIN unverifiable and every staff member needs a manager reset.
2. **QR library: `segno==1.6.6`** — pure Python, zero dependencies, writes PNG/SVG/terminal
   from the same object, so `print_enroll_qr.py` and the admin screen share one dependency
   and no Pillow/native build is added to the clinic machine.
3. **PIN policy is `domain/pin_policy.py`** (new file): the design's `domain/policy.py`
   belongs to step 11 and is outside this task's ownership.
4. **Lockout**: 5 consecutive wrong PINs → `locked_until = now + 15 min` **and** a manager
   unlock (`staff.unlock`) that clears it immediately; a correct PIN inside the window is
   refused (423). The counter is committed before the refusal is raised — raising inside the
   write transaction would roll the counter back.
5. **Auth events go to the audit sink, not the event store.** `staff.locked`,
   `staff.unlocked`, `staff.pin_reset`, `device.enrolled`, `device.revoked` are logged to
   `audit.log`; the event registry (`domain/events.py`, `domain/payloads.py`) is not owned by
   this task. **Step owner of the registry should add these as real events** (§3 of the design
   says lock and reset are events).
6. **Session TTL, lockout thresholds and the PIN length are module constants**
   (`services/auth.py`, `domain/pin_policy.py`) because `config/settings.py` is outside the
   ownership line. Move them to versioned settings in step 11/14 (design §8.6).
7. **CSRF**: session-bound token returned by `/login` and `/me`, echoed in `X-CSRF-Token` for
   every unsafe verb (`403 csrf_failed` otherwise); the session-less login POST uses a
   double-submit cookie `shift_csrf` issued by `GET /login`. The enrollment POST needs no CSRF
   proof: the one-time token is the credential and there is no ambient authority.
8. **Cookies** are `HttpOnly; SameSite=Strict; Path=/; Max-Age=12h` and gain `Secure` only
   when `SHIFT_ENV=production`, so the LAN dev server and `TestClient` keep working over
   HTTP; a test asserts the production flag.
9. **`docs_url`/`redoc_url`/`openapi_url` are disabled** in `create_app`, so the route matrix
   can cover every route and no API schema is published on the clinic LAN.
10. **`purchase.order.decide` is manager-only** while `purchase.order.draft` is the
    purchaser's: whoever drafts must not approve (segregation of duties); a unit test pins it.
11. **`staff_role.role` has no CHECK constraint** (step 11 may add roles); an unknown role
    fails closed — `authenticate()` turns it into `401`, never into a wider permission set.
12. **New files beyond the ownership list** (no collisions, no other worker's file touched):
    `api/templating.py` (Jinja env), `api/__init__.py` (package marker),
    `tests/data/auth_fixtures.py` (shared synthetic staff/devices, same pattern as
    `event_fixtures.py`), `domain/pin_policy.py` (see 3).

## Known trade-off

`login()` verifies the PIN inside the single write transaction, so the writer is held for the
argon2 duration (~0.3 s at the production parameters). Shift-start logins are a handful per
minute, so this is accepted; if the S2 measurements change, read the staff row first and keep
the counter/session write in a short second transaction.

## Verification (run from `apps/shift`, clean PATH, no `_data/`, no real `*.db`)

```text
ruff check .                 -> All checks passed!
ruff format --check .        -> 78 files already formatted
coverage run -m pytest       -> 254 passed
coverage report --include="*/shift/domain/*" --fail-under=95 -> 100% (240 stmts, 24 branches)
coverage report --include="*/shift/api/*"                    -> 99%
coverage report --include="*/shift/services/auth.py"         -> 99%
coverage report --include="*/shift/services/devices.py"      -> 98% (only the lost-race branch)
powershell -File ./dev.ps1 test -> 254 passed, domain gate 100%
```

CLI acceptance: `tools/print_enroll_qr.py` was run against a migrated temp database in
`test_print_enroll_qr_writes_a_png_and_prints_the_url` (PNG magic bytes asserted) and its
unknown-staff path exits 1 (`test_print_enroll_qr_refuses_an_unknown_staff`).

Security tests: token single-use (409), expiry (410), label/pubkey refusals keep the token
usable, token never in `shift.log`/`audit.log`/device list, digest-only storage, revocation
kills live sessions, PIN/token/CSRF never logged, device revocation behind the app's back and
staff deactivation both invalidate an existing session, unknown role fails closed, offsite
`?next=` ignored, page redirect keeps the `next` path.

## Next (integration points for other steps)

- **Step 10 (handover)**: call `AuthService.revoke_staff_sessions(staff_ids=…, reason="shift_closed")`
  when a shift closes — that is the "logout on shift close" rule.
- **Step 11 (settings/catalog)**: move 12 h TTL, 5 attempts, 15 min lock, PIN length into
  versioned settings; add roles only together with `Permission` grants.
- **Event registry owner**: promote the audit lines above to `staff.*` / `device.*` events.
- **Step 12/13 (PWA)**: replace the inline page scripts with `static/csrf.js` + `static/app.js`
  and cache `/login` as offline shell; the pages already carry `<meta name="csrf-token">`.
- `app.py` now also wires `state.auth` and `state.devices`; other routers only need
  `deps.require(<permission>)` and a row in `tests/data/route_matrix.json`.
