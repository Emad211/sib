"""The receipt and invoice photos are read back through a `blob:` URL (shrink on the phone, preview).

`img-src 'self'` alone makes every browser refuse that image, so the photo step reports "could not
read the photo" even though the file arrived. Found on a real phone inside the Android WebView.
The policy must allow `blob:` for images and nothing wider.
"""

from fastapi.testclient import TestClient


def _directives(policy: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            parsed[tokens[0]] = tokens[1:]
    return parsed


def test_images_may_come_from_blob_urls_but_not_from_elsewhere(client: TestClient) -> None:
    policy = client.get("/").headers["content-security-policy"]
    images = _directives(policy)["img-src"]
    assert images == ["'self'", "blob:"]
