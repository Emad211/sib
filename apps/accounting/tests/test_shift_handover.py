"""Schedule-based shift start and the handover rule (FB-01, FB-02)."""

import sqlite3
from datetime import datetime, time

import pytest
from conftest import login_as

from src.domain import shifts
from src.services import shift_handover

D = "2026-10-26"


def at(hhmm, day=D):
    return datetime.fromisoformat(f"{day} {hhmm}:00")


# --------------------------------------------------------------------------- the schedule


@pytest.mark.parametrize(
    "when, expected",
    [
        (at("08:00"), ("morning", D)),
        (at("07:00"), ("morning", D)),  # 30 min early counts as the next shift
        (at("06:59"), ("night", "2026-10-25")),  # still the night that started yesterday
        (at("13:00"), ("evening", D)),
        (at("12:59"), ("morning", D)),
        (at("15:45"), ("evening", D)),  # late takeover: still the window we are in
        (at("19:00"), ("night", D)),
        (at("23:59"), ("night", D)),
        (at("00:30", "2026-10-27"), ("night", D)),  # after midnight keeps the start date
        (at("07:29", "2026-10-27"), ("morning", "2026-10-27")),
    ],
)
def test_scheduled_shift(when, expected):
    assert shifts.scheduled_shift(when) == expected


def test_custom_start_times_and_zero_early_window():
    starts = {"morning": time(8, 0), "evening": time(14, 0), "night": time(20, 0)}
    assert shifts.scheduled_shift(at("07:45"), starts, early_minutes=0) == ("night", "2026-10-25")
    assert shifts.scheduled_shift(at("07:45"), starts) == ("morning", D)


def test_previous_shift_and_standing():
    assert shifts.previous_shift("morning", D) == ("night", "2026-10-25")
    assert shifts.previous_shift("evening", D) == ("morning", D)
    assert shifts.previous_shift("night", D) == ("evening", D)
    now = ("evening", D)
    assert shifts.standing(("evening", D), now) == shifts.CURRENT
    assert shifts.standing(("evening", D + " 13:31:00"), now) == shifts.CURRENT  # time part ignored
    assert shifts.standing(("morning", D), now) == shifts.FINISHING
    assert shifts.standing(("night", "2026-10-25"), now) == shifts.STALE
    assert shifts.standing(("evening", "2026-10-25"), now) == shifts.STALE
    assert shifts.standing(None, now) == shifts.STALE


@pytest.mark.parametrize(
    "text, fallback, expected",
    [
        ("07:30", time(1, 0), time(7, 30)),
        ("x", time(1, 0), time(1, 0)),
        (None, time(2, 0), time(2, 0)),
        ("25:00", time(3, 0), time(3, 0)),
    ],
)
def test_parse_hhmm(text, fallback, expected):
    assert shifts.parse_hhmm(text, fallback) == expected


# --------------------------------------------------------------------------- the service on a database


@pytest.fixture()
def db(synthetic_app):
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def uid(db, name):
    return db.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def put(db, name, shift, work_date):
    db.execute(
        "INSERT OR REPLACE INTO user_active_shift (user_id, active_shift, work_date) VALUES (?, ?, ?)",
        (uid(db, name), shift, work_date),
    )
    db.commit()


def test_schedule_comes_from_settings_and_a_broken_one_falls_back(db):
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('shift_start_morning', '08:00')")
    db.commit()
    assert shift_handover.load_schedule(db)["morning"] == time(8, 0)
    db.execute("UPDATE settings SET value = '15:00' WHERE key = 'shift_start_morning'")  # after evening: nonsense
    db.commit()
    assert shift_handover.load_schedule(db) == shifts.DEFAULT_STARTS


def test_open_invoices_of_another_shift_block_the_start(db):
    found = shift_handover.blockers(db, ("evening", D))
    assert found and all(set(b) == {"invoice_id", "who", "shift_fa", "work_date_fa"} for b in found)
    db.execute("UPDATE invoices SET status = 'closed' WHERE status = 'open'")
    db.commit()
    assert shift_handover.blockers(db, ("evening", D)) == []


def test_an_open_invoice_of_the_shift_being_started_is_not_a_blocker(db):
    db.execute("UPDATE invoices SET status = 'closed' WHERE status = 'open'")
    db.execute(
        "UPDATE invoices SET status = 'open', work_date = ?, shift = 'evening' WHERE id = (SELECT MIN(id) FROM invoices)",
        (D,),
    )
    db.commit()
    assert shift_handover.blockers(db, ("evening", D)) == []


def test_who_may_open_invoices(db):
    now = at("15:00")  # evening
    put(db, "reception1", "night", "2026-10-20")  # stale: from days ago
    ok, reason = shift_handover.may_open_invoice(db, uid(db, "reception1"), now)
    assert not ok and "شروع شیفت عصر" in reason
    put(db, "reception2", "morning", D)  # stayed late and nobody took over yet: keeps working
    assert shift_handover.may_open_invoice(db, uid(db, "reception2"), now) == (True, None)
    put(db, "reception1", "evening", D)  # now the evening shift has started
    assert shift_handover.may_open_invoice(db, uid(db, "reception1"), now) == (True, None)
    ok, reason = shift_handover.may_open_invoice(db, uid(db, "reception2"), now)
    assert not ok and "تحویل داده شده" in reason


# --------------------------------------------------------------------------- the routes


def client(app, name="reception1"):
    c = app.test_client()
    login_as(c, app, name)
    return c


def test_start_shift_is_refused_while_another_shift_has_open_invoices(synthetic_app):
    r = client(synthetic_app).post("/reception/api/shift/change", json={"shift": "night"})
    body = r.get_json()
    assert r.status_code == 409 and body["blockers"] and "تسویه و ببندد" in body["error"]


def test_start_shift_starts_the_scheduled_shift_whatever_is_requested(synthetic_app):
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    conn.execute("UPDATE invoices SET status = 'closed' WHERE status = 'open'")
    conn.commit()
    conn.close()
    from src.common.utils import iran_now

    expected = shifts.scheduled_shift(iran_now())
    c = client(synthetic_app)
    r = c.post("/reception/api/shift/change", json={"shift": "bogus", "work_date": "1999-01-01"})
    assert r.status_code == 200 and (r.get_json()["new_shift"], r.get_json()["work_date"]) == expected
    status = c.get("/reception/api/shift/status").get_json()
    assert status["standing"] == "current" and status["needs_start"] is False and status["can_open_invoices"] is True
    assert c.post("/reception/api/shift/change", json={}).status_code == 200  # starting again is harmless


def test_a_stale_user_cannot_open_an_invoice(synthetic_app):
    r = client(synthetic_app).post(
        "/reception/new",
        data={
            "name": "تست",
            "family_name": "ثبت",
            "phone": "09123456789",
            "national_id": "2170415981",
            "insurance_type": "آزاد",
        },
    )
    assert r.status_code == 409 and "شروع شیفت" in r.get_json()["error"]


def test_status_reports_the_schedule(synthetic_app):
    s = client(synthetic_app).get("/reception/api/shift/status").get_json()
    assert {
        "scheduled_shift",
        "scheduled_shift_fa",
        "scheduled_work_date",
        "standing",
        "needs_start",
        "can_open_invoices",
        "handed_over",
    } <= set(s)


def test_the_page_offers_start_shift_instead_of_manual_change(synthetic_app):
    html = client(synthetic_app).get("/reception/").get_data(as_text=True)
    assert "تغییر شیفت دستی" not in html and "شروع شیفت" in html
