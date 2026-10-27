"""Every manager report page offers the Jalali date picker (FB-24)."""

import pytest
from conftest import login_as

PAGES = [
    "/manager/reports/invoices",
    "/manager/reports/visits",
    "/manager/reports/nursing",
    "/manager/reports/procedures",
    "/manager/reports/users",
    "/manager/reports/consumables",
    "/manager/reports/patients",
    "/manager/logs",
    "/manager/insurance_arrears",
    "/manager/payroll",
]


@pytest.mark.parametrize("path", PAGES)
def test_report_page_loads_the_date_picker(synthetic_app, path):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    r = c.get(path)
    assert r.status_code == 200, path
    html = r.get_data(as_text=True)
    assert "js/jdatepicker.js" in html and "js/report-dates.js" in html and "js/jalaali.min.js" in html


def test_report_still_filters_by_jalali_range(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    assert c.get("/manager/reports/invoices?from=1405/07/01&to=1405/07/02").status_code == 200
