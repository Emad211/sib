"""Canonical JSON and SHA-256 primitives for the append-only event hash chain."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 text preserved."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_hex(value: str | bytes) -> str:
    """Hash text as UTF-8, or bytes as-is, into a lowercase hex digest."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def chain_hash(previous_hash: str, material: Mapping[str, Any]) -> str:
    """Hash `prev_hash ‖ canonical_json(material)`, the event-log link rule."""
    return sha256_hex(previous_hash + canonical_json(material))
