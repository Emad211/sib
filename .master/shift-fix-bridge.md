# Bridge review fixes — worker `task_a2073180ad83`

Date: 2026-10-28 · Worktree: `C:/Users/Emad Karimi/orca/workspaces/sib/shift-bridge`
Branch: `Emad211/shift-bridge` · Base: `aa8b42c` · Source: `.master/review-bridge.md` (Gemini)

## Result

All five findings are fixed. Nothing was left as a disagreement.

| # | Sev | Finding | Fix | Evidence |
|---|---|---|---|---|
| 1 | SHOULD | A re-closed invoice's new amount was ignored | Cash is ingested per `(invoice_id, closed_at, amount)`; `acc_cash` keeps versions, a re-close appends `acc.cash_reversal` + a new `acc.cash_receipt` that reference the superseded receipt, and a void reverses the active cash | `tests/integration/test_cash_corrections.py`, `tests/property/test_corrections_convergence.py` |
| 2 | SHOULD | `EMPTY_WATERMARK_ID = 0` skipped a hypothetical id 0 | Seed is `-1` (`watermark_repo.py`) | `tests/integration/test_watermark_edges.py::test_the_seed_watermark_sorts_before_every_accounting_id` |
| 3 | NICE | No explicit same-second paging test | Two invoices closed in one second, `page_size=1` | `tests/integration/test_watermark_edges.py::test_two_invoices_closed_in_the_same_second_are_paged_apart_by_their_ids` |
| 4 | NICE | Migration 1→3 skips version 2 | Comment in `migrations/__init__.py`: 2 is reserved for the outbox/photo schema (design §5 step 13) | `apps/shift/src/shift/adapters/db/migrations/__init__.py:12` |
| 5 | NICE | `amount_raw` absent from the event payload | `CashReceipt` / `CashReversal` carry optional `amount_raw` (non-negative, finite); the bridge fills it on every cash event | `tests/integration/test_watermark.py::test_money_is_rounded_once_at_ingest_and_the_raw_value_is_kept` |

## The money correction design

**Identity.** A close is identified by `(invoice_id, closed_at, amount)` for the idempotency key
(`acc:v_shift_cash_receipts_v1:<invoice>@<closed_at>@<amount>`), and by `(invoice_id, closed_at)` for
the `acc_cash` row. The same invoice may have several versions; at most one is active
(`superseded_at IS NULL`).

**Re-close** (accounting deletes the lines, reopens, closes at a new stamp and/or amount):

1. The superseded version is found in `acc_cash` (same key, or the active version at a new stamp).
2. Its receipt event is located by key, with a fallback to the pre-upgrade
   `acc:v_shift_cash_receipts_v1:<invoice>` key.
3. One batch appends:
   - `acc.cash_reversal` — `{"amount": <old>, "amount_raw": <old raw>, "reason": "reclosed"}`,
     projection effect `cash: subtract`, `ref_event_id = <superseded receipt ULID>`.
   - `acc.cash_receipt` — the new amount, `ref_event_id = <same receipt ULID>`.
4. The mirror marks the old version `superseded_at` and inserts the new version (or updates the
   amount in place when the stamp did not move).

**Void** (accounting keeps the frozen lines and sets `status='voided'`):

- `acc_void.compensated_at` (migration 6) records that a void was turned into a reversal.
- Each poll, every uncompensated void with active cash in `acc_cash` appends one
  `acc.cash_reversal` (`reason` `refunded`/`voided`, `ref_event_id = active receipt`) in the shift
  where the void was discovered (H-02 subtracts the refund from the current drawer), then marks the
  void compensated. Voids with no ingested cash are marked compensated without an event.
- Existing `acc_void` rows start uncompensated, so the first poll after the upgrade also heals void
  compensations that the previous version never emitted.

**Why a reversal event, not a negative receipt amount.** `Toman` is a non-negative strict integer
(`domain/money.py`) and the registry contract rejects negative cash payloads
(`tests/contract/test_event_registry.py`). A first-class `acc.cash_reversal` with a `SUBTRACT`
projection effect carries the same arithmetic without weakening the money invariant. R-03/ADR-010
("correction = new record with a reference") is preserved: both the reversal and the new receipt
carry `ref_event_id` to the receipt they supersede.

**Watermark.** A re-close can carry a stamp the cash watermark already passed (the invoice was read
once at the old stamp). The cash feed therefore re-reads a bounded trailing window
(`CASH_LOOKBACK = 24h` from the cutoff) every poll. Mirror state and deterministic event keys make
the re-read idempotent; a crash between event append and mirror/watermark commit re-plans the same
keys and the event store returns the stored rows.

**Migration 6 (`bridge_corrections`).** Rebuilds `acc_cash` with PK `(invoice_id, closed_at)` and
`superseded_at`, adds `acc_void.compensated_at`, and rewinds the cash watermark to the seed so the
first poll after the upgrade re-reads every cash row once. That one re-read heals re-closes the
previous version ignored (including re-closes behind the watermark) without double counting, because
matching mirror rows are skipped. `rebuild()` replays only the event log and stays equal to live in
every correction test.

**Known limit (documented, not fixed).** A re-close that removes *all* cash lines leaves no row in
`v_shift_cash_receipts_v1`, so the view cannot signal the disappearance. Closing with a different
non-zero cash amount (the reported case) and voiding are fully handled. A change feed or a `_v2`
view that reports stamp changes would close the zero-cash gap.

## Scope note

`domain/events.py` and `domain/payloads.py` gained the reversal event/payload; this is the minimal
additive vocabulary the compensating event needs. `domain/cutoff.py`, `adapters/accounting/*` and
`tools/make_synthetic_acc_db.py` needed no change.

## Verification (from `apps/shift`, clean PATH with GTK stripped)

```text
ruff check .                                  -> All checks passed!
ruff format --check .                         -> 76 files already formatted
pytest -q                                     -> 233 passed
coverage run -m pytest -q                     -> 233 passed
coverage report --include="*/shift/domain/*"  -> 100% (165 stmts, 8 branches, 0 missed)
```

New/changed tests: `test_cash_corrections.py` (re-close new stamp, same-stamp amount change, two
re-closes, void refund/void, void without ingested cash, legacy receipt key), `test_corrections_convergence.py`
(Hypothesis: random re-close/void sequences, every poll checked against the contract view, re-poll
no-op, `rebuild() == live`), `test_watermark_edges.py` (seed, same-second paging), plus migration 6
and `amount_raw` coverage in `test_watermark.py`.

## Commits (not pushed)

| Hash | Message |
|---|---|
| `2048baa` | `fix(shift): seed the watermark below every accounting id` |
| `b52a361` | `feat(shift): reverse re-closed and voided cash with correction events` |

Both end with `Co-Authored-By: DeepSeek <noreply@deepseek.com>`.
