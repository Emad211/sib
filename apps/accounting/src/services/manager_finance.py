"""Month-to-date figures for the manager's top tiles (FB-20) and the expenses ledger.

* Transactions: what patients paid on closed invoices (consumables included; this is cash flow,
  not "revenue", which keeps excluding consumables by the owner's rule Q-05).
* Expenses: what the manager recorded on the expenses page (voided rows excluded).
* Salaries: doctors' and nurses' pay by the payroll rules for the same days (reception pay is not
  in the system yet; the owner will define it).
* Balance: transactions - expenses - salaries.
"""

from __future__ import annotations

from datetime import date

from src.domain import jalali

EXPENSE_CATEGORIES = (
    "اجاره",
    "قبوض (آب، برق، گاز، تلفن، اینترنت)",
    "خرید دارو و لوازم مصرفی",
    "تعمیرات و نگهداری",
    "نظافت و پذیرایی",
    "دستمزد خارج از سیستم",
    "مالیات و عوارض",
    "سایر",
)
JALALI_MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")


def month_label(day: date) -> str:
    jy, jm, _ = jalali.to_jalali(day)
    return f"{JALALI_MONTHS[jm - 1]} {jy}"


def transactions(db, start: str, end: str) -> tuple[float, int]:
    row = db.execute(
        "SELECT COALESCE(SUM(total_amount), 0) AS total, COUNT(*) AS n FROM invoices"
        " WHERE status = 'closed' AND work_date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()
    return float(row["total"]), int(row["n"])


def expenses_total(db, start: str, end: str) -> float:
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE COALESCE(is_void, 0) = 0 AND spent_on BETWEEN ? AND ?",
        (start, end),
    ).fetchone()
    return float(row["total"])


def salaries_total(db, start: str, end: str) -> float:
    from src.api.manager.payroll import compute_payroll

    return float(sum(r["total_salary"] for r in compute_payroll(db, start, end)))


def month_tiles(db, today: date) -> dict:
    start, end = jalali.month_start(today).isoformat(), today.isoformat()
    paid, invoices = transactions(db, start, end)
    spent = expenses_total(db, start, end)
    pay = salaries_total(db, start, end)
    return {
        "month": month_label(today),
        "transactions": paid,
        "invoices": invoices,
        "expenses": spent,
        "salaries": pay,
        "balance": paid - spent - pay,
    }


def list_expenses(db, start: str, end: str) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM expenses WHERE spent_on BETWEEN ? AND ? ORDER BY spent_on DESC, id DESC", (start, end)
    ).fetchall()
    return [dict(r) | {"spent_on_fa": jalali.jalali_text(r["spent_on"])} for r in rows]


def totals_by_category(rows: list[dict]) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for r in rows:
        if not r["is_void"]:
            totals[r["category"]] = totals.get(r["category"], 0.0) + float(r["amount"])
    return sorted(totals.items(), key=lambda kv: -kv[1])
