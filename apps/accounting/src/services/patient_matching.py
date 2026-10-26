"""Duplicate and conflict checks before a patient is created or reused (FB-04).

Opening an invoice with a national ID that already exists *reuses* that patient and overwrites
their name and phone with what was typed. One typo in the ID therefore renamed someone else. Now:

* same ID, different name  -> ask: is this the same person (then update), or is the ID wrong?
* same phone, other person -> ask: family member sharing a phone, or a mistake?

The receptionist answers by choosing an existing patient or confirming (`confirm=1`).
"""

from __future__ import annotations

from src.domain import identity, jalali

MAX_MATCHES = 5


def _match(row) -> dict:
    return {
        "id": row["id"],
        "full_name": f"{row['name'] or ''} {row['family_name'] or ''}".strip(),
        "national_id": row["national_id"] or "",
        "phone": row["phone_number"] or "",
        "insurance_type": row["insurance_type"] or "",
        "last_visit_fa": jalali.jalali_text(row["last_visit"], ""),
    }


_SELECT = """SELECT p.id, p.name, p.family_name, p.national_id, p.phone_number, p.insurance_type,
                    (SELECT MAX(i.work_date) FROM invoices i WHERE i.patient_id = p.id) AS last_visit
             FROM patients p"""


def find_conflicts(db, name: str, family_name: str, national_id: str, phone: str) -> dict | None:
    """None when nothing needs a second look; otherwise {'message', 'matches'}."""
    messages, matches, seen = [], [], set()

    same_id = (
        db.execute(_SELECT + " WHERE p.national_id = ? LIMIT 1", (national_id,)).fetchone() if national_id else None
    )
    if same_id is not None:
        typed = (identity.normalize_person_name(name), identity.normalize_person_name(family_name))
        stored = (
            identity.normalize_person_name(same_id["name"]),
            identity.normalize_person_name(same_id["family_name"]),
        )
        if typed != stored:
            messages.append(
                f"این کد قبلاً برای «{' '.join(p for p in stored if p)}» ثبت شده است. اگر همان بیمار است تأیید کنید "
                "(نام به آنچه نوشتید اصلاح می‌شود)؛ اگر کد اشتباه است، آن را درست کنید."
            )
            matches.append(_match(same_id))
            seen.add(same_id["id"])

    if phone:
        others = db.execute(
            _SELECT + " WHERE p.phone_number = ? AND COALESCE(p.national_id, '') != ? ORDER BY p.id DESC LIMIT ?",
            (phone, national_id or "", MAX_MATCHES),
        ).fetchall()
        others = [r for r in others if r["id"] not in seen]
        if others:
            messages.append(
                f"این شماره همراه برای {len(others)} بیمار دیگر هم ثبت شده است. اگر یکی از همین‌هاست، همان را انتخاب کنید؛ "
                "اگر شمارهٔ مشترک خانواده است، تأیید کنید."
            )
            matches.extend(_match(r) for r in others)

    if not messages:
        return None
    return {"message": " ".join(messages), "matches": matches}
