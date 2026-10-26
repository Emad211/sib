"""Golden master for every report, export, chart and payroll calculation (ACC-0.6).

Runs the application against a *temporary copy* of a production snapshot with the
clock frozen, requests a fixed list of pages, and stores for each one: HTTP status,
the ordered list of numbers on the page/CSV, and a SHA-256 of the body.

The baseline contains real financial aggregates, so it lives in sib/_data/golden/
(outside git). Only its SHA-256 is committed (tests/golden/<snapshot>.sha256).

    python tools/golden_master.py record  --db ../../_data/prod_snapshot/X.db
    python tools/golden_master.py compare --db ../../_data/prod_snapshot/X.db

Compare fails on any difference in status or numbers. HTML hash differences are
reported separately (cosmetic changes are allowed when numbers are identical).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import date

_CLOCK = (time.perf_counter,)  # tuple: freezegun only patches direct module attributes

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT = os.path.dirname(_HERE)
_SIB_ROOT = os.path.dirname(os.path.dirname(_APP_ROOT))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

GOLDEN_DIR = os.path.join(_SIB_ROOT, "_data", "golden")
HASH_DIR = os.path.join(_APP_ROOT, "tests", "golden")
# Frozen "now": 2026-09-24 20:30 Tehran (the last day of the 2026-09-24 snapshot).
FROZEN_UTC = "2026-09-24 17:00:00"

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NUM = re.compile(r"-?\d[\d,،٬]*(?:\.\d+)?")


def numbers(text: str) -> list[str]:
    """Ordered list of numbers in a page, Persian digits and separators normalised."""
    text = text.translate(_FA)
    return [re.sub(r"[,،٬]", "", m) for m in _NUM.findall(text)]


def jalali_months(first: date, last: date) -> list[tuple[str, str]]:
    import jdatetime

    out, cur = [], jdatetime.date.fromgregorian(date=first).replace(day=1)
    end = jdatetime.date.fromgregorian(date=last)
    while cur <= end:
        nxt = (cur.replace(day=28) + jdatetime.timedelta(days=5)).replace(day=1)
        last_day = nxt - jdatetime.timedelta(days=1)
        out.append((cur.strftime("%Y/%m/%d"), last_day.strftime("%Y/%m/%d")))
        cur = nxt
    return out


def request_plan(conn: sqlite3.Connection) -> list[dict]:
    first, last = conn.execute("SELECT MIN(work_date), MAX(work_date) FROM invoices").fetchone()
    months = jalali_months(date.fromisoformat(first), date.fromisoformat(last))
    full = (months[0][0], months[-1][1])
    plan: list[dict] = [
        {"method": "GET", "url": "/manager/"},
        {"method": "GET", "url": "/manager/payroll"},
        {"method": "GET", "url": "/manager/tariffs"},
        {"method": "GET", "url": "/manager/users"},
    ]
    reports = ["invoices", "visits", "nursing", "procedures", "users", "consumables", "patients"]
    for fr, to in months + [full]:
        q = f"?from={fr}&to={to}"
        for r in reports:
            plan.append({"method": "GET", "url": f"/manager/reports/{r}{q}"})
            plan.append({"method": "GET", "url": f"/manager/export/{r}/csv{q}"})
        plan.append({"method": "GET", "url": f"/manager/insurance_arrears{q}"})
        plan.append({"method": "GET", "url": f"/manager/insurance_arrears/export{q}"})
        y1, m1, d1 = fr.split("/")
        y2, m2, d2 = to.split("/")
        plan.append(
            {
                "method": "POST",
                "url": "/manager/payroll/calculate",
                "data": {
                    "staff_type": "all",
                    "shift": "all",
                    "from_year": y1,
                    "from_month": m1,
                    "from_day": d1,
                    "to_year": y2,
                    "to_month": m2,
                    "to_day": d2,
                },
            }
        )
    for t in ["revenue", "invoices", "patients", "visits", "injections", "procedures", "consumables", "services"]:
        plan.append({"method": "GET", "url": f"/manager/api/chart-data?from={full[0]}&to={full[1]}&type={t}"})
    return plan


def run(snapshot: str) -> dict:
    from freezegun import freeze_time

    with tempfile.TemporaryDirectory(prefix="golden_") as tmp:
        db_copy = os.path.join(tmp, "copy.db")
        src = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        dst = sqlite3.connect(db_copy)
        src.backup(dst)
        dst.close()
        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        plan = request_plan(conn)
        manager_id = conn.execute(
            "SELECT id FROM users WHERE role='manager' AND is_active=1 ORDER BY id LIMIT 1"
        ).fetchone()[0]
        conn.close()
        src.close()

        import src.adapters.sqlite.core as core
        from src.app import create_app
        from src.config.settings import Config

        Config.DATABASE_PATH = db_copy
        core._migrations_done = False
        cfg = {k: getattr(Config, k) for k in dir(Config) if k.isupper()}
        cfg.update(
            {
                "TESTING": True,
                "DATABASE_PATH": db_copy,
                "SECRET_KEY": "golden",
                "BACKUP_FOLDER": os.path.join(tmp, "backups"),
                "PROPAGATE_EXCEPTIONS": False,
            }
        )
        result = {}
        with freeze_time(FROZEN_UTC):
            app = create_app(cfg)
            app.logger.setLevel(logging.CRITICAL)
            client = app.test_client()
            with client.session_transaction() as s:
                s["user_id"], s["role"] = manager_id, "manager"
                s["_csrf"] = "golden-csrf"  # payroll POSTs go through the CSRF check (SEC-05)
            for req in plan:
                key = f"{req['method']} {req['url']}"
                t0 = _CLOCK[0]()
                resp = client.open(
                    req["url"], method=req["method"], data=req.get("data", {}), headers={"X-CSRFToken": "golden-csrf"}
                )
                ms = round((_CLOCK[0]() - t0) * 1000)
                body = resp.get_data()
                text = body.decode("utf-8", errors="replace")
                result[key] = {
                    "status": resp.status_code,
                    "numbers": numbers(text),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "ms": ms,
                }
        return result


def _baseline_path(snapshot: str) -> str:
    return os.path.join(GOLDEN_DIR, os.path.splitext(os.path.basename(snapshot))[0] + ".json")


def _hash_path(snapshot: str) -> str:
    return os.path.join(HASH_DIR, os.path.splitext(os.path.basename(snapshot))[0] + ".sha256")


def _canonical(result: dict) -> bytes:
    return json.dumps(
        {k: {"status": v["status"], "numbers": v["numbers"]} for k, v in result.items()},
        ensure_ascii=False,
        sort_keys=True,
    ).encode()


def record(snapshot: str) -> dict:
    result = run(snapshot)
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    os.makedirs(HASH_DIR, exist_ok=True)
    with open(_baseline_path(snapshot), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=0, sort_keys=True)
    with open(_hash_path(snapshot), "w", encoding="utf-8") as f:
        f.write(hashlib.sha256(_canonical(result)).hexdigest() + "\n")
    return result


def compare(snapshot: str) -> tuple[list[str], list[str]]:
    """Returns (number/status differences, html-only differences)."""
    with open(_baseline_path(snapshot), encoding="utf-8") as f:
        base = json.load(f)
    with open(_hash_path(snapshot), encoding="utf-8") as f:
        committed = f.read().strip()
    if hashlib.sha256(_canonical(base)).hexdigest() != committed:
        return ["baseline file does not match the committed hash (tests/golden) — re-record deliberately"], []
    now = run(snapshot)
    hard, soft = [], []
    for key in sorted(set(base) | set(now)):
        b, n = base.get(key), now.get(key)
        if b is None or n is None:
            hard.append(f"{key}: {'new' if b is None else 'missing'} request")
        elif b["status"] != n["status"]:
            hard.append(f"{key}: status {b['status']} -> {n['status']}")
        elif b["numbers"] != n["numbers"]:
            first = next(
                i for i, (x, y) in enumerate(zip(b["numbers"] + [None], n["numbers"] + [None], strict=False)) if x != y
            )
            hard.append(
                f"{key}: numbers differ from position {first} ({len(b['numbers'])} -> {len(n['numbers'])} numbers)"
            )
        elif b["sha256"] != n["sha256"]:
            soft.append(f"{key}: same numbers, different markup")
    return hard, soft


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["record", "compare"])
    ap.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    if a.mode == "record":
        r = record(a.db)
        errors = sorted(k for k, v in r.items() if v["status"] >= 500)
        print(f"recorded {len(r)} requests -> {_baseline_path(a.db)}")
        print(f"server errors in baseline: {len(errors)}")
        slow = sorted(r.items(), key=lambda kv: -kv[1]["ms"])[:10]
        print("slowest:")
        for k, v in slow:
            print(f"  {v['ms']} ms  {k}")
        for e in errors:
            print("  500", e)
    else:
        hard, soft = compare(a.db)
        for line in hard:
            print("DIFF", line)
        for line in soft:
            print("markup", line)
        print(f"{len(hard)} differences, {len(soft)} markup-only changes")
        sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
