"""Reception screens: most-used items first (FB-06) and ×N grouping of identical lines (FB-09)."""

import sqlite3

from conftest import login_as, put_on_current_shift


def run(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    cur = conn.execute(sql, args)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last


def q(app, sql, *args):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_nursing_and_consumable_lists_put_the_most_used_first(synthetic_app):
    from src.adapters.sqlite.consumable_tariffs_repo import ConsumableTariffsRepository
    from src.adapters.sqlite.nursing_services_repo import NursingServicesRepository
    from src.common.utils import iran_now

    today = iran_now().strftime("%Y-%m-%d")
    least = run(
        synthetic_app, "INSERT INTO nursing_services (service_name, unit_price, is_active) VALUES ('ییی کمتر', 1, 1)"
    )
    most = run(
        synthetic_app, "INSERT INTO nursing_services (service_name, unit_price, is_active) VALUES ('ییی بیشتر', 1, 1)"
    )
    pid = q(synthetic_app, "SELECT id FROM patients LIMIT 1")[0][0]
    for _ in range(500):  # far more than any synthetic service in the window
        run(
            synthetic_app,
            "INSERT INTO injections (patient_id, injection_type, service_id, work_date) VALUES (?, 'x', ?, ?)",
            pid,
            most,
            today,
        )
    run(
        synthetic_app,
        "INSERT INTO consumable_tariffs (name, default_price, category, is_active) VALUES ('ییی دارو', 1, 'drug', 1)",
    )
    for _ in range(500):
        run(
            synthetic_app,
            "INSERT INTO consumables_ledger (patient_id, item_name, work_date) VALUES (?, 'ییی دارو', ?)",
            pid,
            today,
        )
    with synthetic_app.app_context():
        services = NursingServicesRepository().list_active()
        drugs = ConsumableTariffsRepository().list_active("drug")
    assert services[0]["id"] == most
    assert [s["id"] for s in services].index(least) > 0
    assert drugs[0]["name"] == "ییی دارو"


def test_old_usage_does_not_count(synthetic_app):
    from src.adapters.sqlite.nursing_services_repo import NursingServicesRepository

    old = run(
        synthetic_app, "INSERT INTO nursing_services (service_name, unit_price, is_active) VALUES ('ییی قدیمی', 1, 1)"
    )
    pid = q(synthetic_app, "SELECT id FROM patients LIMIT 1")[0][0]
    for _ in range(500):
        run(
            synthetic_app,
            "INSERT INTO injections (patient_id, injection_type, service_id, work_date) VALUES (?, 'x', ?, '2020-01-01')",
            pid,
            old,
        )
    with synthetic_app.app_context():
        services = NursingServicesRepository().list_active()
    assert services[0]["id"] != old


def test_identical_lines_show_once_with_times_n(synthetic_app):
    pid = q(synthetic_app, "SELECT id FROM patients LIMIT 1")[0][0]
    inv = run(
        synthetic_app,
        "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, opened_by)"
        " VALUES (?, 'open', 'آزاد', 0, '2026-09-24', 'morning', '2026-09-24 08:00:00', 'reception1')",
        pid,
    )
    for _ in range(3):
        run(
            synthetic_app,
            "INSERT INTO injections (patient_id, injection_type, work_date, total_price, invoice_id) VALUES (?, 'سرم تست', '2026-09-24', 50000, ?)",
            pid,
            inv,
        )
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    put_on_current_shift(synthetic_app, "reception1")
    html = c.get(f"/reception/?invoice_id={inv}").get_data(as_text=True)
    assert html.count('class="group-row"') == 1 and "×۳" in html
    assert html.count('class="group-member g0"') == 3  # the single rows are still there (for paying / deleting one)
