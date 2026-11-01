"""S-4, S-17: the pages an unauthenticated visitor can reach carry the baseline security headers."""

import pytest
from fastapi.testclient import TestClient

PAGES = ["/login", "/enroll", "/healthz"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_is_unframeable_and_unsniffable(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_the_login_page_cannot_be_framed_and_still_runs_its_own_script(client: TestClient) -> None:
    csp = client.get("/login").headers["Content-Security-Policy"]

    assert "frame-ancestors 'none'" in csp
    assert "script-src" not in csp, "the login page has inline code; a script policy would break it"
