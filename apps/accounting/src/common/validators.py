"""Kept for old imports; the rules live in `src.domain.identity` (ACC-2.2)."""

from src.domain.identity import is_valid_national_id as validate_iranian_national_id
from src.domain.identity import is_valid_phone as validate_iranian_phone

__all__ = ["validate_iranian_national_id", "validate_iranian_phone"]
