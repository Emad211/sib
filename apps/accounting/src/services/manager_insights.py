"""What the manager needs first: exceptions, the last shifts, system health (ACC-2.8, A06 §3.1).

Every number here comes from one place and every "needs attention" count can be opened as a
list of the rows behind it (`attention_detail`). Thresholds were chosen from the real
production data (see docs/accounting/A06_MANAGER_AREA_SPEC.md §6) and can be overridden in
the `settings` table (`stale_open_hours`, `anomaly_delete_threshold`).

Revenue follows the owner's rule (Q-05, ADR in A06): visits + nursing + procedures of
*closed* invoices. Consumables (medicine/supplies) are shown separately and never summed in.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from src.common.utils import format_jalali_datetime, gregorian_to_jalali, iran_now

SHIFT_ORDER = {"morning": 0, "evening": 1, "night": 2}
SHIFT_FA = {"morning": "صبح", "evening": "عصر", "night": "شب"}
DELETE_ACTIONS = ("visit_delete", "injection_delete", "procedure_delete", "consumable_delete")
LOOKBACK_DAYS = 7
BACKUP_WARN_HOURS = 24

DEFAULTS = {"stale_open_hours": 6, "anomaly_delete_threshold": 20}

# Why each default: stale_open_hours - only ~0.4% of closed invoices stayed open longer than 6 h;
# anomaly_delete_threshold - one user deleting >= 20 items in a day happened 8 times in 240
# days (median is 2, p95 is 13), i.e. about once a month: rare enough to look at.


def jalali_date(work_date: str | None) -> str:
    if not work_date:
        return "—"
    try:
        d = date.fromisoformat(work_date[:10])
    except ValueError:
        return work_date
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{jy}/{jm:02d}/{jd:02d}"


def get_threshold(db, key: str) -> int:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    try:
        value = int(row["value"]) if row and row["value"] not in (None, "") else DEFAULTS[key]
    except (TypeError, ValueError):
        value = DEFAULTS[key]
    return value if value > 0 else DEFAULTS[key]


def reference_work_date(db) -> str:
    """Latest work date with any invoice. After midnight the calendar date is still empty while
    the night shift of the previous work date is running, so "today" must come from the data."""
    row = db.execute("SELECT MAX(work_date) AS d FROM invoices").fetchone()
    return (row["d"] if row and row["d"] else None) or iran_now().strftime("%Y-%m-%d")


def _pairs_clause(pairs):
    return " OR ".join("(i.work_date = ? AND i.shift = ?)" for _ in pairs), [v for p in pairs for v in p]


def latest_shifts(db, n: int = 3) -> list[tuple[str, str]]:
    rows = db.execute(
        """SELECT work_date, shift FROM invoices WHERE work_date IS NOT NULL AND shift IS NOT NULL
           GROUP BY work_date, shift
           ORDER BY work_date DESC, CASE shift WHEN 'morning' THEN 0 WHEN 'evening' THEN 1 ELSE 2 END DESC
           LIMIT ?""",
        (n,),
    ).fetchall()
    return [(r["work_date"], r["shift"]) for r in rows]


def _revenue_by_pair(db, pairs) -> dict[tuple[str, str], dict[str, float]]:
    """{(work_date, shift): {visits, nursing, procedures, consumables}} for closed invoices."""
    out = {p: {"visits": 0, "nursing": 0, "procedures": 0, "consumables": 0} for p in pairs}
    if not pairs:
        return out
    where, params = _pairs_clause(pairs)
    for key, table, col in (
        ("visits", "visits", "price"),
        ("nursing", "injections", "total_price"),
        ("procedures", "procedures", "price"),
        ("consumables", "consumables_ledger", "total_cost"),
    ):
        for r in db.execute(
            f"""SELECT i.work_date, i.shift, COALESCE(SUM(t.{col}), 0) AS total
                FROM invoices i JOIN {table} t ON t.invoice_id = i.id
                WHERE i.status = 'closed' AND ({where}) GROUP BY i.work_date, i.shift""",
            params,
        ):
            out[(r["work_date"], r["shift"])][key] = r["total"]
    return out


def _same_shift_history(work_date: str, shift: str, weeks: int = 4):
    d = date.fromisoformat(work_date)
    return [((d - timedelta(days=7 * k)).isoformat(), shift) for k in range(1, weeks + 1)]


def recent_shifts(db, n: int = 3) -> list[dict]:
    """The last `n` shifts, newest first, with counts, revenue by kind and a 4-week comparison."""
    pairs = latest_shifts(db, n)
    if not pairs:
        return []
    where, params = _pairs_clause(pairs)
    stats = {p: {"invoices": 0, "open": 0, "closed": 0, "zero": 0, "closers": ""} for p in pairs}
    for r in db.execute(
        f"""SELECT i.work_date, i.shift, COUNT(*) AS n,
                   SUM(i.status = 'open') AS open_n, SUM(i.status = 'closed') AS closed_n,
                   SUM(i.status = 'closed' AND COALESCE(i.total_amount, 0) = 0) AS zero_n,
                   GROUP_CONCAT(DISTINCT CASE WHEN i.status = 'closed'
                                THEN COALESCE(u.full_name, i.closed_by) END) AS closers
            FROM invoices i LEFT JOIN users u ON u.username = i.closed_by
            WHERE {where} GROUP BY i.work_date, i.shift""",
        params,
    ):
        stats[(r["work_date"], r["shift"])] = {
            "invoices": r["n"],
            "open": r["open_n"] or 0,
            "closed": r["closed_n"] or 0,
            "zero": r["zero_n"] or 0,
            "closers": (r["closers"] or "").replace(",", "، "),
        }
    deletes = dict.fromkeys(pairs, 0)
    for r in db.execute(
        f"""SELECT i.work_date, i.shift, COUNT(*) AS n FROM activity_logs a JOIN invoices i ON i.id = a.invoice_id
            WHERE a.action_type IN ({",".join("?" * len(DELETE_ACTIONS))}) AND ({where})
            GROUP BY i.work_date, i.shift""",
        [*DELETE_ACTIONS, *params],
    ):
        deletes[(r["work_date"], r["shift"])] = r["n"]
    unset = dict.fromkeys(pairs, 0)
    for r in db.execute(
        f"""SELECT i.work_date, i.shift, COUNT(*) AS n FROM invoice_item_payments p JOIN invoices i ON i.id = p.invoice_id
            WHERE p.payment_type IS NULL AND i.status = 'closed' AND ({where}) GROUP BY i.work_date, i.shift""",
        params,
    ):
        unset[(r["work_date"], r["shift"])] = r["n"]

    history = {p: _same_shift_history(*p) for p in pairs}
    revenue = _revenue_by_pair(db, sorted({*pairs, *(h for hs in history.values() for h in hs)}))

    shifts = []
    for index, p in enumerate(pairs):
        rev = revenue[p]
        total = rev["visits"] + rev["nursing"] + rev["procedures"]
        past = [sum(revenue[h][k] for k in ("visits", "nursing", "procedures")) for h in history[p]]
        past = [v for v in past if v > 0]
        baseline = sum(past) / len(past) if len(past) >= 3 else None
        # The newest shift with open invoices is still running: comparing half a shift to whole
        # ones would read as a crash, so it gets a label instead of a percentage.
        in_progress = index == 0 and stats[p]["open"] > 0
        shifts.append(
            {
                "work_date": p[0],
                "shift": p[1],
                "date_fa": jalali_date(p[0]),
                "shift_fa": SHIFT_FA.get(p[1], p[1]),
                **stats[p],
                "revenue": total,
                "visits": rev["visits"],
                "nursing": rev["nursing"],
                "procedures": rev["procedures"],
                "consumables": rev["consumables"],
                "deletes": deletes[p],
                "unset_payments": unset[p],
                "baseline": baseline,
                "in_progress": in_progress,
                "vs_baseline": ((total - baseline) / baseline) if baseline and not in_progress else None,
            }
        )
    return shifts


# --------------------------------------------------------------------------- attention

ATTENTION_TITLES = {
    "open_invoices": "فاکتور باز قدیمی",
    "unset_payment": "آیتم بدون نوع پرداخت",
    "empty_closed": "فاکتور بستهٔ خالی",
    "heavy_deletes": "حذف زیاد آیتم توسط یک کاربر",
    "locked_users": "حساب قفل‌شده",
}


def _window_start(db) -> str:
    ref = date.fromisoformat(reference_work_date(db))
    return (ref - timedelta(days=LOOKBACK_DAYS - 1)).isoformat()


def _now_text(now: datetime | None) -> str:
    return (now or iran_now()).strftime("%Y-%m-%d %H:%M:%S")


def _open_invoices(db, now):
    hours = get_threshold(db, "stale_open_hours")
    rows = db.execute(
        """SELECT i.id, i.opened_at, COALESCE(u.full_name, i.opened_by) AS who, i.insurance_type, i.total_amount
           FROM invoices i LEFT JOIN users u ON u.username = i.opened_by
           WHERE i.status = 'open' AND (julianday(?) - julianday(i.opened_at)) * 24 > ?
           ORDER BY i.opened_at""",
        (_now_text(now), hours),
    ).fetchall()
    return (
        ["شماره فاکتور", "باز شدن", "پذیرش", "بیمه", "مبلغ"],
        [
            [
                r["id"],
                format_jalali_datetime(r["opened_at"]),
                r["who"] or "",
                r["insurance_type"] or "",
                int(r["total_amount"] or 0),
            ]
            for r in rows
        ],
        f"بازمانده بیش از {hours} ساعت",
    )


def _unset_payment(db, now):
    rows = db.execute(
        """SELECT i.id, i.work_date, i.shift, COALESCE(u.full_name, i.closed_by) AS who, COUNT(*) AS n
           FROM invoice_item_payments p JOIN invoices i ON i.id = p.invoice_id
           LEFT JOIN users u ON u.username = i.closed_by
           WHERE p.payment_type IS NULL AND i.status = 'closed' AND i.work_date >= ?
           GROUP BY i.id ORDER BY i.work_date DESC, i.id DESC""",
        (_window_start(db),),
    ).fetchall()
    return (
        ["شماره فاکتور", "تاریخ کاری", "شیفت", "بستن توسط", "تعداد آیتم"],
        [
            [r["id"], jalali_date(r["work_date"]), SHIFT_FA.get(r["shift"], r["shift"] or ""), r["who"] or "", r["n"]]
            for r in rows
        ],
        f"{LOOKBACK_DAYS} روز کاری اخیر",
    )


def _empty_closed(db, now):
    rows = db.execute(
        """SELECT i.id, i.work_date, i.shift, COALESCE(u.full_name, i.closed_by) AS who, i.closed_at
           FROM invoices i LEFT JOIN users u ON u.username = i.closed_by
           WHERE i.status = 'closed' AND i.work_date >= ?
             AND NOT EXISTS (SELECT 1 FROM visits t WHERE t.invoice_id = i.id)
             AND NOT EXISTS (SELECT 1 FROM injections t WHERE t.invoice_id = i.id)
             AND NOT EXISTS (SELECT 1 FROM procedures t WHERE t.invoice_id = i.id)
             AND NOT EXISTS (SELECT 1 FROM consumables_ledger t WHERE t.invoice_id = i.id)
           ORDER BY i.work_date DESC, i.id DESC""",
        (_window_start(db),),
    ).fetchall()
    return (
        ["شماره فاکتور", "تاریخ کاری", "شیفت", "بستن توسط", "زمان بستن"],
        [
            [
                r["id"],
                jalali_date(r["work_date"]),
                SHIFT_FA.get(r["shift"], r["shift"] or ""),
                r["who"] or "",
                format_jalali_datetime(r["closed_at"]),
            ]
            for r in rows
        ],
        f"{LOOKBACK_DAYS} روز کاری اخیر",
    )


def _heavy_deletes(db, now):
    threshold = get_threshold(db, "anomaly_delete_threshold")
    rows = db.execute(
        f"""SELECT a.username, DATE(a.created_at) AS day, COUNT(*) AS n, COALESCE(SUM(a.amount), 0) AS amount
            FROM activity_logs a
            WHERE a.action_type IN ({",".join("?" * len(DELETE_ACTIONS))}) AND DATE(a.created_at) >= ?
            GROUP BY a.username, DATE(a.created_at) HAVING COUNT(*) >= ?
            ORDER BY day DESC, n DESC""",
        [*DELETE_ACTIONS, _window_start(db), threshold],
    ).fetchall()
    return (
        ["کاربر", "تاریخ", "تعداد حذف", "مجموع مبلغ حذف‌شده"],
        [[r["username"] or "", jalali_date(r["day"]), r["n"], int(r["amount"] or 0)] for r in rows],
        f"حداقل {threshold} حذف در یک روز",
    )


def _locked_users(db, now):
    rows = db.execute(
        "SELECT username, full_name, role, locked_until FROM users WHERE locked_until IS NOT NULL AND locked_until > ? ORDER BY locked_until",
        (_now_text(now),),
    ).fetchall()
    return (
        ["نام کاربری", "نام", "نقش", "قفل تا"],
        [[r["username"], r["full_name"] or "", r["role"], format_jalali_datetime(r["locked_until"])] for r in rows],
        "پس از ۵ رمز اشتباه، ۱۵ دقیقه قفل می‌شود",
    )


_DETAIL = {
    "open_invoices": _open_invoices,
    "unset_payment": _unset_payment,
    "empty_closed": _empty_closed,
    "heavy_deletes": _heavy_deletes,
    "locked_users": _locked_users,
}


def attention_detail(db, key: str, now: datetime | None = None):
    """(title, note, headers, rows) for one attention item, or None for an unknown key."""
    builder = _DETAIL.get(key)
    if builder is None:
        return None
    headers, rows, note = builder(db, now)
    return ATTENTION_TITLES[key], note, headers, rows


def _system_items(backup_dir: str | None, now: datetime | None) -> list[dict]:
    from src.services.backup import latest_backup_age

    if not backup_dir:
        return []
    age = latest_backup_age(backup_dir, now)
    if age is None:
        return [
            {
                "key": "backup",
                "title": "هیچ پشتیبانی وجود ندارد",
                "count": 1,
                "note": "در تنظیمات یک پشتیبان بسازید",
                "href": "settings",
            }
        ]
    if age > timedelta(hours=BACKUP_WARN_HOURS):
        hours = int(age.total_seconds() // 3600)
        return [
            {
                "key": "backup",
                "title": "آخرین پشتیبان قدیمی است",
                "count": hours,
                "unit": "ساعت",
                "note": "پشتیبان‌گیری خودکار انجام نشده؟",
                "href": "settings",
            }
        ]
    return []


def attention_items(db, backup_dir: str | None = None, now: datetime | None = None) -> list[dict]:
    """Exceptions only; an empty list means nothing needs the manager."""
    items = []
    for key in ("open_invoices", "unset_payment", "empty_closed", "heavy_deletes", "locked_users"):
        _headers, rows, note = _DETAIL[key](db, now)
        if rows:
            items.append(
                {"key": key, "title": ATTENTION_TITLES[key], "count": len(rows), "note": note, "href": "detail"}
            )
    return items + _system_items(backup_dir, now)


# --------------------------------------------------------------------------- health


def system_health(db, db_path: str, backup_dir: str | None, app_version: str, now: datetime | None = None) -> dict:
    from src.services.backup import latest_backup_age

    age = latest_backup_age(backup_dir, now) if backup_dir else None
    try:
        size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 1)
    except OSError:
        size_mb = None
    return {
        "app_version": app_version,
        "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
        "db_size_mb": size_mb,
        "backup_age_hours": None if age is None else int(age.total_seconds() // 3600),
        "backup_ok": age is not None and age <= timedelta(hours=BACKUP_WARN_HOURS),
    }
