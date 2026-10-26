"""Compare two SQLite schemas and flag anything that is not additive (ADR-002, A03 §5).

python tools/schema_diff.py OLD.db NEW.db          # exit 1 on a non-additive change
python tools/schema_diff.py --dump DB.db OUT.sql  # schema only, no data
"""

from __future__ import annotations

import sqlite3
import sys


def describe(path: str) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            cols = {
                r[1]: {"type": r[2].upper(), "notnull": r[3], "default": r[4], "pk": r[5], "hidden": r[6]}
                for r in conn.execute(f'PRAGMA table_xinfo("{name}")')
            }
            tables[name] = cols
        indexes = {
            r[0]: r[1]
            for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")
        }
        views = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='view'")}
        triggers = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    return {"tables": tables, "indexes": indexes, "views": views, "triggers": triggers, "user_version": version}


def diff(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """Returns (violations, additions). Violations break ADR-002."""
    bad, added = [], []
    for t, cols in old["tables"].items():
        if t not in new["tables"]:
            bad.append(f"table removed: {t}")
            continue
        for c, spec in cols.items():
            n = new["tables"][t].get(c)
            if n is None:
                bad.append(f"column removed: {t}.{c}")
            elif n["type"] != spec["type"]:
                bad.append(f"column type changed: {t}.{c} {spec['type']} -> {n['type']}")
            elif n["notnull"] and not spec["notnull"]:
                bad.append(f"NOT NULL added to existing column: {t}.{c}")
        for c, spec in new["tables"][t].items():
            if c not in cols:
                if spec["notnull"] and spec["default"] is None and not spec["pk"]:
                    bad.append(f"new column {t}.{c} is NOT NULL without default")
                else:
                    added.append(f"column added: {t}.{c}")
    for t in new["tables"]:
        if t not in old["tables"]:
            added.append(f"table added: {t}")
    for kind in ("indexes", "views"):
        for name in old[kind]:
            if name not in new[kind]:
                bad.append(f"{kind[:-2] if kind == 'indexes' else 'view'} removed: {name}")
        for name in new[kind]:
            if name not in old[kind]:
                added.append(f"{'index' if kind == 'indexes' else 'view'} added: {name}")
    for name in new["triggers"]:
        if name not in old["triggers"]:
            added.append(f"trigger added: {name} (review: must not modify data)")
    if new["user_version"] < old["user_version"]:
        bad.append(f"user_version went backwards {old['user_version']} -> {new['user_version']}")
    return bad, added


def dump(path: str) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 WHEN 'view' THEN 2 ELSE 3 END, name"
    ).fetchall()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    return f"-- user_version = {version}\n" + "\n".join(f"{sql};" for _, _, sql in rows) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--dump":
        with open(argv[2], "w", encoding="utf-8", newline="\n") as f:
            f.write(dump(argv[1]))
        return
    bad, added = diff(describe(argv[0]), describe(argv[1]))
    for a in added:
        print("  +", a)
    for b in bad:
        print("  !", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
