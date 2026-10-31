"""The purchase screen's API contract (SHF-4, S01 §4, R-01/R-07).

The screen owns no data: it asks `GET /api/v1/purchase/state` for its own filtered view and posts one
named action to `POST /api/v1/purchase/action`. Four things can hurt the clinic from there, and each is
gated below: a price never reaches a person without `purchase.payment.record`, a refusal is one short
Persian message and no figure, the page shell holds no order or price of its own (it is served
`no-store`), and the app shell offers the way in only to somebody who holds a purchase permission.
The rules of the chain itself are piece A's tests (`tests/integration/test_purchase_chain.py`).

The catalog is the synthetic reference data from `tests/data/synthetic_catalog.py`; every value is
invented and no production database or patient data is read (ADR-006).
"""

import re
from collections.abc import Iterator

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from synthetic_catalog import seed as seed_catalog

from shift.app import create_app
from shift.config.settings import Settings
from shift.services.purchase import UNKNOWN_ACTION

STATE_URL = "/api/v1/purchase/state"
ACTION_URL = "/api/v1/purchase/action"
ITEM = "para_500_amp"
LOCATION = "shelf_reception"
QUANTITY = 4
UNIT_PRICE = 25_000
INVOICE_REFERENCE = "فاکتور ۱۲"
PERSIAN = re.compile(r"[\u0600-\u06FF]")


@pytest.fixture()
def staff_client(settings: Settings) -> Iterator[TestClient]:
    """One app on the synthetic roster and reference catalog; every test signs in itself."""
    with TestClient(create_app(settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_catalog(client.app.state.database)
        yield client


def _sign_in(client: TestClient, role: str) -> str:
    """Sign in the real way and hand back the session's CSRF token for the unsafe calls."""
    person = ROLES[role]
    response = login(client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])


def _act(client: TestClient, csrf: str, action: str, data: dict) -> dict:
    """One named action through the screen's write door, answered as the API answered it."""
    return client.post(ACTION_URL, json={"action": action, "data": data}, headers={"X-CSRFToken": csrf}).json()


def _record_a_parcel(client: TestClient) -> str:
    """Reception takes a parcel in, the accountant types its price: the chain the screen drives."""
    csrf = _sign_in(client, "reception")
    received = _act(
        client,
        csrf,
        "delivery.receive",
        {
            "invoice_reference": INVOICE_REFERENCE,
            "lines": [{"item_id": ITEM, "location_id": LOCATION, "quantity": QUANTITY}],
        },
    )
    assert received["status"] == "recorded", received
    delivery_id = str(received["delivery_id"])
    client.cookies.clear()
    csrf = _sign_in(client, "accountant")
    typed = _act(
        client,
        csrf,
        "invoice.digitize",
        {
            "delivery_id": delivery_id,
            "lines": [{"item_id": ITEM, "quantity": QUANTITY, "unit_price_toman": UNIT_PRICE}],
        },
    )
    assert typed["status"] == "recorded", typed
    return delivery_id


def test_the_state_carries_a_price_only_for_the_accountant(staff_client: TestClient) -> None:
    """A receptionist never receives a price; the accountant receives the invoice's own figures."""
    _record_a_parcel(staff_client)

    accountant = staff_client.get(STATE_URL)
    assert accountant.status_code == 200, accountant.text
    view = accountant.json()
    assert view["invoices"][0]["invoice_total"] == QUANTITY * UNIT_PRICE
    assert view["invoices"][0]["lines"][0]["unit_price_toman"] == UNIT_PRICE
    assert view["deliveries"] is None, "the accountant reads the invoice, not the door's count"

    staff_client.cookies.clear()
    _sign_in(staff_client, "reception")
    reception = staff_client.get(STATE_URL)
    assert reception.status_code == 200, reception.text
    door = reception.json()
    assert door["invoices"] is None
    assert door["orders"] is None
    assert door["warehouse"] is None
    assert door["deliveries"][0]["status"] == "digitized"
    assert "unit_price_toman" not in reception.text
    assert "invoice_total" not in reception.text
    assert str(UNIT_PRICE) not in reception.text


def test_a_refused_action_is_one_persian_message_with_no_figure(staff_client: TestClient) -> None:
    """An action the chain does not know answers 422 with the service's own sentence."""
    csrf = _sign_in(staff_client, "storekeeper")
    refused = staff_client.post(
        ACTION_URL,
        json={"action": "unknown.action", "data": {}},
        headers={"X-CSRFToken": csrf},
    )

    assert refused.status_code == 422
    message = refused.json()["error"]["message"]
    assert message == UNKNOWN_ACTION
    assert PERSIAN.search(message), "a refusal is a Persian sentence the person can read"
    assert not re.search(r"\d", message), "no count and no price in a refusal (R-02)"


def test_the_page_shell_holds_no_purchase_data_and_is_never_stored(staff_client: TestClient) -> None:
    """The screen's data is fetched live, so the page itself is a bare, uncached shell (ADR-016)."""
    _sign_in(staff_client, "manager")
    page = staff_client.get("/purchase")

    assert page.status_code == 200, page.text
    assert page.headers["cache-control"] == "no-store", "a price may not stay in a shared cache"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert 'src="/static/purchase.js"' in page.text
    assert "پاراستامول" not in page.text, "the shell ships no catalog row"
    assert "unit_price_toman" not in page.text
    assert "invoice_total" not in page.text


def test_the_app_shell_links_purchasing_only_to_a_person_who_may_use_it(staff_client: TestClient) -> None:
    """The link follows the permissions, never a role name (R-07)."""
    anonymous = staff_client.get("/")
    assert 'href="/purchase"' not in anonymous.text

    _sign_in(staff_client, "storekeeper")
    home = staff_client.get("/")
    assert 'href="/purchase"' in home.text
    assert "خرید و انبار" in home.text
