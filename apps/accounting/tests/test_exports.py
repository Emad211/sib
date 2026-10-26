"""CSV / Excel exports (ACC-2.8) and the delete-amount audit fix (BUG-05)."""

import io
import sqlite3
import zipfile
from xml.etree import ElementTree as ET

import pytest
from conftest import login_as

from src.services import exports

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _sheet(xlsx: bytes):
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        assert z.testzip() is None
        for name in z.namelist():  # every part must be well-formed XML
            ET.fromstring(z.read(name))
        return ET.fromstring(z.read("xl/worksheets/sheet1.xml")), ET.fromstring(z.read("xl/workbook.xml"))


def _cells(sheet):
    return {c.get("r"): c for c in sheet.iterfind(".//m:c", NS)}


# --------------------------------------------------------------------------- csv


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", '=HYPERLINK("http://x")'])
def test_formula_like_text_is_neutralised_in_csv(value):
    assert exports.csv_safe(value) == "'" + value


@pytest.mark.parametrize("value", ["علی", "-5", "+12", "1,234", "-1,234.5", "", "a=b", 12, None])
def test_normal_cells_are_left_alone(value):
    assert exports.csv_safe(value) == value


def test_csv_has_bom_headers_and_safe_cells():
    raw = exports.build_csv(["نام", "مبلغ"], [["=EVIL()", 5], ["ب", None]])
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig").splitlines()
    assert text == ["نام,مبلغ", "'=EVIL(),5", "ب,"]


# --------------------------------------------------------------------------- xlsx


def test_xlsx_is_valid_rtl_with_frozen_header_and_persian_text():
    sheet, wb = _sheet(exports.build_xlsx(["نام", "مبلغ"], [["علی", 1500000]], "گزارش فاکتور"))
    assert wb.find(".//m:sheet", NS).get("name") == "گزارش فاکتور"
    view = sheet.find(".//m:sheetView", NS)
    assert view.get("rightToLeft") == "1" and view.find("m:pane", NS).get("state") == "frozen"
    cells = _cells(sheet)
    assert cells["A1"].find(".//m:t", NS).text == "نام" and cells["A2"].find(".//m:t", NS).text == "علی"


def test_national_id_keeps_its_leading_zero_and_amounts_are_numbers():
    sheet, _ = _sheet(exports.build_xlsx(["کد ملی", "مبلغ"], [["0012345678", 1234567]]))
    cells = _cells(sheet)
    assert cells["A2"].get("t") == "inlineStr" and cells["A2"].find(".//m:t", NS).text == "0012345678"
    assert cells["B2"].get("t") is None and cells["B2"].find("m:v", NS).text == "1234567"
    assert cells["B2"].get("s") == "2"  # thousands separator


def test_plain_columns_have_no_thousands_separator():
    sheet, _ = _sheet(exports.build_xlsx(["شماره", "مبلغ"], [[29619, 29619]], plain_cols=(0,)))
    cells = _cells(sheet)
    assert cells["A2"].get("s") == "0" and cells["B2"].get("s") == "2"


def test_formulas_are_stored_as_text_in_xlsx():
    sheet, _ = _sheet(exports.build_xlsx(["x"], [["=1+1"]]))
    cell = _cells(sheet)["A2"]
    assert cell.find("m:f", NS) is None and cell.find(".//m:t", NS).text == "=1+1"


def test_control_characters_and_markup_cannot_break_the_file():
    sheet, _ = _sheet(exports.build_xlsx(["x"], [["a\x00b\x0bc <&> ]]>"]]))
    assert _cells(sheet)["A2"].find(".//m:t", NS).text == "abc <&> ]]>"


def test_empty_report_is_still_a_valid_workbook():
    sheet, _ = _sheet(exports.build_xlsx(["a", "b"], []))
    assert set(_cells(sheet)) == {"A1", "B1"}


def test_sheet_title_is_made_safe():
    _, wb = _sheet(exports.build_xlsx(["a"], [], "گزارش/بد:نام" + "x" * 50))
    name = wb.find(".//m:sheet", NS).get("name")
    assert len(name) <= 31 and not set(name) & set("[]:*?/\\")


def test_many_columns_get_valid_references():
    assert [exports._col_letter(i) for i in (0, 25, 26, 27, 51, 52, 701, 702)] == [
        "A",
        "Z",
        "AA",
        "AB",
        "AZ",
        "BA",
        "ZZ",
        "AAA",
    ]


# --------------------------------------------------------------------------- filenames


def test_filenames_are_ascii_and_carry_the_range():
    assert (
        exports.export_filename("users_report.csv", "xlsx", "1405/07/01", "1405/07/30")
        == "users_report_1405-07-01_1405-07-30.xlsx"
    )
    assert exports.export_filename("invoices", "csv") == "invoices.csv"
    hostile = exports.export_filename('../../x"\r\ny', "csv", '1405/07/01"', "")
    assert hostile.isascii() and not set(hostile) & set('"/\\\r\n')


# --------------------------------------------------------------------------- routes


EXPORTS = [
    "/manager/export/invoices/csv",
    "/manager/export/visits/csv",
    "/manager/export/nursing/csv",
    "/manager/export/procedures/csv",
    "/manager/export/consumables/csv",
    "/manager/export/users/csv",
    "/manager/export/patients/csv",
    "/manager/insurance_arrears/export",
    "/manager/logs/export",
]


@pytest.mark.parametrize("url", EXPORTS)
def test_every_export_offers_csv_and_a_valid_excel_file(synthetic_app, url):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    csv_resp = c.get(url)
    assert csv_resp.status_code == 200 and csv_resp.data.startswith(b"\xef\xbb\xbf")
    assert csv_resp.headers["Content-Disposition"].endswith('.csv"')

    xlsx_resp = c.get(url + ("&" if "?" in url else "?") + "format=xlsx&from=1405/01/01&to=1405/12/29")
    assert xlsx_resp.status_code == 200 and "spreadsheetml" in xlsx_resp.headers["Content-Type"]
    assert xlsx_resp.headers["Content-Disposition"].endswith('1405-01-01_1405-12-29.xlsx"')
    sheet, _ = _sheet(xlsx_resp.data)
    assert "A1" in _cells(sheet)  # header row present


def test_export_buttons_are_on_the_report_pages(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    for page in (
        "/manager/reports/invoices",
        "/manager/reports/patients",
        "/manager/logs",
        "/manager/insurance_arrears",
    ):
        assert "format=xlsx" in c.get(page).get_data(as_text=True), page


def test_invoice_export_has_no_formula_from_a_hostile_patient_name(synthetic_app):
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    pid = conn.execute("SELECT patient_id FROM invoices LIMIT 1").fetchone()[0]
    conn.execute("UPDATE patients SET name = '=HYPERLINK(\"http://evil\")', family_name = '' WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    body = c.get("/manager/export/invoices/csv?from=1300/01/01&to=1500/01/01").data.decode("utf-8-sig")
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body


def test_export_is_manager_only(synthetic_app):
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    assert c.get("/manager/export/invoices/csv").status_code in (302, 403)


def test_activity_log_export_is_no_longer_capped_at_10000_rows(synthetic_app):
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    conn.executemany(
        "INSERT INTO activity_logs (username, action_type, action_category, created_at) VALUES ('bulk', 'login', 'auth', '2026-01-01 00:00:00')",
        [()] * 10500,
    )
    conn.commit()
    conn.close()
    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "manager1")
    lines = c.get("/manager/logs/export?user_id=").data.decode("utf-8-sig").splitlines()
    assert len(lines) > 10001


# --------------------------------------------------------------------------- BUG-05


@pytest.mark.parametrize(
    "table, cols, vals, kind, amount, name",
    [
        ("visits", "doctor_name, price", "'دکتر ت', 350000", "visit", 350000, "دکتر ت"),
        ("injections", "injection_type, total_price", "'سرم', 120000", "injection", 120000, "سرم"),
        ("procedures", "procedure_type, price", "'بخیه', 200000", "procedure", 200000, "بخیه"),
        ("consumables_ledger", "item_name, total_cost", "'گاز', 15000", "consumable", 15000, "گاز"),
    ],
)
def test_deleting_an_item_logs_its_real_amount_and_name(synthetic_app, table, cols, vals, kind, amount, name):
    """The log used to record amount 0 for every deletion (sqlite3.Row has no .get())."""
    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    pid = conn.execute("SELECT id FROM patients LIMIT 1").fetchone()[0]
    inv = conn.execute(
        "INSERT INTO invoices (patient_id, status, insurance_type, total_amount, work_date, shift, opened_at, opened_by)"
        " VALUES (?, 'open', 'آزاد', 0, '2026-09-24', 'morning', '2026-09-24 08:00:00', 'reception1')",
        (pid,),
    ).lastrowid
    item = conn.execute(
        f"INSERT INTO {table} (patient_id, work_date, invoice_id, {cols}) VALUES (?, '2026-09-24', ?, {vals})",
        (pid, inv),
    ).lastrowid
    conn.commit()
    conn.close()

    c = synthetic_app.test_client()
    login_as(c, synthetic_app, "reception1")
    r = c.post("/reception/item/delete", data={"invoice_id": inv, "item_type": kind, "item_id": item})
    assert r.status_code == 200, r.get_data(as_text=True)

    conn = sqlite3.connect(synthetic_app.config["DATABASE_PATH"])
    logged = conn.execute(
        "SELECT amount, target_name FROM activity_logs WHERE invoice_id = ? AND action_type = ?",
        (inv, f"{kind}_delete"),
    ).fetchone()
    conn.close()
    assert logged == (amount, name)
