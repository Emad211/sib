"""The staff phone screen for shift events (SHF-3, S01 §3, R-10).

The page is the wizard's shell: the vocabulary arrives as one JSON blob built by the registry, the
approval list is filled by the page's own script, and the browser may keep the shell offline
(ADR-016). Exactly three things can hurt the clinic from here, and each is gated below: a person
without `events.append` never receives the page (no session redirects to the login screen), the
catalog holds only the buttons the signed-in permission opens, and the template itself carries no
approval row and no reference figure — the cached shell is served offline, so it must not expose
another person's work or a frozen count (R-02, ADR-011).

The catalog is the synthetic reference data from `tools/seed_reference_data.py`; every value is
invented and no production database or patient data is read (ADR-006).
"""

import json
import re
from collections.abc import Iterator

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from seed_reference_data import seed as seed_catalog

from shift.app import create_app
from shift.config.settings import Settings

CATALOG_BLOB = re.compile(
    r'<script id="shift-event-catalog" type="application/json">(.*?)</script>',
    re.DOTALL,
)
PRECACHE_LIST = re.compile(r"const PRECACHE_URLS = \[(.*?)\];", re.DOTALL)
PENDING_LIST = re.compile(r'<ul id="shift-pending-list"[^>]*>(.*?)</ul>', re.DOTALL)
CATALOG_KEYS = {
    "kinds",
    "items",
    "locations",
    "staff",
    "campaigns",
    "pending_url",
    "approved_type",
    "rejected_type",
}
FIELD_KEYS = {"key", "label", "kind", "required", "options", "max_length", "minimum", "maximum"}
STATUS_ROLES = ("reception", "nurse", "storekeeper")


@pytest.fixture()
def staff_client(settings: Settings) -> Iterator[TestClient]:
    """One app on the synthetic roster and reference catalog; every test signs in itself."""
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_catalog(client.app.state.database)
        yield client


def _signed_in(client: TestClient, role: str = "reception") -> TestClient:
    """Sign in the real way; the client keeps the session cookie it was issued."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return client


def _catalog(html: str) -> dict:
    """The wizard's JSON blob, parsed the way the page's own script parses it."""
    match = CATALOG_BLOB.search(html)
    assert match is not None, "the page carries no shift-event catalog"
    return json.loads(match.group(1))


def test_a_browser_without_a_session_is_sent_to_the_login_page(staff_client: TestClient) -> None:
    response = staff_client.get("/events", headers={"Accept": "text/html"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("role", STATUS_ROLES)
def test_staff_who_may_append_open_the_wizard(staff_client: TestClient, role: str) -> None:
    """Every role holding `events.append` gets the one-job screen, cacheable for offline use."""
    _signed_in(staff_client, role)
    page = staff_client.get("/events")

    assert page.status_code == 200, page.text
    assert page.headers["cache-control"] == "no-cache", "the worker may keep the shell, HTTP may not"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    catalog = _catalog(page.text)
    assert CATALOG_KEYS <= catalog.keys()
    assert catalog["pending_url"] == "/api/v1/shift-events/pending"
    assert (catalog["approved_type"], catalog["rejected_type"]) == ("event.approved", "event.rejected")
    # The wizard has a large button per kind, and every kind describes its own questions.
    assert catalog["kinds"], "no kind reached the phone"
    for kind in catalog["kinds"]:
        assert {"type", "code", "label", "group", "fields", "needs_approval"} <= kind.keys()
        assert kind["group"] in {"money", "stock", "care", "equipment"}
        for field in kind["fields"]:
            assert field.keys() == FIELD_KEYS
    assert {item["location_id"] for item in catalog["items"]} <= {location["id"] for location in catalog["locations"]}
    assert all({"id", "name"} <= item.keys() for item in catalog["items"])
    assert all({"id", "name"} <= person.keys() for person in catalog["staff"])


def test_the_catalog_holds_only_the_buttons_the_permission_opens(staff_client: TestClient) -> None:
    """A receptionist may report waste and a card transfer, never a nurse's vitals (R-07)."""
    _signed_in(staff_client, "reception")
    catalog = _catalog(staff_client.get("/events").text)

    types = {kind["type"] for kind in catalog["kinds"]}
    assert {"stock.waste_reported", "card.transfer_reported"} <= types
    assert "vitals.recorded" not in types


def test_the_served_shell_carries_no_approval_row_and_no_reference(staff_client: TestClient) -> None:
    """The cached shell must not expose another person's pending work or a frozen count (R-02)."""
    _signed_in(staff_client, "reception")
    page = staff_client.get("/events")

    pending = PENDING_LIST.search(page.text)
    assert pending is not None, "the approval section must exist for the script to fill"
    assert pending.group(1).strip() == "", "the template ships approval rows; they are fetched live"
    assert "عدد مرجع" not in page.text
    # The catalog describes questions, never a stored count: no reference payload has a way in.
    catalog = _catalog(page.text)
    assert not {"reference", "reference_number", "cash_reference"} & catalog.keys()


def test_the_home_screen_links_staff_to_the_wizard_and_hides_it_from_visitors(
    staff_client: TestClient,
) -> None:
    anonymous = staff_client.get("/")
    assert 'href="/events"' not in anonymous.text

    _signed_in(staff_client)
    home = staff_client.get("/")
    assert 'href="/events"' in home.text
    assert "ثبت رویداد شیفت" in home.text


def test_the_worker_precaches_the_new_assets_and_never_the_authenticated_page(
    staff_client: TestClient,
) -> None:
    worker = staff_client.get("/sw.js")

    assert worker.status_code == 200
    match = PRECACHE_LIST.search(worker.text)
    assert match is not None
    urls = re.findall(r'"([^"]+)"', match.group(1))
    assert "/static/shift-events.css" in urls
    assert "/static/shift-events.js" in urls
    assert "/events" not in urls, "an authenticated page is cached on first visit, not precached"
