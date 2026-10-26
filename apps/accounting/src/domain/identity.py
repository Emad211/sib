"""National ID and mobile number rules (ACC-2.2, ACC-2.9). Pure functions, no I/O.

Persian digits are accepted on input and always stored as ASCII, otherwise the same person would
exist twice ("۰۰۱۲۳۴۵۶۷۸" and "0012345678") and search would miss one of them.
"""

from __future__ import annotations

import re

from src.domain.money import normalize_digits


def normalize_national_id(value: str | None) -> str:
    return normalize_digits(value).strip()


def normalize_phone(value: str | None) -> str:
    return normalize_digits(value).strip()


def is_valid_national_id(national_id: str | None) -> bool:
    """The official check-digit rule: 10 digits, not all the same, control digit matches."""
    nid = normalize_national_id(national_id)
    if len(nid) != 10 or not (nid.isascii() and nid.isdigit()):
        return False
    if len(set(nid)) == 1:
        return False
    check = int(nid[9])
    total = sum(int(nid[i]) * (10 - i) for i in range(9))
    remainder = total % 11
    return check == remainder if remainder < 2 else check == 11 - remainder


def is_valid_phone(phone: str | None) -> bool:
    """Iranian mobile: 11 digits starting with 09."""
    value = normalize_phone(phone)
    return len(value) == 11 and value.isascii() and value.isdigit() and value.startswith("09")


# --------------------------------------------------------------------------- names (FB-05)

_ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه"})
_PERSIAN_LETTER = re.compile(r"^[\u0621-\u063A\u0641-\u064A\u067E\u0686\u0698\u06A9\u06AF\u06CC\u200c]+$")
# Words people type when they do not want to ask for the real name.
PLACEHOLDER_NAMES = frozenset(
    {"الف", "ب", "بیمار", "مریض", "ناشناس", "نامشخص", "مجهول", "تست", "ندارد", "فلانی", "اقا", "آقا", "خانم"}
)


def normalize_person_name(value: str | None) -> str:
    """Trim, collapse spaces, Arabic ي/ك -> Persian ی/ک (so search and duplicate checks match)."""
    return " ".join((value or "").translate(_ARABIC_TO_PERSIAN).split())


def person_name_problem(value: str | None) -> str | None:
    """None when this looks like a real first or last name; otherwise why not (Persian message).

    Every word must be Persian letters only and at least 2 letters long, so 'خ باقری' or 'الف'
    are refused, while 'سید محمد' and 'عبدالله‌زاده' pass.
    """
    name = normalize_person_name(value)
    if not name:
        return "خالی است"
    words = name.split(" ")
    if any(not _PERSIAN_LETTER.match(w) for w in words):
        return "فقط حروف فارسی مجاز است (بدون عدد و نشانه)"
    if any(len(w.replace("\u200c", "")) < 2 for w in words):
        return "حرف تنها یا مخفف پذیرفته نیست؛ نام کامل را بنویسید"
    if name in PLACEHOLDER_NAMES:
        return "نام واقعی بیمار را بنویسید"
    return None


# --------------------------------------------------------------------------- foreign nationals (FB-03)

_FOREIGN_CODE = re.compile(r"^\d{12}$")  # کد فراگیر اتباع
_PASSPORT = re.compile(r"^[A-Z0-9]{6,12}$")


def normalize_foreign_id(value: str | None) -> str:
    return normalize_digits(value).strip().replace(" ", "").upper()


def is_valid_foreign_id(value: str | None) -> bool:
    """A foreign national's 12-digit universal code, or a passport number (6-12 letters/digits)."""
    v = normalize_foreign_id(value)
    return bool(_FOREIGN_CODE.match(v) or (_PASSPORT.match(v) and not v.isdigit()))
