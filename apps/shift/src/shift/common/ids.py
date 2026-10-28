"""ULID generation for client-minted event identities (offline-safe, sortable)."""

import re
import secrets
from datetime import UTC, datetime

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_LENGTH = 26
_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_TIMESTAMP_CHARS = 10
_RANDOM_BITS = 80


def _encode(value: int, length: int) -> str:
    characters = []
    for _ in range(length):
        characters.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(characters))


def _decode(text: str) -> int:
    value = 0
    for character in text:
        value = (value << 5) | ALPHABET.index(character)
    return value


def new_ulid(at: datetime | None = None) -> str:
    """Mint a ULID; `at` must be timezone-aware when supplied (tests use it)."""
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("ULID timestamp must be timezone-aware")
    timestamp_ms = int(moment.timestamp() * 1000)
    if not 0 <= timestamp_ms < 1 << 48:
        raise ValueError("ULID timestamp is outside the 48-bit range")
    value = (timestamp_ms << _RANDOM_BITS) | int.from_bytes(secrets.token_bytes(10), "big")
    return _encode(value, ULID_LENGTH)


def is_ulid(value: str) -> bool:
    """True when `value` is exactly 26 Crockford base32 characters."""
    return isinstance(value, str) and bool(_ULID_PATTERN.fullmatch(value))


def ulid_timestamp(value: str) -> datetime:
    """Return the UTC instant encoded in a ULID."""
    if not is_ulid(value):
        raise ValueError("not a ULID")
    milliseconds = _decode(value[:_TIMESTAMP_CHARS])
    return datetime.fromtimestamp(milliseconds / 1000, UTC)
