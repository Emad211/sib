"""Tariff change history: who changed which price, when, from what to what (ACC-2.8, A06 §3.5).

Tariffs drive every invoice total, and the program recomputes shares from the *current* tariff,
so a silent price edit changes money. Each handler in `api/manager.py` calls one of the
`record_*` functions inside the same transaction as the change itself, so a change can never be
saved without its history row (and vice versa). Values are stored as readable Persian text so
the history stays meaningful even if a column is renamed later.
"""

from __future__ import annotations

from collections.abc import Mapping

KIND_LABELS = {
    "nursing": "خدمات پرستاری",
    "insurance": "تعرفهٔ بیمه / ویزیت",
    "consumable": "مصرفی",
    "exclusion": "استثنای پرستاری بیمه",
    "procedure": "کار عملی",
}
ACTION_LABELS = {"create": "افزودن", "update": "ویرایش", "delete": "حذف"}
FIELD_LABELS = {
    "name": "نام",
    "price": "قیمت",
    "nursing_covers": "پوشش پرستاری",
    "active": "وضعیت",
    "supplementary": "بیمهٔ تکمیلی",
    "category": "دسته",
    "acc_code": "کد کالا (انبار)",
    "stock_factor": "ضریب تبدیل به انبار",
    "created": "ایجاد",
    "deleted": "حذف",
    "excluded": "استثنا شد",
    "included": "از استثنا خارج شد",
}


def _username() -> str | None:
    try:
        from flask import g, has_request_context

        if has_request_context() and getattr(g, "user", None):
            return g.user["username"]
    except Exception:  # noqa: BLE001 - auditing must never break the change itself
        pass
    return None


def _fmt(field: str, value) -> str:
    if value is None:
        return ""
    if field == "active":
        return "فعال" if value else "غیرفعال"
    if field in ("nursing_covers", "supplementary"):
        return "بله" if value else "خیر"
    if field == "category":
        return {"drug": "دارو", "supply": "لوازم"}.get(str(value), str(value))
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, int) and field == "price":
        return f"{value:,}"
    return str(value)


def _insert(db, kind, tariff_id, label, action, field, old, new) -> None:
    db.execute(
        "INSERT INTO tariff_changes (username, kind, tariff_id, label, action, field, old_value, new_value)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_username(), kind, tariff_id, label, action, FIELD_LABELS.get(field, field), old, new),
    )


def record_created(db, kind: str, tariff_id: int, label: str, values: Mapping) -> None:
    summary = "، ".join(f"{FIELD_LABELS.get(k, k)}: {_fmt(k, v)}" for k, v in values.items())
    _insert(db, kind, tariff_id, label, "create", "created", None, summary)


def record_deleted(db, kind: str, tariff_id: int, label: str, values: Mapping) -> None:
    summary = "، ".join(f"{FIELD_LABELS.get(k, k)}: {_fmt(k, v)}" for k, v in values.items())
    _insert(db, kind, tariff_id, label, "delete", "deleted", summary, None)


def record_update(db, kind: str, tariff_id: int, label: str, changes: Mapping[str, tuple]) -> int:
    """One row per field that really changed; returns how many rows were written."""
    written = 0
    for field, (old, new) in changes.items():
        if _fmt(field, old) == _fmt(field, new):
            continue
        _insert(db, kind, tariff_id, label, "update", field, _fmt(field, old), _fmt(field, new))
        written += 1
    return written


def record_exclusions(db, insurance_type: str, before: set[int], after: set[int]) -> None:
    names = {r["id"]: r["service_name"] for r in db.execute("SELECT id, service_name FROM nursing_services")}
    for sid in sorted(after - before):
        _insert(db, "exclusion", sid, insurance_type, "update", "excluded", None, names.get(sid, str(sid)))
    for sid in sorted(before - after):
        _insert(db, "exclusion", sid, insurance_type, "update", "included", None, names.get(sid, str(sid)))


def history(db, since: str | None = None, kind: str | None = None, limit: int = 1000) -> list[dict]:
    where, params = [], []
    if since:
        where.append("changed_at >= ?")
        params.append(since)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    sql = "SELECT * FROM tariff_changes" + (" WHERE " + " AND ".join(where) if where else "")
    rows = db.execute(sql + " ORDER BY changed_at DESC, id DESC LIMIT ?", [*params, limit]).fetchall()
    return [
        dict(r)
        | {
            "kind_label": KIND_LABELS.get(r["kind"], r["kind"]),
            "action_label": ACTION_LABELS.get(r["action"], r["action"]),
        }
        for r in rows
    ]
