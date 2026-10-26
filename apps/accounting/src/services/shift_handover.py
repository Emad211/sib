"""Starting a shift (FB-01, FB-02): the schedule decides which shift, open invoices block the handover.

* Nobody picks a shift by hand any more. "Start shift" starts the shift the schedule says.
* A shift cannot be started while an invoice from another shift is still open: whoever opened it
  stays, settles it and closes it (an open visit may still get injections).
* Nobody is switched automatically. A person who stays late keeps working in their shift
  (FINISHING). A person whose stored shift is older than that (STALE, e.g. yesterday's) may not
  open new invoices until they start the scheduled shift, so nothing lands on the wrong date.
"""

from __future__ import annotations

from datetime import datetime

from src.domain import jalali, shifts


def load_schedule(db) -> dict:
    """Start times from settings (`shift_start_morning|evening|night` = 'HH:MM'), defaults otherwise."""
    rows = {
        r["key"]: r["value"]
        for r in db.execute(
            "SELECT key, value FROM settings WHERE key IN ('shift_start_morning', 'shift_start_evening', 'shift_start_night')"
        )
    }
    starts = {
        name: shifts.parse_hhmm(rows.get(f"shift_start_{name}"), default)
        for name, default in shifts.DEFAULT_STARTS.items()
    }
    if not starts["morning"] < starts["evening"] < starts["night"]:
        return dict(shifts.DEFAULT_STARTS)  # a broken setting must not break the reception desk
    return starts


def stored_shift(db, user_id: int) -> tuple[str, str] | None:
    row = db.execute("SELECT active_shift, work_date FROM user_active_shift WHERE user_id = ?", (user_id,)).fetchone()
    return (row["active_shift"], row["work_date"]) if row else None


def blockers(db, scheduled: tuple[str, str]) -> list[dict]:
    """Open invoices that belong to another shift than the one being started."""
    rows = db.execute(
        """SELECT i.id, i.work_date, i.shift, i.opened_at, COALESCE(u.full_name, i.opened_by_name, i.opened_by) AS who
           FROM invoices i LEFT JOIN users u ON u.username = i.opened_by
           WHERE i.status = 'open' AND NOT (i.work_date = ? AND i.shift = ?)
           ORDER BY i.opened_at""",
        (scheduled[1], scheduled[0]),
    ).fetchall()
    return [
        {
            "invoice_id": r["id"],
            "who": r["who"] or "—",
            "shift_fa": shifts.shift_label(r["shift"]),
            "work_date_fa": jalali.jalali_text(r["work_date"]),
        }
        for r in rows
    ]


def scheduled_taken_by_other(db, user_id: int, scheduled: tuple[str, str]) -> bool:
    row = db.execute(
        "SELECT 1 FROM user_active_shift WHERE active_shift = ? AND work_date = ? AND user_id != ? LIMIT 1",
        (scheduled[0], scheduled[1], user_id),
    ).fetchone()
    return row is not None


def status(db, user_id: int, now: datetime) -> dict:
    scheduled = shifts.scheduled_shift(now, load_schedule(db))
    stored = stored_shift(db, user_id)
    where = shifts.standing(stored, scheduled)
    taken = scheduled_taken_by_other(db, user_id, scheduled)
    return {
        "scheduled_shift": scheduled[0],
        "scheduled_shift_fa": shifts.shift_label(scheduled[0]),
        "scheduled_work_date": scheduled[1],
        "standing": where,
        "needs_start": where != shifts.CURRENT,
        "can_open_invoices": where == shifts.CURRENT or (where == shifts.FINISHING and not taken),
        "handed_over": where == shifts.FINISHING and taken,
    }


def may_open_invoice(db, user_id: int, now: datetime) -> tuple[bool, str | None]:
    s = status(db, user_id, now)
    if s["can_open_invoices"]:
        return True, None
    if s["handed_over"]:
        return False, "شیفت شما تحویل داده شده است؛ فقط فاکتورهای باز خودتان را تسویه و ببندید."
    return False, f"ابتدا «شروع شیفت {s['scheduled_shift_fa']}» را بزنید."
