"""Clinic expenses (FB-20): the manager records what the clinic spent; the dashboard sums it."""

from datetime import date

from flask import flash, g, redirect, render_template, request, url_for

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.utils import iran_now
from src.domain import jalali
from src.domain.money import parse_amount
from src.services import manager_finance as finance
from src.services.activity_logger import ActionCategory, ActionType, log_activity

from . import bp
from ._common import manager_required


def _month_bounds(text: str | None) -> tuple[date, date]:
    """'1405/08' (or empty = this month) -> first and last Gregorian day of that Jalali month."""
    today = iran_now().date()
    first = jalali.parse_jalali(f"{text}/01") if text else None
    first = first or jalali.month_start(today)
    jy, jm, _ = jalali.to_jalali(first)
    nxt = jalali.parse_jalali(f"{jy + (jm == 12)}/{1 if jm == 12 else jm + 1}/01")
    from datetime import timedelta

    return first, nxt - timedelta(days=1)


@bp.route("/expenses", methods=["GET", "POST"])
@login_required
@manager_required(flash_message="دسترسی محدود")
def expenses():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            spent_on = jalali.parse_jalali(request.form.get("spent_on", ""))
            amount = parse_amount(request.form.get("amount", ""))
            category = request.form.get("category", "")
            note = " ".join(request.form.get("note", "").split())[:300]
            if spent_on is None or spent_on > iran_now().date():
                flash("تاریخ نامعتبر است (تاریخ آینده هم پذیرفته نیست).", "error")
            elif amount is None or amount <= 0:
                flash("مبلغ نامعتبر است؛ فقط عدد بزرگ‌تر از صفر وارد کنید.", "error")
            elif category not in finance.EXPENSE_CATEGORIES:
                flash("دسته‌بندی را از فهرست انتخاب کنید.", "error")
            else:
                cur = db.execute(
                    "INSERT INTO expenses (spent_on, category, amount, note, created_by) VALUES (?, ?, ?, ?, ?)",
                    (spent_on.isoformat(), category, amount, note or None, g.user["username"]),
                )
                db.commit()
                log_activity(
                    ActionType.EXPENSE_ADD,
                    ActionCategory.SYSTEM,
                    target_type="expense",
                    target_id=cur.lastrowid,
                    target_name=category,
                    amount=amount,
                    description=f"ثبت هزینه: {category}",
                )
                flash("هزینه ثبت شد.", "success")
        elif action == "void":
            expense_id = request.form.get("expense_id", type=int)
            reason = " ".join(request.form.get("reason", "").split())[:200]
            row = db.execute(
                "SELECT * FROM expenses WHERE id = ? AND COALESCE(is_void, 0) = 0", (expense_id,)
            ).fetchone()
            if row is None:
                flash("هزینه پیدا نشد یا قبلاً باطل شده است.", "error")
            elif not reason:
                flash("دلیل ابطال را بنویسید.", "error")
            else:
                db.execute(
                    "UPDATE expenses SET is_void = 1, void_reason = ?, voided_by = ?, voided_at = ? WHERE id = ?",
                    (reason, g.user["username"], iran_now().strftime("%Y-%m-%d %H:%M:%S"), expense_id),
                )
                db.commit()
                log_activity(
                    ActionType.EXPENSE_VOID,
                    ActionCategory.SYSTEM,
                    target_type="expense",
                    target_id=expense_id,
                    target_name=row["category"],
                    amount=row["amount"],
                    old_value=reason,
                    description="ابطال هزینه",
                )
                flash("هزینه باطل شد.", "success")
        return redirect(url_for("manager.expenses", month=request.args.get("month", "")))

    month_text = request.args.get("month", "").strip()
    first, last = _month_bounds(month_text or None)
    rows = finance.list_expenses(db, first.isoformat(), last.isoformat())
    jy, jm, _ = jalali.to_jalali(first)
    prev_first = jalali.month_start(first.fromordinal(first.toordinal() - 1))
    pjy, pjm, _ = jalali.to_jalali(prev_first)
    njy, njm = (jy + 1, 1) if jm == 12 else (jy, jm + 1)
    return render_template(
        "manager/expenses.html",
        rows=rows,
        by_category=finance.totals_by_category(rows),
        total=sum(float(r["amount"]) for r in rows if not r["is_void"]),
        categories=finance.EXPENSE_CATEGORIES,
        month_label=finance.month_label(first),
        prev_month=f"{pjy}/{pjm:02d}",
        next_month=f"{njy}/{njm:02d}",
        today_fa=jalali.jalali_text(iran_now().date()),
    )
