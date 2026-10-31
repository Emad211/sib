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

The manager's side of the same promise (SHF-6.1) runs after the flows: the scheduler's own builder
stores the day's card, the card's four pages are read as a manager and scanned for every reference
the audit trail froze, a receptionist and a nurse are refused with a `403` whose raw bytes carry no
figure or name of the card at all, and the document the engine stored is scanned as well — because
`audit_log` is the only sink allowed to rest a reference in (R-02, ADR-011).

Everything is synthetic: accounting comes from `tools/make_synthetic_acc_db.py` (emptied of
invoices), staff and phones from `tests/data/auth_fixtures.py`.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, seed_roster
from fastapi.testclient import TestClient
from synthetic_accounting import empty_accounting
from synthetic_catalog import seed as seed_catalog

import shift as shift_package
from shift.adapters.db import projections_repo
from shift.app import create_app
from shift.common.clock import TEHRAN, to_tehran
from shift.config.settings import Settings
from shift.domain.daily_card import FRIDAY, window_for
from shift.domain.handover_counts import CountStatus
from shift.domain.handover_fsm import HandoverFlow
from shift.services.audit import AuditEntry
from shift.services.auth import SESSION_COOKIE
from shift.services.daily_card import GENERATED_ACTION, Card
from shift.services.handover import EXCEPTION_ACTION, REFERENCE_ACTION

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
OUTGOING = ROLES["multi_role"].staff_id  # the custodian signs on their own phone, the second one
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
# The two gaps a card exists to report (S01 §6): a manager's own page may show these and the receipt
# below, and nothing else the server holds. The saline gap is not in `FIGURES` because no API answer
# ever carried it; it is still a card figure, so it is asserted by value where the card holds it.
CARD_GAPS: tuple[int, ...] = (abs(CASH_REFERENCE - CASH_JOINT), abs(DICLO_REFERENCE - DICLO_JOINT))
# What the card and its stored document may never carry: every figure the server keeps to itself —
# the five frozen references (`CASH_REFERENCE`, `SHELF_REFERENCE`, `DICLO_REFERENCE`,
# `SALINE_REFERENCE`, `OXYGEN_REFERENCE`) and the blind and joint counts behind them — except the
# gaps the card is written to report.
FORBIDDEN_FIGURES: tuple[int, ...] = tuple(figure for figure in FIGURES if figure not in CARD_GAPS)
# A receipt the audit trail reports as landed inside the flow: the manager's card shows this total.
CASH_EXCEPTION = 4_512_309
INCOMING = ROLES["reception"].staff_id
CARD_PATH = "/manager/card"
HISTORY_PATH = "/manager/cards"
SETTINGS_PATH = "/manager/settings"
REFUSAL = "نقش شما اجازهٔ این کار را ندارد."
CARD_HEADINGS = (
    "شروع اضطراری تحویل",
    "نقد ثبت‌شده در تحویل",
    "مغایرت‌ها",
    "شیفت‌ها و تحویل‌های روز",
    "تحویل‌های باز",
)
# Anything of the card's own page: a 403 may not be that page with its values stripped in the template.
CARD_MARKUP = ("mg-page", "mg-section", "mg-amount", "mg-alert-row", "کارت روزانهٔ مدیر")
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
    # The manager's own card is allowed to show the gaps it reports, so its pages scan the rest of
    # the server's figures; every response outside the card keeps the full `FIGURES`.
    figures: tuple[int, ...] = FIGURES

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
        for figure in self.figures:
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
        environment="development",  # plain-HTTP test client: no Secure cookies
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


@dataclass(frozen=True, slots=True)
class Session:
    """One person signed in on their own phone; both sides of a handover hold one (S03 §3)."""

    headers: dict[str, str]
    token: str


def _sign_in(client: TestClient, scanner: Scanner, role: str = "reception") -> Session:
    """The real login handshake, both requests scanned, and the CSRF header for unsafe verbs."""
    person = ROLES[role]
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
    token = signed_in.cookies.get(SESSION_COOKIE)
    assert token, "the login handshake issued no session"
    return Session({"X-CSRFToken": signed_in.json()["csrf_token"]}, token)


def _act(client: TestClient, session: Session) -> dict[str, str]:
    """Act as one signed-in person; the other phone's session stays valid beside it."""
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, session.token)
    return session.headers


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
    session: Session,
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
    response = _call(
        client, scanner, "POST", f"/api/v1/handover/{handover_id}/counts", json=body, headers=_act(client, session)
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"status"}
    return response.json()["status"]


def _joint(
    client: TestClient,
    scanner: Scanner,
    session: Session,
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
    response = _call(
        client, scanner, "POST", f"/api/v1/handover/{handover_id}/joint", json=body, headers=_act(client, session)
    )
    assert response.status_code == 200, response.text
    return response.json()["status"]


def _advance(
    client: TestClient, scanner: Scanner, session: Session, handover_id: str, *, expected: str | None
) -> dict[str, Any]:
    response = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/advance", headers=_act(client, session))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["step"] == expected
    return body


def _checklist(
    client: TestClient,
    scanner: Scanner,
    session: Session,
    handover_id: str,
    item_id: str,
    *,
    ok: bool,
    reading: float | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"item_id": item_id, "ok": ok}
    if reading is not None:
        body["reading"] = reading
    response = _call(
        client,
        scanner,
        "POST",
        f"/api/v1/handover/{handover_id}/checklists",
        json=body,
        headers=_act(client, session),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _parcel(
    client: TestClient, scanner: Scanner, session: Session, handover_id: str, parcel_id: str, packages: int
) -> dict[str, Any]:
    response = _call(
        client,
        scanner,
        "POST",
        f"/api/v1/handover/{handover_id}/parcels",
        json={"parcel_id": parcel_id, "packages": packages},
        headers=_act(client, session),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _sign(client: TestClient, scanner: Scanner, session: Session, handover_id: str) -> dict[str, Any]:
    """Sign with the session's own side; the request carries no signer at all (H-04, S03 §3)."""
    response = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/sign", headers=_act(client, session))
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


def _reception_flow(client: TestClient, scanner: Scanner, incoming: Session, outgoing: Session) -> str:
    """Start the reception flow and walk it to a closed state; returns its id.

    The incoming person opens it on their phone; the outgoing person joins it through the current
    list on their own phone, and each side signs from the phone it holds (S03 §3).
    """
    started = _call(
        client, scanner, "POST", "/api/v1/handover/start", json={"flow": "reception"}, headers=_act(client, incoming)
    )
    assert started.status_code == 201, started.text
    state = started.json()
    handover_id = state["handover_id"]
    assert state["steps"] == ["cash", "shelf", "checklists", "parcels", "dual_sign"]
    assert (state["step"], state["cash_status"], state["counts"]) == ("cash", "first", {})

    cash_reference, stock = _read_figures(client)
    assert cash_reference == CASH_REFERENCE, "the box's reference really was in the server's hands"
    assert stock[(PARA, SHELF)] == SHELF_REFERENCE
    assert stock[(DICLO, SHELF)] == DICLO_REFERENCE

    targets = _call(client, scanner, "GET", f"/api/v1/handover/{handover_id}/targets", headers=_act(client, incoming))
    assert targets.status_code == 200
    assert {item["item_id"] for item in targets.json()["items"]} == {PARA, DICLO}

    assert _count(client, scanner, incoming, handover_id, scope="cash", counted=CASH_FIRST) == "incoming"
    assert _read_figures(client)[0] == CASH_REFERENCE, "a blind count never moves the reference (H-11)"
    assert _count(client, scanner, incoming, handover_id, scope="cash", counted=CASH_SECOND) == "joint"
    assert _read_figures(client)[0] == CASH_REFERENCE, "two blind counts still compare against the reference"
    assert _joint(client, scanner, incoming, handover_id, CASH_JOINT) == "settled"
    assert _read_figures(client)[0] == CASH_JOINT, "only the settlement moves the box (H-13)"
    shelf = _advance(client, scanner, incoming, handover_id, expected="shelf")
    assert shelf["cash_status"] == "settled"
    assert shelf["counts"] == {PARA: "first", DICLO: "first"}

    assert (
        _count(
            client,
            scanner,
            incoming,
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
            client, scanner, incoming, handover_id, scope="item", counted=DICLO_FIRST, item_id=DICLO, location_id=SHELF
        )
        == "recount"
    )
    assert (
        _count(
            client, scanner, incoming, handover_id, scope="item", counted=DICLO_SECOND, item_id=DICLO, location_id=SHELF
        )
        == "outgoing"
    )
    assert (
        _count(
            client, scanner, incoming, handover_id, scope="item", counted=DICLO_THIRD, item_id=DICLO, location_id=SHELF
        )
        == "joint"
    )
    assert (
        _joint(client, scanner, incoming, handover_id, DICLO_JOINT, scope="item", item_id=DICLO, location_id=SHELF)
        == "settled"
    )

    _advance(client, scanner, incoming, handover_id, expected="checklists")
    answered = _checklist(client, scanner, incoming, handover_id, "store_lock_ok", ok=True)
    reading = _checklist(client, scanner, incoming, handover_id, "fridge_temperature", ok=True, reading=6.5)
    assert (answered["step"], reading["step"]) == ("checklists", "checklists")
    _advance(client, scanner, incoming, handover_id, expected="parcels")
    parcel = _parcel(client, scanner, incoming, handover_id, "parcel-courier-01", 3)
    assert parcel["step"] == "parcels"
    _advance(client, scanner, incoming, handover_id, expected="dual_sign")

    # The second phone finds the flow through the join list and signs its own side there.
    joined = _call(client, scanner, "GET", "/api/v1/handover/current", headers=_act(client, outgoing))
    assert joined.status_code == 200
    assert [(row["handover_id"], row["flow"], row["step"]) for row in joined.json()] == [
        (handover_id, "reception", "dual_sign")
    ]
    assert (
        _call(client, scanner, "GET", f"/api/v1/handover/{handover_id}", headers=_act(client, outgoing)).status_code
        == 200
    )

    assert _sign(client, scanner, incoming, handover_id)["closed"] is False
    assert _sign(client, scanner, outgoing, handover_id)["closed"] is True

    final = _call(client, scanner, "GET", f"/api/v1/handover/{handover_id}", headers=_act(client, outgoing))
    assert (final.json()["closed"], final.json()["step"]) == (True, None)
    assert final.json()["signed"] == ["incoming", "outgoing"]
    return handover_id


def _emergency_flow(client: TestClient, scanner: Scanner, incoming: Session) -> str:
    """A nursing flow where the outgoing person left: one person finishes, and no culprit is named."""
    started = _call(
        client, scanner, "POST", "/api/v1/handover/start", json={"flow": "nursing"}, headers=_act(client, incoming)
    )
    assert started.status_code == 201, started.text
    handover_id = started.json()["handover_id"]
    assert started.json()["cash_status"] is None, "the nursing flow hands over no cash (H-12)"

    _, stock = _read_figures(client)
    assert stock[(SALINE, CPR)] == SALINE_REFERENCE
    assert stock[(OXYGEN, CPR)] == OXYGEN_REFERENCE

    left = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/early-leave", headers=_act(client, incoming))
    assert left.json()["early_left"] is True
    blocked = _call(client, scanner, "POST", f"/api/v1/handover/{handover_id}/advance", headers=_act(client, incoming))
    assert blocked.status_code == 409, "an early leave blocks the flow until the manager confirms (H-08)"

    confirmed = _call(
        client,
        scanner,
        "POST",
        f"/api/v1/handover/{handover_id}/emergency-start",
        json={"manager_id": MANAGER},
        headers=_act(client, incoming),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["emergency"]["manager_id"] == MANAGER
    assert confirmed.json()["emergency"]["at"]

    assert (
        _count(
            client,
            scanner,
            incoming,
            handover_id,
            scope="item",
            counted=SALINE_COUNT,
            item_id=SALINE,
            location_id=CPR,
        )
        == "recount"
    )
    assert (
        _count(
            client,
            scanner,
            incoming,
            handover_id,
            scope="item",
            counted=SALINE_COUNT,
            item_id=SALINE,
            location_id=CPR,
        )
        == "settled"
    )
    return handover_id


def _scan_the_shell(client: TestClient, scanner: Scanner, headers: dict[str, str]) -> None:
    """The rendered page, every static file, the PWA shell and `/me` — none of them may carry a figure."""
    for url in ("/", "/handover", "/manifest.webmanifest", "/sw.js", "/offline.html", "/api/v1/auth/me"):
        response = _call(client, scanner, "GET", url, headers=headers)
        assert response.status_code == 200, url
    static = Path(shift_package.__file__).resolve().parent / "static"
    files = sorted(path for path in static.rglob("*") if path.is_file())
    assert files, "the static shell must exist for this scan to mean anything"
    for path in files:
        url = "/static/" + path.relative_to(static).as_posix()
        response = _call(client, scanner, "GET", url)
        assert response.status_code == 200, url


# --- the manager's side of the promise (SHF-6.1) ---------------------------------------------------


def _record_a_receipt_inside_the_flow(client: TestClient, handover_id: str, amount: int) -> None:
    """The audit row `HandoverService._report_carry` writes when a receipt lands inside a flow.

    A real receipt is an accounting close stamped inside the flow's own window (H-11); that scenario
    belongs to the accounting and handover tests. This gate records the row through the same audit
    service call the production path makes, with the same action and detail, so the card's cash block
    really holds a figure that a non-manager page must never show.
    """
    client.app.state.audit.record(
        AuditEntry(
            actor_id=INCOMING,
            action=EXCEPTION_ACTION,
            subject_id=handover_id,
            detail={"flow": "reception", "amount": amount},
        )
    )


def _card_day_for(moment: datetime) -> date:
    """The card date whose window covers `moment`'s Tehran day: the next run the scheduler makes.

    A card reports the day before it (S01 §6), so today's work is on tomorrow's card. When tomorrow
    is Friday there is no card at all and Saturday's card covers Thursday and Friday (ADR-018),
    which still contains today.
    """
    today = to_tehran(moment).date()
    run_day = today + timedelta(days=1)
    return run_day + timedelta(days=1) if run_day.weekday() == FRIDAY else run_day


def _generate_the_card(client: TestClient) -> Card:
    """Store the card through the scheduler's own entry point, then prove it covers the work above."""
    now = client.app.state.clock.now_utc()
    run_day = _card_day_for(now)
    stored = client.app.state.daily_card.generate_due(datetime.combine(run_day, time(7, 30), tzinfo=TEHRAN))
    assert [card.card_date for card in stored] == [run_day], "the scheduler path stored no card for the day"
    window = window_for(run_day)
    assert window is not None and window.covered_from <= to_tehran(now).date() <= window.covered_to, (
        "the generated card does not cover the day the flows were recorded on"
    )
    return stored[0]


def _manager_urls(card: Card) -> tuple[str, ...]:
    """The four manager pages, the stored card's own day among them."""
    return (CARD_PATH, HISTORY_PATH, f"{CARD_PATH}/{card.card_date.isoformat()}", SETTINGS_PATH)


def _card_amounts(card: Card) -> tuple[int, ...]:
    """The money the card carries: every settled gap and every receipt total, no clock reading."""
    sections = card.to_payload()["sections"]
    amounts = {
        int(entry[key])
        for section in sections.values()
        for entry in section
        for key in ("difference", "amount")
        if key in entry
    }
    return tuple(sorted(amounts))


def _card_identities(card: Card) -> tuple[str, ...]:
    """Every name and handover id the card carries — the emergency actor's name among them."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in {"name", "handover_id"} and isinstance(child, str):
                    found.add(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(card.to_payload()["sections"])
    return tuple(sorted(found))


def _assert_the_card_really_carries(card: Card) -> None:
    """Fail closed: the card really holds the receipt, the three gaps and the emergency start."""
    assert [entry.amount for entry in card.cash_exceptions] == [CASH_EXCEPTION], "the card held no receipt"
    assert {entry.difference for entry in card.discrepancies} == {
        abs(CASH_REFERENCE - CASH_JOINT),
        abs(DICLO_REFERENCE - DICLO_JOINT),
        abs(SALINE_REFERENCE - SALINE_COUNT),
    }, "the card held no gap"
    assert [entry.recorded_by.name for entry in card.emergency_starts] == [ROLES["reception"].full_name]
    assert [entry.manager.name for entry in card.emergency_starts] == [ROLES["manager"].full_name]
    unnamed = [entry for entry in card.discrepancies if entry.difference == abs(SALINE_REFERENCE - SALINE_COUNT)]
    assert [entry.culprit for entry in unnamed] == [None], "the emergency path's gap stays unnamed (H-08)"


def _scan_the_manager_pages(client: TestClient, session: Session, card: Card) -> int:
    """The manager's four pages: 200, the card's own figures rendered, and no server figure among them.

    Scanned with `FORBIDDEN_FIGURES`, not `FIGURES`: a card's job is to show the gap it settled and
    the receipt it was told about, so those two gaps are the only figures allowed on its pages. The
    rendered amounts are asserted first, so a page that quietly stopped showing the card cannot make
    this scan pass by rendering nothing.
    """
    references = Scanner(figures=FORBIDDEN_FIGURES)
    headers = _act(client, session)
    urls = _manager_urls(card)
    card_page = _call(client, references, "GET", urls[0], headers=headers)
    assert card_page.status_code == 200, card_page.text
    for amount in _card_amounts(card):
        assert f"{amount:,}" in card_page.text, f"the card page rendered no {amount}, so scanning for it proves nothing"
    for url in urls[1:]:
        response = _call(client, references, "GET", url, headers=headers)
        assert response.status_code == 200, url
    return references.count


def _assert_no_card_data(response, card: Card, label: str) -> None:
    """The raw body, every header and every cookie: no reference, no count, no gap, no name, no id."""
    surfaces = {
        "body": response.content.decode("utf-8", "replace"),
        "headers": "\n".join(f"{name}: {value}" for name, value in response.headers.items()),
        "cookies": "\n".join(f"{name}={value}" for name, value in response.cookies.items()),
    }
    for figure in (*FIGURES, *_card_amounts(card)):
        for variant in _variants(figure):
            for surface, content in surfaces.items():
                assert not _appears(variant, content), f"{label}: figure {figure} reached the 403 {surface}"
    for identity in _card_identities(card):
        for surface, content in surfaces.items():
            assert identity not in content, f"{label}: {identity!r} reached the 403 {surface}"


def _scan_as_non_managers(client: TestClient, scanner: Scanner, card: Card, sessions: tuple[Session, ...]) -> int:
    """The same four pages as people who may not open them: a 403, and not one figure of the card."""
    scanned = 0
    for session in sessions:
        headers = {**_act(client, session), "Accept": "text/html"}
        for url in _manager_urls(card):
            response = _call(client, scanner, "GET", url, headers=headers)
            scanned += 1
            assert response.status_code == 403, url
            body = response.content.decode("utf-8", "replace")
            assert REFUSAL in body, url
            # The raw bytes are the shared refusal page, never the card with its values stripped in
            # the template: neither its markup nor one of its headings may survive at all.
            for marker in (*CARD_MARKUP, *CARD_HEADINGS):
                assert marker not in body, f"{url}: the 403 page still carries {marker!r}"
            _assert_no_card_data(response, card, url)
    return scanned


def _assert_the_stored_card_holds_no_leaked_figure(client: TestClient, card: Card) -> None:
    """The document the engine wrote: the gaps and the receipt it means to hold, never a server figure."""
    with client.app.state.database.read() as connection:
        payload = str(
            connection.exec_driver_sql(
                "SELECT payload FROM daily_cards WHERE card_date = ?", (card.card_date.isoformat(),)
            ).scalar_one()
        )
    for figure in FORBIDDEN_FIGURES:
        for variant in _variants(figure):
            assert not _appears(variant, payload), f"the stored card carries the figure {figure}"
    for amount in _card_amounts(card):
        assert _appears(str(amount), payload), f"the stored card does not hold {amount}, so its absence proves nothing"


def _assert_the_generated_row_holds_no_figure(client: TestClient, settings: Settings, card: Card) -> None:
    """The card's own audit row names the window it built and restates no figure of the card.

    `audit.log` is the only sink allowed to carry a number (R-02, ADR-011) and the frozen reference
    map is the only row that may carry one: a generation row says which day it built, nothing about
    what it found.
    """
    figures = (*FIGURES, *_card_amounts(card))
    with client.app.state.database.read() as connection:
        rows = [
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT subject_id, detail FROM audit_log WHERE action = ? ORDER BY id", (GENERATED_ACTION,)
            ).mappings()
        ]
    assert rows, "generating a card left no audit row"
    assert str(rows[-1]["subject_id"]) == card.card_date.isoformat()
    lines = [
        line
        for line in (settings.log_dir / "audit.log").read_text(encoding="utf-8").splitlines()
        if GENERATED_ACTION in line
    ]
    assert lines, "the card generation reached no audit log line"
    for text in (*[f"{row['subject_id']} {row['detail']}" for row in rows], *lines):
        for figure in figures:
            for variant in _variants(figure):
                assert not _appears(variant, text), f"the card's audit row carries the figure {figure}"


def test_the_reference_never_leaves_the_server_in_a_whole_handover(
    app_client: TestClient, leak_settings: Settings
) -> None:
    scanner = Scanner()
    incoming = _sign_in(app_client, scanner)
    outgoing = _sign_in(app_client, scanner, "multi_role")
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

    closed = _reception_flow(app_client, scanner, incoming, outgoing)
    emergency = _emergency_flow(app_client, scanner, incoming)

    other_event = _call(
        app_client,
        scanner,
        "POST",
        "/api/v1/events",
        json={
            "client_event_id": "01J00000000000000000000000",
            "type": "stock.waste_reported",
            "payload": {
                "item_id": "para_500_amp",
                "location_id": "shelf_reception",
                "quantity": 2,
                "waste_kind": "broken",
                "culprit_id": "staff-doctor",
                "confirmer_id": "staff-spare",
            },
            "client_at": "2026-10-28T10:00:00Z",
        },
        headers=_act(app_client, incoming),
    )
    assert other_event.status_code == 200, other_event.text
    _scan_the_shell(app_client, scanner, _act(app_client, incoming))

    # The manager's side (SHF-6.1): the day's settled gaps and a receipt are in the audit trail, the
    # scheduler's builder stores the card, and from here the manager's pages, the two refusals and
    # the stored document are scanned for the figures the server really held.
    _record_a_receipt_inside_the_flow(app_client, closed, CASH_EXCEPTION)
    manager = _sign_in(app_client, scanner, "manager")
    card = _generate_the_card(app_client)
    _assert_the_card_really_carries(card)
    assert _scan_the_manager_pages(app_client, manager, card) == len(_manager_urls(card))
    nurse = _sign_in(app_client, scanner, "nurse")
    assert _scan_as_non_managers(app_client, scanner, card, (incoming, nurse)) == 2 * len(_manager_urls(card))
    _assert_the_stored_card_holds_no_leaked_figure(app_client, card)
    _assert_the_generated_row_holds_no_figure(app_client, leak_settings, card)

    # 75 answers through the full `FIGURES` scan, plus the four manager pages the card's own scanner
    # reads: nothing may silently stop being scanned without failing here.
    assert scanner.count >= 75, f"the scan covered only {scanner.count} responses"
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
