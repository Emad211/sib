"""Who may read a receipt photo, and how a path can never leave the store (SHF-7, E02).

A receipt photo is staff-visible evidence, not a public file: uploading needs the same guard as the
event, reading needs `shift_event.approve` (the approver side; the manager holds every permission),
and the URL segment is only ever a 64-character hash. These tests drive the routes with real sessions
and the store directly with hostile names, because a path traversal here would put a camera roll or
the database itself behind an <img> tag.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from synthetic_catalog import seed as seed_catalog

from shift.app import create_app
from shift.config.settings import Settings
from shift.services.photos import PhotoRejected, PhotoStore

JPEG = b"\xff\xd8\xff\xe0" + b"photo" * 40 + b"\xff\xd9"
SHA = hashlib.sha256(JPEG).hexdigest()
READERS = ("manager", "storekeeper", "accountant", "shift_doctor", "multi_role")
OTHERS = ("reception", "nurse", "purchaser")


@pytest.fixture()
def staff_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key, presence=True)
        seed_catalog(client.app.state.database)
        yield client


def _sign_in(client: TestClient, role: str) -> dict[str, str]:
    person = ROLES[role]
    client.cookies.clear()
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return {"X-CSRFToken": response.json()["csrf_token"]}


def test_only_the_approver_side_may_read_a_photo(staff_client: TestClient) -> None:
    """The uploader cannot read its own photo back, and no reader role is left out."""
    sign_in = _sign_in(staff_client, "reception")
    uploaded = staff_client.put(
        f"/api/v1/photos/{SHA}", content=JPEG, headers={**sign_in, "Content-Type": "image/jpeg"}
    )
    assert uploaded.status_code == 201, uploaded.text

    for role in READERS:
        _sign_in(staff_client, role)
        assert staff_client.get(f"/api/v1/photos/{SHA}").status_code == 200, role
    for role in OTHERS:
        _sign_in(staff_client, role)
        assert staff_client.get(f"/api/v1/photos/{SHA}").status_code == 403, role


def test_a_photo_needs_a_session_and_a_hash_to_be_read_or_written(staff_client: TestClient) -> None:
    """Anonymous calls fail closed, and a segment that is not a hash is a 404, not a file name."""
    staff_client.cookies.clear()
    assert staff_client.get(f"/api/v1/photos/{SHA}").status_code == 401
    assert staff_client.put(f"/api/v1/photos/{SHA}", content=JPEG).status_code == 401

    _sign_in(staff_client, "manager")
    for hostile in ("..%2f..%2fshift.db", "../..%2fshift.db", "not-a-hash", "0" * 63, "A" * 64):
        response = staff_client.get(f"/api/v1/photos/{hostile}")
        assert response.status_code in {404, 422}, hostile

    _sign_in(staff_client, "reception")
    refused = staff_client.put(
        "/api/v1/photos/not-a-hash",
        content=JPEG,
        headers={"X-CSRFToken": staff_client.cookies.get("shift_csrf", ""), "Content-Type": "image/jpeg"},
    )
    assert refused.status_code in {403, 404}, "a malformed name is refused before any write"


def test_the_store_holds_no_path_but_a_hash(tmp_path: Path) -> None:
    """Even when a name looks like a file, the store only answers a hash that is already its own."""
    store = PhotoStore(tmp_path)
    stored = store.save(JPEG)
    assert stored == SHA
    assert store.exists(SHA)
    assert store.open_path(SHA).read_bytes() == JPEG

    (tmp_path.parent / "outside.jpg").write_bytes(b"not a photo")
    for hostile in ("../outside.jpg", r"..\outside.jpg", str(tmp_path.parent / "outside.jpg"), "abc"):
        with pytest.raises(PhotoRejected):
            store.open_path(hostile)
        assert store.exists(hostile) is False
