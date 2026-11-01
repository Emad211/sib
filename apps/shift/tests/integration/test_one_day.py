"""A7: one working day of the reception flow, end to end through the real routes.

The money and stock rules are pinned piece by piece elsewhere; this walks them together the way a day
goes, so a change that breaks how they fit is caught here:

1. the manager's opening count of the shelf and the box, and the storekeeper's opening count of the store;
2. a shelf charge (store → shelf, both sides), a return (shelf → store, both sides), confirmed waste with
   its photo, and a card transfer with its receipt — each on the person's own session;
3. the handover starts and reads accounting: a closed invoice's consumption (D) comes off its shelf;
4. both people count on their own phones — the outgoing person the box first, the incoming person the
   shelf — and every count matches the reference `E = C0 + R - D - U - W` without anybody seeing it;
5. the checklists, the signatures, the close; the outgoing person's session winds down;
6. no discrepancy was recorded, a rebuild equals the live projections, and no answer carried a figure.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from auth_fixtures import PIN, ROLES, login, seed_roster
from fastapi.testclient import TestClient
from make_synthetic_acc_db import build
from photo_fixtures import RECEIPT_JPEG, RECEIPT_SHA
from synthetic_accounting import close_open_invoices
from synthetic_catalog import seed as seed_catalog

from shift.adapters.db import catalog_repo, projections_repo
from shift.app import create_app
from shift.config.settings import Settings
from shift.domain.handover_fsm import HandoverFlow
from shift.domain.policy import ShiftKey
from shift.services.auth import SESSION_COOKIE

SHELF = "shelf_reception"
STORE = "main_store"
PARA, DICLO, SYRINGE = "para_500_amp", "diclofenac_75_amp", "syringe_5"
OPENING = {PARA: 10, DICLO: 5, SYRINGE: 20}
CASH_OPENING = 1_000_000
OUTGOING = ROLES["multi_role"]
INCOMING = ROLES["reception"]


@pytest.fixture()
def day(tmp_path: Path) -> Iterator[TestClient]:
    accounting = build(tmp_path / "accounting.db")
    close_open_invoices(
        accounting.path, "2026-09-24 13:40:00"
    )  # H-03: the outgoing person closed every invoice before handing over
    root = tmp_path / "app"
    settings = Settings(
        environment="development",
        database_path=root / "shift.db",
        log_dir=root / "logs",
        audit_log_path=root / "logs" / "audit.log",
        secret_file=root / ".secret",
        accounting_db_path=accounting.path,
    )
    with TestClient(create_app(settings)) as client:
        database = client.app.state.database
        seed_roster(database, pepper=client.app.state.secret_key, on_shift=False)
        seed_catalog(database)
        with database.write() as connection:
            # The syringe the synthetic accounting bills under «SYR-5»: one code, one counted shelf item.
            catalog_repo.insert_item_version(
                connection,
                item_id=SYRINGE,
                name="سرنگ ۵ سی‌سی",
                kind="counted",
                location_id=SHELF,
                unit="عدد",
                minimum=5,
                acc_code="SYR-5",
                stock_factor=1,
                retired=False,
                version=1,
                effective_from_shift=ShiftKey.baseline().text,
                changed_at="2026-09-01T00:00:00+00:00",
                changed_by="tests",
            )
        client.app.state.handover.record_baseline(
            flow=HandoverFlow.RECEPTION,
            custodian_id=OUTGOING.staff_id,
            manager_id=ROLES["manager"].staff_id,
            opening_cash=CASH_OPENING,
            items=[{"item_id": item, "location_id": SHELF, "quantity": qty} for item, qty in OPENING.items()],
        )
        client.app.state.photos.save(RECEIPT_JPEG)
        yield client


class Phones:
    """Each person on their own session; the responses are kept for the figure scan at the end."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.tokens: dict[str, tuple[str, str]] = {}
        self.bodies: list[str] = []

    def act(self, role: str, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
        person = ROLES[role]
        if role not in self.tokens:
            self.client.cookies.clear()
            response = login(self.client, mobile=person.mobile, device_id=person.device_id, pin=PIN)
            assert response.status_code == 200, response.text
            self.tokens[role] = (response.cookies.get(SESSION_COOKIE), response.json()["csrf_token"])
        token, csrf = self.tokens[role]
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, token)
        response = self.client.request(method, url, json=body, headers={"X-CSRFToken": csrf})
        self.bodies.append(response.text)
        assert response.status_code < 400, f"{role} {method} {url}: {response.text}"
        return response.json() if response.content else None

    def purchase(self, role: str, action: str, **data: Any) -> Any:
        return self.act(role, "POST", "/api/v1/purchase/action", {"action": action, "data": data})

    def event(self, role: str, event_type: str, payload: dict[str, Any]) -> Any:
        from shift.common.ids import new_ulid

        key = new_ulid()
        return self.act(
            role,
            "POST",
            "/api/v1/events",
            {
                "type": event_type,
                "payload": payload,
                "client_event_id": key,
                "idempotency_key": key,
                "client_at": datetime.now(UTC).isoformat(),
            },
        )


def _stock(client: TestClient) -> dict[tuple[str, str], int]:
    with client.app.state.database.read() as connection:
        return {(r["item_id"], r["location_id"]): int(r["qty"]) for r in projections_repo.stock_rows(connection)}


def _box(client: TestClient) -> int:
    with client.app.state.database.read() as connection:
        return next(
            int(r["expected"]) for r in projections_repo.cash_rows(connection) if r["cash_box_id"] == "reception"
        )


def test_one_working_day_of_the_reception_flow(day: TestClient) -> None:
    phones = Phones(day)

    # 1. The storekeeper's opening count of the main store, every item once.
    store_items = [
        i["item_id"] for i in phones.act("storekeeper", "GET", "/api/v1/purchase/state")["warehouse"]["items"]
    ]
    phones.purchase("storekeeper", "store.baseline", lines=[{"item_id": item, "quantity": 50} for item in store_items])

    # 2. The day's movements, each confirmed by the other side or carrying its photo.
    offer = phones.purchase(
        "storekeeper",
        "restock.record",
        item_id=PARA,
        from_location_id=STORE,
        to_location_id=SHELF,
        quantity=5,
        confirmer_id=OUTGOING.staff_id,
    )
    phones.purchase("multi_role", "restock.confirm", offer_id=offer["event_id"], quantity=5)
    back = phones.purchase(
        "multi_role",
        "shelf_return.record",
        item_id=PARA,
        from_location_id=SHELF,
        to_location_id=STORE,
        quantity=2,
        confirmer_id=ROLES["storekeeper"].staff_id,
    )
    phones.purchase("storekeeper", "shelf_return.confirm", offer_id=back["event_id"], quantity=2)
    phones.event(
        "multi_role",
        "stock.waste_reported",
        {
            "item_id": DICLO,
            "location_id": SHELF,
            "quantity": 1,
            "waste_kind": "broken",
            "culprit_id": ROLES["nurse"].staff_id,
            "confirmer_id": ROLES["storekeeper"].staff_id,
            "photo_sha256": RECEIPT_SHA,
        },
    )
    phones.event(
        "multi_role", "card.transfer_reported", {"amount": 150_000, "admission_no": "A-1", "photo_sha256": RECEIPT_SHA}
    )

    # 3. The handover starts on the incoming phone and reads accounting at its own cutoff.
    started = phones.act("reception", "POST", "/api/v1/handover/start", {"flow": "reception"})
    handover_id = started["handover_id"]
    assert started["my_side"] is None or started["my_side"] == "incoming"
    stock = _stock(day)
    billed = _billed(day, "SYR-5")
    assert billed > 0, "the synthetic day bills syringes"
    expected = {PARA: 10 + 5 - 2, DICLO: 5 - 1, SYRINGE: 20 - billed}  # R = 5, U = 2, W = 1, D = billed
    assert {item: stock[(item, SHELF)] for item in expected} == expected
    box = _box(day)

    # 4. The box: outgoing first, then incoming, blind to each other. Then the shelf, incoming first.
    url = f"/api/v1/handover/{handover_id}"
    assert phones.act("multi_role", "POST", f"{url}/counts", {"scope": "cash", "counted": box}) == {
        "status": "incoming"
    }
    assert phones.act("reception", "POST", f"{url}/counts", {"scope": "cash", "counted": box}) == {"status": "matched"}
    assert phones.act("reception", "POST", f"{url}/advance")["step"] == "shelf"
    for item, quantity in expected.items():
        answer = phones.act(
            "reception",
            "POST",
            f"{url}/counts",
            {"scope": "item", "item_id": item, "location_id": SHELF, "counted": quantity},
        )
        assert answer == {"status": "matched"}, item

    # 5. The checklists, the parcels, the two signatures.
    assert phones.act("reception", "POST", f"{url}/advance")["step"] == "checklists"
    for row in phones.act("reception", "GET", f"{url}/targets")["checklists"]:
        body: dict[str, Any] = {"item_id": row["item_id"], "ok": True}
        if row["kind"] == "reading":
            body["reading"] = 5.0
        phones.act("reception", "POST", f"{url}/checklists", body)
    assert phones.act("reception", "POST", f"{url}/advance")["step"] == "parcels"
    assert phones.act("reception", "POST", f"{url}/advance")["step"] == "dual_sign"
    assert phones.act("reception", "POST", f"{url}/sign")["closed"] is False
    assert phones.act("multi_role", "POST", f"{url}/sign")["closed"] is True

    # 6. Nothing was a discrepancy, the books rebuild exactly, and no answer carried a figure.
    with day.app.state.database.read() as connection:
        kinds = [row[0] for row in connection.exec_driver_sql("SELECT type FROM events ORDER BY seq").all()]
        ends = [
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT expires_at FROM sessions WHERE staff_id = ? AND revoked_at IS NULL", (OUTGOING.staff_id,)
            ).all()
        ]
    assert "discrepancy.recorded" not in kinds
    assert "acc.consumption" in kinds and "shelf_return_confirmed" in kinds and "restock_confirmed" in kinds
    assert day.app.state.projections.rebuild().drift == ()
    now = day.app.state.clock.now_utc()
    assert ends and all(_ends_within(end, now, timedelta(hours=1, minutes=1)) for end in ends)
    figures = {str(box), f"{box:,}", *(str(q) for q in (13,))}
    for text in phones.bodies:
        assert not any(f'"counted": {figure}' in text or f'"expected": {figure}' in text for figure in figures)
        assert '"reference"' not in text


def _ends_within(end: str, now: Any, limit: timedelta) -> bool:
    from datetime import datetime

    return datetime.fromisoformat(end) - now <= limit


def _billed(client: TestClient, code: str) -> int:
    """D as accounting states it: the closed invoices' consumption of one stock code, whole units."""
    path = client.app.state.settings.accounting_db_path
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM v_shift_consumption_v1 WHERE acc_code = ?", (code,)
        ).fetchone()
    finally:
        connection.close()
    return round(row[0])
