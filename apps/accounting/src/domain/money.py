"""Amounts: parsing what a person typed and showing it back (ACC-2.2).

The database stores amounts in Toman (Q-23). Tariff forms used to do
`float(text.replace(',', ''))` inside a bare `try/except` that silently turned anything it could not
read into 0. A manager who pasted "۱۲۰٬۰۰۰" (Persian digits and the Arabic thousands separator)
therefore saved a price of 0. `parse_amount` understands those and tells the caller when the text
is not an amount, instead of inventing a price.
"""

from __future__ import annotations

import math
import re

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_SEPARATORS = re.compile(r"[,٬،\s‌‏‎]")
_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")


def normalize_digits(text: str | None) -> str:
    """Persian/Arabic-Indic digits -> ASCII digits; None -> ''."""
    return (text or "").translate(_DIGITS)


def parse_amount(text, default: float | None = None) -> float | None:
    """'120,000' / '۱۲۰٬۰۰۰' / ' 5000 ' -> float. Returns `default` when `text` is not an amount.

    Negative values, NaN and infinity are not amounts. A bare number type (int/float) passes through.
    """
    if text is None:
        return default
    if isinstance(text, bool):
        return default
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        cleaned = _SEPARATORS.sub("", normalize_digits(str(text)))
        if not _NUMBER.fullmatch(cleaned):
            return default
        value = float(cleaned)
    if not math.isfinite(value) or value < 0:
        return default
    return value


def format_amount(value) -> str:
    """12000.0 -> '12,000' (ASCII digits, thousands separator, no decimals unless needed)."""
    number = parse_amount(value, 0.0) or 0.0
    return f"{int(number):,}" if float(number).is_integer() else f"{number:,.2f}"
