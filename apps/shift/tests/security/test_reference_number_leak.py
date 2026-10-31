"""The owner's core promise, proved over HTTP: a reference number never leaves the server.

ADR-011 and R-02 say the shelf and cash references are compared on the server and answered as a
status. This file drives a whole reception handover and an emergency nursing one through the real
API, and after **every single request** it searches the body bytes, the headers, the cookies, the
status line and the parsed JSON for the figures the server held. It also reads the rendered shell,
every static file served through the app, the manifest, the service worker, the offline page and
`/api/v1/auth/me`, and it reads the two logs afterwards: `audit.log` is the only place a reference
may rest, and a line there must be the frozen reference map of a flow that recorded its discrepancy.

The figures are invented and distinctive, so a substring search cannot false-positive on a year, an
identifier or a file size. The test is allowed to know them (it reads them back from the projections
at the moment the flow starts); the client is not, and that is exactly what is asserted.

Everything is synthetic: accounting comes from `tools/make_synthetic_acc_db.py` (emptied of
invoices), staff and phones from `tests/data/auth_fixtures.py`.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, seed_roster
from fastapi.testclient import TestClient
from seed_reference_data import seed as seed_catalog
from synthetic_accounting import empty_accounting

import shift as shift_package
from shift.adapters.db import projections_repo
from shift.app import create_app
from shift.config.settings import Settings
from shift.domain.handover_counts import CountStatus
from shift.domain.handover_fsm import HandoverFlow
from shift.services.handover import REFERENCE_ACTION

CASH_REFERENCE = 2_468_913  # what the box should hold when the flow starts (N0 of the baseline)
CASH_FIRST = 2_469_500  # a blind cash count, off by a little
CASH_SECOND = 2_461_204  # the second blind count, different again
CASH_JOINT = 2_377_802  # what both counted together; its gap from the reference is 91 111
SHELF_REFERENCE = 137_431  # the shelf item that matches on the first count
DICLO_REFERENCE = 284_617  # the shelf item that has to walk the whole chain
DICLO_FIRST = 284_000
DICLO_SECOND = 144_200
DICLO_THIRD = 150_000
DICLO_JOINT = 122_980  # its gap from the reference is 161 637
SALINE_REFERENCE = 618_031  # the nursing flow's first item, settled alone after an early leave
SALINE_COUNT = 610_000
OXYGEN_REFERENCE = 124_906  # the second CPR item, matched on the first count
SHELF = "shelf_reception"
CPR = "cpr_room"
PARA = "para_500_amp"
DICLO = "diclofenac_75_amp"
SALINE = "saline_500_bag"
OXYGEN = "oxygen_mask"
OUTGOING = "staff-outgoing"
MANAGER = ROLES["manager"].staff_id

FIGURES: tuple[int, ...] = (
    CASH_REFERENCE,
    CASH_FIRST,
    CASH_SECOND,
    CASH_JOINT,
    abs(CASH_REFERENCE - CASH_JOINT),
    SHELF_REFERENCE,
    DICLO_REFERENCE,
    DICLO_FIRST,
    DICLO_SECOND,
    DICLO_THIRD,
    DICLO_JOINT,
    abs(DICLO_REFERENCE - DICLO_JOINT),
    SALINE_REFERENCE,
    SALINE_COUNT,
    OXYGEN_REFERENCE,
)
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_STATUSES = {status.value for status in CountStatus}


def _variants(figure: int) -> tuple[str, ...]:
    """Every spelling a leak could take: plain, grouped by comma or underscore, Persian digits."""
    plain = str(figure)
    return (
        plain,
        f"{figure:,}",
        f"{figure:_}",
        plain.translate(_PERSIAN_DIGITS),
        f"{figure:,}".replace(",", "٬").translate(_PERSIAN_DIGITS),
    )


def _appears(variant: str, text: str) -> bool:
    """A plain digit run may not be part of a longer number; a grouped spelling is a plain match."""
    if variant.isdigit():
        return re.search(rf"(?<!\d){re.escape(variant)}(?!\d)", text) is not None
    return variant in text


@dataclass
class Scanner:
    """Every response of the run, searched for every figure after each request."""

    responses: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.responses)

    def scan(self, response, label: str) -> None:
        """Search one answer: body bytes, headers, cookies, status line and the parsed JSON."""
        self.responses.append(label)
        text = response.content.decode("utf-8", "replace")
        headers = "\n".join(f"{name}: {value}" for name, value in response.headers.items())
        cookies = "\n".join(f"{name}={value}" for name, value in response.cookies.items())
        status_line = f"{response.status_code} {response.reason_phrase}"
        surfaces = {"body": text, "headers": headers, "cookies": cookies, "status line": status_line}
        for figure in FIGURES:
            for variant in _variants(figure):
                for surface, content in surfaces.items():
                    assert not _appears(variant, content), f"{label}: figure {figure} leaked into the {surface}"
        if "/api/v1/handover" in label and "json" in response.headers.get("content-type", ""):
            self._assert_closed(response.json(), label)

    @staticmethod
    def _assert_closed(body: Any, label: str) -> None:
        """A handover answer carries statuses and identifiers only, never a count or a delta."""
        for leaf in _leaves(body):
            assert isinstance(leaf, (str, bool)) or leaf is None, (
                f"{label}: a numeric field reached the client: {leaf!r}"
            )
        for chain in _status_values(body):
            assert chain in _STATUSES, f"{label}: {chain!r} is not a closed CountStatus"


def _leaves(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from _leaves(child)
    else:
        yield value


def _status_values(body: Any) -> Iterator[str]:
    """Every value of a `counts` map or a `cash_status` field, wherever it appears."""
    if isinstance(body, dict):
        for key, value in body.items():
            if key == "counts" and isinstance(value, dict):
                yield from (chain for chain in value.values() if isinstance(chain, str))
            elif key == "cash_status" and isinstance(value, str):
                yield value
            yield from _status_values(value)
    elif isinstance(body, list):
        for child in body:
            yield from _status_values(child)


def _call(client: TestClient, scanner: Scanner, method: str, url: str, **kwargs):
    """Every HTTP request of this test goes through here, so nothing is answered unscanned."""
    response = client.request(method, url, **kwargs)
    scanner.scan(response, f"{method} {url}")
    return response


def _settings(root: Path) -> Settings:
    return Settings(
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
        accounting_db_path=empty_accounting(root / "accounting.db"),
    )


@pytest.fixture()
def leak_settings(tmp_path: Path) -> Settings:
    """The app's own settings, so the test reads the log directory the app really wrote."""
    return _settings(tmp_path / "app")


@pytest.fixture()
def app_client(leak_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(leak_settings)) as client:
        seed_roster(client.app.state.database, pepper=client.app.state.secret_key)
        seed_catalog(client.app.state.database)
        yield client


def _sign_in(client: TestClient, scanner: Scanner) -> dict[str, str]:
    """The real login handshake, both requests scanned, and the CSRF header for unsafe verbs."""
    person = ROLES["reception"]
    page = _call(client, scanner, "GET", "/login")
    assert page.status_code == 200
    signed_in = _call(
        client,
        scanner,
        "POST",
        "/api/v1/auth/login",
        json={"mobile": person.mobile, "pin": PIN, "device_id": person.device_id},
        headers={"X-CSRFToken": client.cookies.get("shift_csrf", "")},
    )
    assert signed_in.status_code == 200, signed_in.text
    return {"X-CSRFToken": signed_in.json()["csrf_token"]}


def _baseline(client: TestClient, flow: HandoverFlow, *, opening_cash: int | None, items: list[dict[str, Any]]) -> None:
    """The manager's initial count: the custodian, the box's opening figure and the shelf's figures."""
    client.app.state.handover.record_baseline(
        flow=flow,
        custodian_id=OUTGOING,
        manager_id=MANAGER,
        opening_cash=opening_cash,
        items=items,
    )


def _count(
    client: TestClient,
    scanner: Scanner,
    headers: dict[str, str],
    handover_id: str,
    *,
    scope: str,
    counted: int,
    item_id: str | None = None,
    location_id: str | None = None,
) -> str:
    body: dict[str, Any] = {"scope": scope, "counted": counted}
    if item_id is not None:
        body["item_id"] = item_id
        body["location_id"] = location_id
    response = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/counts", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"status"}
    return response.json()["status"]


def _joint(
    client: TestClient,
    scanner: Scanner,
    headers: dict[str, str],
    handover_id: str,
    counted: int,
    *,
    scope: str = "cash",
    item_id: str | None = None,
    location_id: str | None = None,
) -> str:
    body: dict[str, Any] = {"scope": scope, "counted": counted}
    if item_id is not None:
        body["item_id"] = item_id
        body["location_id"] = location_id
    response = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/joint", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["status"]


def _advance(
    client: TestClient, scanner: Scanner, headers: dict[str, str], handover_id: str, *, expected: str | None
) -> dict[str, Any]:
    response = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/advance", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["step"] == expected
    return body


def _sign(
    client: TestClient, scanner: Scanner, headers: dict[str, str], handover_id: str, signer: str
) -> dict[str, Any]:
    response = _call(
        client, scanner, "POST", f"/api/v1/handover/{handover_id}/sign", json={"signer": signer}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _read_figures(client: TestClient) -> tuple[int, Mapping[tuple[str, str], int]]:
    """The figures the server holds now, read back from the projections (the client never sees them)."""
    with client.app.state.database.read() as connection:
        cash = next(row for row in projections_repo.cash_rows(connection) if row["cash_box_id"] == "reception")
        stock = {
            (str(row["item_id"]), str(row["location_id"])): int(row["qty"])
            for row in projections_repo.stock_rows(connection)
        }
    return int(cash["expected"]), stock


def _reception_flow(client: TestClient, scanner: Scanner, headers: dict[str, str]) -> str:
    """Start the reception flow and walk it to a closed state; returns its id."""
    started = _call(client, scanner, "POST", "/api/v1/handover/start", json={"flow": "reception"}, headers=headers)
    assert started.status_code == 201, started.text
    state = started.json()
    handover_id = state["handover_id"]
    assert state["steps"] == ["cash", "shelf", "checklists", "parcels", "dual_sign"]
    assert (state["step"], state["cash_status"], state["counts"]) == ("cash", "first", {})

    cash_reference, stock = _read_figures(client)
    assert cash_reference == CASH_REFERENCE, "the box's reference really was in the server's hands"
    assert stock[(PARA, SHELF)] == SHELF_REFERENCE
    assert stock[(DICLO, SHELF)] == DICLO_REFERENCE

    targets = _call(client, scanner, "GET", f"/api/v1/handover/{handover_id}/targets", headers=headers)
    assert targets.status_code == 200
    assert {item["item_id"] for item in targets.json()["items"]} == {PARA, DICLO}

    assert _count(client, scanner, headers, handover_id, scope="cash", counted=CASH_FIRST) == "incoming"
    assert _read_figures(client)[0] == CASH_REFERENCE, "a blind count never moves the reference (H-11)"
    assert _count(client, scanner, headers, handover_id, scope="cash", counted=CASH_SECOND) == "joint"
    assert _read_figures(client)[0] == CASH_REFERENCE, "two blind counts still compare against the reference"
    assert _joint(client, scanner, headers, handover_id, CASH_JOINT) == "settled"
    assert _read_figures(client)[0] == CASH_JOINT, "only the settlement moves the box (H-13)"
    shelf = _advance(client, scanner, headers, handover_id, expected="shelf")
    assert shelf["cash_status"] == "settled"
    assert shelf["counts"] == {PARA: "first", DICLO: "first"}

    assert (
        _count(
            client,
            scanner,
            headers,
            handover_id,
            scope="item",
            counted=SHELF_REFERENCE,
            item_id=PARA,
            location_id=SHELF,
        )
        == "matched"
    )
    assert (
        _count(
            client, scanner, headers, handover_id, scope="item", counted=DICLO_FIRST, item_id=DICLO, location_id=SHELF
        )
        == "recount"
    )
    assert (
        _count(
            client, scanner, headers, handover_id, scope="item", counted=DICLO_SECOND, item_id=DICLO, location_id=SHELF
        )
        == "outgoing"
    )
    assert (
        _count(
            client, scanner, headers, handover_id, scope="item", counted=DICLO_THIRD, item_id=DICLO, location_id=SHELF
        )
        == "joint"
    )
    assert (
        _joint(client, scanner, headers, handover_id, DICLO_JOINT, scope="item", item_id=DICLO, location_id=SHELF)
        == "settled"
    )

    _advance(client, scanner, headers, handover_id, expected="checklists")
    _advance(client, scanner, headers, handover_id, expected="parcels")
    _advance(client, scanner, headers, handover_id, expected="dual_sign")
    assert _sign(client, scanner, headers, handover_id, "incoming")["closed"] is False
    assert _sign(client, scanner, headers, handover_id, "outgoing")["closed"] is True

    final = _call(client, scanner, "GET", f"/api/v1/handover/{handover_id}", headers=headers)
    assert (final.json()["closed"], final.json()["step"]) == (True, None)
    assert final.json()["signed"] == ["incoming", "outgoing"]
    return handover_id


def _emergency_flow(client: TestClient, scanner: Scanner, headers: dict[str, str]) -> str:
    """A nursing flow where the outgoing person left: one person finishes, and no culprit is named."""
    started = _call(client, scanner, "POST", "/api/v1/handover/start", json={"flow": "nursing"}, headers=headers)
    assert started.status_code == 201, started.text
    handover_id = started.json()["handover_id"]
    assert started.json()["cash_status"] is None, "the nursing flow hands over no cash (H-12)"

    _, stock = _read_figures(client)
    assert stock[(SALINE, CPR)] == SALINE_REFERENCE
    assert stock[(OXYGEN, CPR)] == OXYGEN_REFERENCE

    left = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/early-leave", headers=headers)
    assert left.json()["early_left"] is True
    blocked = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/advance", headers=headers)
    assert blocked.status_code == 409, "an early leave blocks the flow until the manager confirms (H-08)"

    confirmed = _call(
        client,
        scanner,
        "POST",
        f"/api/v1/handover/{handover_id}/emergency-start",
        json={"manager_id": MANAGER},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["emergency"]["manager_id"] == MANAGER
    assert confirmed.json()["emergency"]["at"]

    assert (
        _count(
            client, scanner, headers, handover_id, scope="item", counted=SALINE_COUNT, item_id=SALINE, location_id=CPR
        )
        == "recount"
    )
    assert (
        _count(
            client, scanner, headers, handover_id, scope="item", counted=SALINE_COUNT, item_id=SALINE, location_id=CPR
        )
        == "settled"
    )
    return handover_id


def _scan_the_shell(client: TestClient, scanner: Scanner, headers: dict[str, str]) -> None:
    """The rendered page, every static file, the PWA shell and `/me` — none of them may carry a figure."""
    for url in ("/", "/manifest.webmanifest", "/sw.js", "/offline.html", "/api/v1/auth/me"):
        response = _call(client, scanner, "GET", url, headers=headers)
        assert response.status_code == 200, url
    static = Path(shift_package.__file__).resolve().parent / "static"
    files = sorted(path for path in static.rglob("*") if path.is_file())
    assert files, "the static shell must exist for this scan to mean anything"
    for path in files:
        url = "/static/" + path.relative_to(static).as_posix()
        response = _call(client, scanner, "GET", url)
        assert response.status_code == 200, url


def test_the_reference_never_leaves_the_server_in_a_whole_handover(
    app_client: TestClient, leak_settings: Settings
) -> None:
    scanner = Scanner()
    headers = _sign_in(app_client, scanner)
    _baseline(
        app_client,
        HandoverFlow.RECEPTION,
        opening_cash=CASH_REFERENCE,
        items=[
            {"item_id": PARA, "location_id": SHELF, "quantity": SHELF_REFERENCE},
            {"item_id": DICLO, "location_id": SHELF, "quantity": DICLO_REFERENCE},
        ],
    )
    _baseline(
        app_client,
        HandoverFlow.NURSING,
        opening_cash=None,
        items=[
            {"item_id": SALINE, "location_id": CPR, "quantity": SALINE_REFERENCE},
            {"item_id": OXYGEN, "location_id": CPR, "quantity": OXYGEN_REFERENCE},
        ],
    )

    closed = _reception_flow(app_client, scanner, headers)
    emergency = _emergency_flow(app_client, scanner, headers)

    other_event = _call(
        app_client,
        scanner,
        "POST",
        "/api/v1/events",
        json={
            "client_event_id": "01J00000000000000000000000",
            "type": "stock.waste_reported",
            "payload": {"item_id": "gauze_roll", "location_id": "doctor_room", "quantity": 2},
            "client_at": "2026-10-28T10:00:00Z",
        },
        headers=headers,
    )
    assert other_event.status_code == 200, other_event.text
    _scan_the_shell(app_client, scanner, headers)

    assert scanner.count >= 30, f"the scan covered only {scanner.count} responses"
    _assert_only_the_audit_trail_holds_a_figure(app_client, leak_settings, closed, emergency)


def _assert_only_the_audit_trail_holds_a_figure(
    client: TestClient, settings: Settings, closed: str, emergency: str
) -> None:
    """A figure may rest in `audit_log` only, tied to the flow that recorded its discrepancy."""
    public_log = (settings.log_dir / "shift.log").read_text(encoding="utf-8")
    for figure in FIGURES:
        for variant in _variants(figure):
            assert not _appears(variant, public_log), f"figure {figure} reached the public log"

    audit = (settings.log_dir / "audit.log").read_text(encoding="utf-8").splitlines()
    assert any(f"{REFERENCE_ACTION}.{closed}" in line for line in audit), "the references are audited at start"
    for line in audit:
        if any(_appears(variant, line) for figure in FIGURES for variant in _variants(figure)):
            assert REFERENCE_ACTION in line, "the only place a figure may rest is the frozen reference row"

    with client.app.state.database.read() as connection:
        rows = connection.exec_driver_sql(
            "SELECT subject_id, detail FROM audit_log WHERE action LIKE ? ORDER BY id",
            (f"{REFERENCE_ACTION}.%",),
        ).mappings()
        audited = {str(row["subject_id"]): str(row["detail"]) for row in rows}
        discrepancies = [
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT handover_id, payload FROM events WHERE type = 'discrepancy.recorded' ORDER BY seq"
            ).mappings()
        ]
        cash_reference = int(
            next(row for row in projections_repo.cash_rows(connection) if row["cash_box_id"] == "reception")["expected"]
        )

    assert set(audited) == {closed, emergency}
    for figure in (CASH_REFERENCE, SHELF_REFERENCE, DICLO_REFERENCE, SALINE_REFERENCE, OXYGEN_REFERENCE):
        assert str(figure) in audited[closed] or str(figure) in audited[emergency], (
            f"the server never held {figure}, so scanning for it proves nothing"
        )
    assert cash_reference == CASH_JOINT, "the settled figure became the box's next reference (H-13)"

    by_flow = [(row["handover_id"], row["payload"]) for row in discrepancies]
    assert {flow for flow, _ in by_flow} == {closed, emergency}
    assert any(f'"difference":{CASH_REFERENCE - CASH_JOINT}' in payload for _, payload in by_flow)
    unnamed = [payload for flow, payload in by_flow if flow == emergency]
    assert unnamed and all('"culprit_id":null' in payload for payload in unnamed), (
        "an emergency start never names the absent person (H-08)"
    )
