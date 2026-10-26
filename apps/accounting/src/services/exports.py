"""Report exports: CSV and Excel (.xlsx) from the same rows (ACC-2.8, A06 §3.2).

* CSV keeps the UTF-8 BOM so Excel reads Persian correctly, and neutralises spreadsheet
  formulas: a text cell that starts with `= + - @` (or a control character) is prefixed with
  `'`, otherwise a crafted patient name or note would run as a formula when the manager
  opens the file.
* Excel is written with the standard library only (no new dependency in the exe). Text is
  stored as text, so a national ID such as `0012345678` keeps its leading zero (Excel drops
  it when it opens a CSV), amounts are real numbers with thousands separators, the sheet is
  right-to-left with a frozen, bold header, and nothing is ever evaluated as a formula.
* Any route using `table_response` gets both formats: `?format=xlsx` selects Excel.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, datetime
from xml.sax.saxutils import escape

from flask import make_response, request

CSV_MIME = "text/csv; charset=utf-8"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")
_PLAIN_NUMBER = re.compile(r"[-+]?\d[\d,]*(\.\d+)?")
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def csv_safe(value):
    """Return a CSV cell value that a spreadsheet will never interpret as a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_START) and not _PLAIN_NUMBER.fullmatch(value):
        return "'" + value
    return value


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def build_csv(headers, rows) -> bytes:
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out)
    writer.writerow([csv_safe(h) for h in headers])
    for row in rows:
        writer.writerow([csv_safe(v) if isinstance(v, str) else ("" if v is None else v) for v in row])
    return out.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- xlsx


def _col_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _xml_text(value: str) -> str:
    return escape(_ILLEGAL_XML.sub("", value))


def build_xlsx(headers, rows, sheet_title: str = "گزارش", plain_cols=()) -> bytes:
    """A one-sheet workbook. Style ids: 0 normal, 1 header, 2 money (#,##0), 3 text (@).

    Integers/floats are numbers with a thousands separator, except in `plain_cols` (column
    indexes such as an invoice number) which stay unformatted.
    """
    headers = list(headers)
    rows = [list(r) for r in rows]
    widths = [min(max(len(_cell_text(h)) + 4, 10), 40) for h in headers]
    for row in rows[:500]:
        for i, v in enumerate(row[: len(widths)]):
            widths[i] = min(max(widths[i], len(_cell_text(v)) + 2), 40)

    def cell(ref, value, style=0, plain=False):
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, (int, float)):
            if not style and not plain and float(value).is_integer():
                style = 2
            return f'<c r="{ref}" s="{style}"><v>{value!r}</v></c>'
        text = _cell_text(value)
        if text == "":
            return f'<c r="{ref}" s="{style}"/>'
        return f'<c r="{ref}" s="{3 if style == 0 else style}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(text)}</t></is></c>'

    sheet_rows = ['<row r="1">' + "".join(cell(f"{_col_letter(i)}1", h, 1) for i, h in enumerate(headers)) + "</row>"]
    for n, row in enumerate(rows, start=2):
        sheet_rows.append(
            f'<row r="{n}">'
            + "".join(cell(f"{_col_letter(i)}{n}", v, plain=i in plain_cols) for i, v in enumerate(row))
            + "</row>"
        )
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView rightToLeft="1" workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{cols}</cols><sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE2E8F0"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="4">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        "</cellXfs></styleSheet>"
    )
    title = _xml_text(re.sub(r"[\[\]:*?/\\]", " ", sheet_title)[:31] or "Sheet1")
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{title}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": styles,
        "xl/worksheets/sheet1.xml": sheet,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in parts.items():
            z.writestr(name, content)
    return buf.getvalue()


# --------------------------------------------------------------------------- flask glue


def export_filename(base: str, ext: str, date_from: str = "", date_to: str = "") -> str:
    """ASCII-only name such as `invoices_1405-07-01_1405-07-30.xlsx` (Jalali range from the filters)."""
    stem = _SAFE_NAME.sub("", base.rsplit(".", 1)[0]) or "report"
    span = "_".join(
        _SAFE_NAME.sub("", d.replace("/", "-"))
        for d in (date_from, date_to)
        if d and _SAFE_NAME.sub("", d.replace("/", "-"))
    )
    return f"{stem}_{span}.{ext}" if span else f"{stem}.{ext}"


def table_response(rows, headers, filename: str, sheet_title: str = "گزارش", plain_cols=()):
    """Send `rows` as CSV (default) or Excel (`?format=xlsx`), named after the filter range."""
    fmt = "xlsx" if request.args.get("format", "").lower() == "xlsx" else "csv"
    name = export_filename(
        filename,
        fmt,
        request.args.get("from", "") or request.args.get("date_from", ""),
        request.args.get("to", "") or request.args.get("date_to", ""),
    )
    if fmt == "xlsx":
        body, mime = build_xlsx(headers, rows, sheet_title, plain_cols), XLSX_MIME
    else:
        body, mime = build_csv(headers, rows), CSV_MIME
    response = make_response(body)
    response.headers["Content-Type"] = mime
    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    response.headers["Cache-Control"] = "no-store"
    return response
