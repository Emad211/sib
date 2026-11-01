"""Receipt photos on the server (SHF-7): upload by content hash, read by the approver side.

The photo is evidence for the accountant, so the rules that matter are integrity and backup: the
bytes must hash to the name the phone claims, the event that names them must not point at a photo the
store does not hold, and a backup must carry the photos it does not hold yet exactly once.
"""

import hashlib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from synthetic_catalog import seed as seed_catalog

from shift.app import create_app
from shift.common.clock import TEHRAN
from shift.config.settings import Settings
from shift.services.backup import BackupService
from shift.services.login_throttle import LoginThrottle

EVENTS_URL = "/api/v1/events"
PHOTOS_URL = "/api/v1/photos"
JPEG = b"\xff\xd8\xff\xe0" + b"receipt" * 30 + b"\xff\xd9"
SHA = hashlib.sha256(JPEG).hexdigest()


@pytest.fixture()
def staff_client(settings: Settings) -> Iterator[TestClient]:
    """One app on the synthetic roster and reference catalog; every test signs in itself."""
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_catalog(client.app.state.database)
        yield client


def _headers(client: TestClient, role: str) -> dict[str, str]:
    """A real login's CSRF header; the client keeps the session cookie it was issued."""
    person = ROLES[role]
    client.cookies.clear()
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return {"X-CSRFToken": response.json()["csrf_token"]}


def _upload(client: TestClient, body: bytes = JPEG, sha: str = SHA, content_type: str = "image/jpeg"):
    return client.put(
        f"{PHOTOS_URL}/{sha}",
        content=body,
        headers={**_headers(client, "reception"), "Content-Type": content_type},
    )


def test_a_photo_is_stored_under_its_hash_and_served_to_the_approver(
    staff_client: TestClient, settings: Settings
) -> None:
    """The uploader names the hash; the manager reads the same bytes; the uploader cannot read them."""
    uploaded = _upload(staff_client)

    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json() == {"sha256": SHA, "bytes": len(JPEG)}
    stored = settings.resolved_photos_dir / SHA[:2] / f"{SHA}.jpg"
    assert stored.read_bytes() == JPEG, "the file is exactly what the phone sent"

    _headers(staff_client, "manager")
    served = staff_client.get(f"{PHOTOS_URL}/{SHA}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["cache-control"] == "private, no-store"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content == JPEG

    _headers(staff_client, "reception")
    assert staff_client.get(f"{PHOTOS_URL}/{SHA}").status_code == 403


def test_an_upload_must_be_a_jpeg_that_hashes_to_its_name(staff_client: TestClient) -> None:
    """A wrong name, a wrong type or a body over the cap is refused; nothing is stored."""
    wrong_name = _upload(staff_client, sha="b" * 64)
    assert wrong_name.status_code == 400
    assert "هش" in wrong_name.text

    not_jpeg = _upload(staff_client, content_type="text/plain")
    assert not_jpeg.status_code == 415

    oversized = _upload(staff_client, body=JPEG + b"\x00" * (1024 * 1024))
    assert oversized.status_code == 413

    assert list(staff_client.app.state.settings.resolved_photos_dir.rglob("*.jpg")) == []


def test_an_event_may_only_name_a_photo_the_store_holds(staff_client: TestClient) -> None:
    """The phone uploads first; an event whose hash has no file is refused before anything moves."""
    missing = "c" * 64
    headers = _headers(staff_client, "reception")
    refused = staff_client.post(
        EVENTS_URL,
        json={
            "type": "card.transfer_reported",
            "payload": {"amount": 120_000, "admission_no": "A-1010", "photo_sha256": missing},
            "client_event_id": "01J00000000000000000000101",
            "idempotency_key": "01J00000000000000000000101",
            "client_at": datetime.now(TEHRAN).isoformat(),
        },
        headers=headers,
    )
    assert refused.status_code == 422
    assert "عکس" in refused.text

    _upload(staff_client)
    headers = _headers(staff_client, "reception")
    accepted = staff_client.post(
        EVENTS_URL,
        json={
            "type": "card.transfer_reported",
            "payload": {"amount": 120_000, "admission_no": "A-1010", "photo_sha256": SHA},
            "client_event_id": "01J00000000000000000000102",
            "idempotency_key": "01J00000000000000000000102",
            "client_at": datetime.now(TEHRAN).isoformat(),
        },
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "recorded"


def test_a_backup_copies_the_photos_once(staff_client: TestClient, settings: Settings, tmp_path: Path) -> None:
    """Content-addressed photos are copied when missing, kept as-is, and counted in the report."""
    _upload(staff_client)
    backup = BackupService(
        settings.database_path,
        backup_dir=tmp_path / "backups",
        keep_snapshots=3,
        photos_dir=settings.resolved_photos_dir,
    )

    first = backup.create()
    assert first.photos_copied == 1
    copy = tmp_path / "backups" / "photos" / SHA[:2] / f"{SHA}.jpg"
    assert copy.read_bytes() == JPEG

    second = backup.create()
    assert second.photos_copied == 0, "an immutable photo is copied once, not on every snapshot"


def test_a_flood_of_photos_from_one_person_is_refused(staff_client: TestClient) -> None:
    """S-14: a phone sends a few photos an hour; sixty in ten minutes is a flood that would fill the disk."""
    staff_client.app.state.photo_throttle = LoginThrottle(attempts=3)

    codes = [_upload(staff_client).status_code for _ in range(5)]

    assert codes == [201, 201, 201, 429, 429]
