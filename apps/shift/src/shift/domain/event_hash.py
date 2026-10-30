"""The exact event fields covered by the hash chain and their canonical form."""

import json
from collections.abc import Mapping
from typing import Any

from shift.common.hashing import chain_hash

HASH_FIELDS = (
    "event_id",
    "type",
    "schema_v",
    "payload",
    "actor_id",
    "device_id",
    "shift_id",
    "work_date",
    "client_at",
    "server_at",
    "at_tehran",
    "ref_event_id",
    "idempotency_key",
)


def hash_material(record: Mapping[str, Any] | object) -> dict[str, Any]:
    """Select the hashed fields; accepts a mapping or a record object."""

    def value(name: str) -> Any:
        return record[name] if isinstance(record, Mapping) else getattr(record, name)

    material: dict[str, Any] = {field: value(field) for field in HASH_FIELDS}
    payload = material["payload"]
    if isinstance(payload, str):
        material["payload"] = json.loads(payload)
    return material


def event_hash(previous_hash: str, record: Mapping[str, Any] | object) -> str:
    """Hash one event into the chain (`prev_hash ‖ canonical material`)."""
    return chain_hash(previous_hash, hash_material(record))
