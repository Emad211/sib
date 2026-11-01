"""A synthetic receipt photo for the kinds that must carry one (owner decision 2026-11-02, A5).

A card transfer, a cash withdrawal, waste and a broken device are refused without their photo. Tests that
are about something else attach this one; a route test also puts it in the app's store, because the
events route only accepts a photo the server already holds.
"""

import hashlib
from typing import Any

PHOTO_KINDS = frozenset(
    {"card.transfer_reported", "cash.withdrawn", "stock.waste_reported", "equipment.fault_reported"}
)
RECEIPT_JPEG = b"\xff\xd8\xff\xdb" + b"synthetic receipt, no patient data" + b"\xff\xd9"
RECEIPT_SHA = hashlib.sha256(RECEIPT_JPEG).hexdigest()


def with_photo(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The payload with the synthetic receipt when its kind requires a photo and it names none."""
    if event_type in PHOTO_KINDS and payload.get("photo_sha256") is None:
        return {**payload, "photo_sha256": RECEIPT_SHA}
    return payload


def keep_receipt(app: Any) -> None:
    """Put the synthetic receipt in a running app's photo store."""
    app.state.photos.save(RECEIPT_JPEG)
