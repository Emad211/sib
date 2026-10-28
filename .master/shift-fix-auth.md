# Shift auth — review fixes (task_2ec770c3c9e2, dispatch `ctx_58c3eb18828e`)

Date: 2026-10-28 · Branch: `Emad211/shift-auth` · Worktree: `C:/Users/Emad Karimi/orca/workspaces/sib/shift-auth`
Input: `.master/review-auth.md` (0 BLOCKER, 3 SHOULD, 4 NICE). Every fix stayed inside the auth
slice; `_data/` and `*.db` were never opened; nothing was pushed.

## Outcome

| # | Severity | Finding | State | Commit |
|---|---|---|---|---|
| 2 | SHOULD | Writer lock held for the whole Argon2id verification | **fixed** | `62a8dac` |
| 1 | SHOULD | Cross-device / unknown-mobile timing *and* status difference | **fixed** (also for inactive accounts) | `62a8dac` + `a18c82c` |
| 4 | NICE | `_safe_next_path` backslash / control-character redirects | **fixed** | `58a2d55` |
| 5 | NICE | Cookie deletion without the attributes it was set with | **fixed** (logout + pre-auth cookie) | `598de0b` |
| 6 | NICE | PIN policy accepts 2-digit repeat cycles (`121212`) | **fixed** (`pin_pattern`) | `a4a11a8` |
| 3 | SHOULD | `.shift_secret_key` (PIN pepper) is not backed up with the database | **deferred, out of slice** — see below | — |
| 7 | NICE | Auth events go to `audit.log`, not the event store | **deferred, already documented** — see below | — |

## 1. SHOULD-2 — hashing outside the SQLite writer (`62a8dac`)

`AuthService.login` used to open `with self._database.write()` (i.e. `BEGIN IMMEDIATE` on the
single-writer pool) *before* verifying the PIN, so the writer was held for the ~0.3 s of
Argon2id on every attempt, including the decoy hash for unknown mobiles.

Now: `_identify()` reads the phone and the staff row in a **read** transaction; `_pin_matches()`
verifies outside every transaction; `_record_attempt()` takes the writer only to record the
failure counter / lock or to insert the session, and lets the transaction commit before the
refusal is raised (so the counter still survives the refusal). An unknown mobile writes nothing
at all. `reset_pin()` hashes before it opens the writer for the same reason.

The staff row is **re-read inside** the write transaction, which closes the races the move opens:
a failure counted by another request while this one was hashing is added to (not overwritten with
the pre-hash snapshot), and a session is only issued when the row still exists, is still active,
is still unlocked, and still holds **the digest that was verified**. That last check keeps
`reset_pin` absolute: without it, a login that hashed the old PIN just before a manager reset would
insert its session *after* `revoke_for_staff` and survive the reset for up to 12 hours.

## 2. SHOULD-1 — one Argon2id check on every path, one refusal envelope (`62a8dac`, `a18c82c`)

Every login path now pays exactly **one** `verify()`:

| Path | Before | After |
|---|---|---|
| Unknown mobile | decoy verify (~300 ms) | decoy verify (digest built once, then cached) |
| Wrong PIN | verify | verify |
| Mobile belongs to somebody else (borrowed phone) | **no verify**, 403 `device_not_enrolled` in ~1 ms | verify, 401 `invalid_credentials` |
| Inactive staff | **no verify**, 403 `staff_inactive` in ~1 ms | verify, 401 `invalid_credentials` |
| Locked account | no verify, 423 | verify, 423 (unchanged status — S4 asks for the lock answer) |

`api/errors.describe_refusal` now maps `InvalidCredentials` **and** `StaffInactive` to the same
`401 invalid_credentials` body, so status, code and message are byte-identical for all four
"this person may not log in" cases. The precise reason (`result=unknown_mobile|device_not_owned|
inactive|bad_pin`) stays in `audit.log`, which only the manager can read. The 403 codes remain
for refusals about the *phone* itself (unknown or revoked device), which say nothing about who
the caller is and no mobile-dependent information at all.

Tests are call-count based, never wall-clock: `tests/integration/test_login_hardening.py`
asserts `verify()` is called once for accepted / wrong PIN / unknown mobile / borrowed device /
inactive / locked, that the decoy digest is hashed once and reused, that **no write transaction
is open** while a hash runs (a `Database.write` spy records the state seen inside `hash()` and
`verify()`), that a locked account takes no writer, that a failure recorded *during* the hash is
not lost, and that a row deactivated, locked or PIN-reset during the hash opens no session.
`test_session_lifecycle.py` compares the four HTTP refusals byte for byte.

## 3. NICE — the other hardening (`58a2d55`, `598de0b`, `a4a11a8`)

- **Redirect**: `_safe_next_path` refuses any value whose first character is not `/`, that
  starts with `//`, or that carries `[\x00-\x20\x7f\\]` — covering `//evil.example`,
  `/\evil.example` and the `/<tab>/evil.example` spelling browsers normalise to `//evil`.
- **Cookies**: `logout` and the login-time pre-auth deletion now repeat the attributes of the
  matching `Set-Cookie` (`HttpOnly`, `SameSite=strict`, `Path=/`, and `Secure` under
  `SHIFT_ENV=production`), so strict engines accept the deletion.
- **PIN policy**: a PIN built from a 2- or 3-digit block repeated (`121212`, `123123`) is
  refused as `PinProblem.PATTERN` (`pin_pattern`); `112233` is still allowed because it is a
  pair run, not a repetition.

## Disagreements / deferrals

**Finding 3 (pepper backup) — not fixed here, and I think it should not be fixed here.** The
defect is real: `Settings.read_or_create_secret()` creates `.shift_secret_key` next to the
database and `adapters/db/core.py:_backup_database()` copies only the SQLite file, so restoring
`shift.db` on another host silently invalidates every PIN. But the fix belongs to files outside
this task's ownership — `services/backup.py` (Step 14) does not exist yet, and
`config/settings.py` + `adapters/db/core.py` + the DR runbooks are another step's slice. The
operational consequence is already recorded in `.master/shift-auth.md` decision 1. Hand-off:
Step 14 must copy `.shift_secret_key` together with `shift.db`, restore both, and fail loudly on
startup if a non-empty database exists without a secret file.

**Finding 7 (auth events in the event store) — deferred, no new decision.** `.master/shift-auth.md`
decision 5 already records that `domain/events.py`, `domain/payloads.py` and the event registry
belong to step SHF-1.2. Promoting the audit lines to `staff.locked|unlocked|pin_reset` events
needs those files; nothing in the auth slice can do it half-way without a registry entry.

**Reversal of a recorded decision.** `.master/shift-auth.md` §"Known trade-off" accepted holding
the writer during the hash. This task reverses it (review SHOULD-2), so that paragraph is stale:
the login path now costs one read transaction plus one short write transaction, and the writer is
never held across a hash. Only the owner of that file should edit it.

## HTTP contract changes other steps must know

- `POST /api/v1/auth/login`: a mobile that belongs to somebody else, or to a deactivated person,
  now answers **401 `invalid_credentials`** instead of 403 `device_not_enrolled` /
  `staff_inactive`. Clients must not branch on those two codes for login any more (the shipped
  pages only display `error.message`, so nothing broke). Unknown/revoked *device* keeps 403.
- `POST /api/v1/staff/{id}/pin-reset` can now answer `400 pin_rejected` with
  `details.rule = "pin_pattern"` for a repeated-block PIN.

## Verification (from `apps/shift`, clean PATH, no `_data/`, no real `*.db`)

```text
.venv/Scripts/python.exe -m ruff check .            -> All checks passed!
.venv/Scripts/python.exe -m ruff format --check .   -> 79 files already formatted
.venv/Scripts/python.exe -m pytest -q               -> 286 passed  (was 256 before this task)
coverage report --include="*/shift/domain/*" --fail-under=95 -> 100% (245 stmts, 26 branches)
coverage report services/auth.py, services/devices.py, api/* -> auth.py 100%, deps.py and
                                                               routes_*.py 100%, errors.py 97%,
                                                               devices.py 98% (pre-existing gaps:
                                                               the SessionInvalid mapping, the
                                                               catch-all 500 handler, the lost race)
```

New tests: `tests/integration/test_login_hardening.py` — 13 cases with the parametrized paths —
plus the extended refusal, redirect, cookie and PIN-policy cases; every assertion is a call
count or a header/state check, never a duration.

## Residual risk

- A locked account still answers `423 account_locked`, which confirms the mobile exists — that is
  the S4 requirement (the person must be told the manager has to unlock them), it costs five
  failed attempts, and every lock is written to `audit.log`.
- Refusals still differ between "this phone is unknown/revoked" and "mobile or PIN is wrong", but
  the first pair depends only on the device credential the phone already holds.
- The login path now does two extra row reads (fresh staff row inside the writer, device read in
  the read transaction); measured suite time for `login`-heavy tests is unchanged within noise.
