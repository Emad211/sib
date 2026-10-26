"""National ID and mobile number rules (ACC-2.2, ACC-2.9). Pure functions, no I/O.

Persian digits are accepted on input and always stored as ASCII, otherwise the same person would
exist twice ("۰۰۱۲۳۴۵۶۷۸" and "0012345678") and search would miss one of them.
"""

from __future__ import annotations

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
