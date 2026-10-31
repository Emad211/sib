import logging
from datetime import timedelta

from flask import (
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.utils import format_jalali_datetime, iran_now
from src.services.activity_logger import ActionCategory, ActionType, log_activity
from src.services.auth_service import USER_ROLES, password_length_error

from . import bp
from ._common import manager_required

logger = logging.getLogger(__name__)


@bp.route("/staff")
@login_required
@manager_required
def staff():
    """Staff are managed on the users page (BUG-02: manager/staff.html never existed)."""
    return redirect(url_for("manager.users_management"))


@bp.route("/users", methods=["GET", "POST"])
@login_required
@manager_required(flash_message="دسترسی محدود")
def users_management():
    """مدیریت کاربران سیستم (مدیر و پذیرش) و کادر درمان (پزشک و پرستار)"""

    db = get_db()

    if request.method == "POST":
        action = request.form.get("action")
        entity_type = request.form.get("entity_type")  # 'user' or 'staff'

        if entity_type == "user":
            if action == "add":
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "").strip()
                full_name = request.form.get("full_name", "").strip()
                role = request.form.get("role", "reception")

                # SEC-07: a password that is being SET must reach the minimum length
                problem = password_length_error(password) if password else None

                if role not in USER_ROLES:
                    flash("نقش کاربر نامعتبر است.", "error")
                elif problem:
                    flash(problem, "error")
                elif username and password and full_name:
                    # چک کردن تکراری نبودن نام کاربری
                    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                    if existing:
                        flash(f'نام کاربری "{username}" قبلاً استفاده شده است.', "error")
                    else:
                        # Use AuthService.register_user to create the user with bcrypt
                        from src.services.auth_service import AuthService

                        auth_service = AuthService()
                        created = auth_service.register_user(username, password, role, full_name)
                        if created:
                            flash(f'کاربر "{full_name}" با موفقیت اضافه شد.', "success")
                        else:
                            flash(f'خطا در ایجاد کاربر "{full_name}".', "error")

            elif action == "unlock":
                user_id = request.form.get("user_id", type=int)
                target = (
                    db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
                )
                if target:
                    db.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (user_id,))
                    db.commit()
                    log_activity(
                        ActionType.USER_UNLOCK,
                        ActionCategory.SYSTEM,
                        target_type="user",
                        target_id=user_id,
                        target_name=target["username"],
                    )
                    flash(f"قفل حساب «{target['username']}» باز شد.", "success")
                else:
                    flash("کاربر یافت نشد.", "error")

            elif action == "update":
                user_id = request.form.get("user_id")
                username = request.form.get("username", "").strip()
                full_name = request.form.get("full_name", "").strip()
                role = request.form.get("role", "reception")
                is_active = request.form.get("is_active") == "1"
                new_password = request.form.get("new_password", "").strip()
                # SEC-07: a password that is being CHANGED must reach the minimum length
                problem = password_length_error(new_password) if new_password else None

                if role not in USER_ROLES:
                    flash("نقش کاربر نامعتبر است.", "error")
                elif problem:
                    flash(problem, "error")
                elif user_id and username and full_name:
                    # چک کردن تکراری نبودن نام کاربری برای کاربر دیگر
                    existing = db.execute(
                        "SELECT id FROM users WHERE username = ? AND id != ?", (username, user_id)
                    ).fetchone()
                    if existing:
                        flash(f'نام کاربری "{username}" قبلاً استفاده شده است.', "error")
                    else:
                        if new_password:
                            from werkzeug.security import generate_password_hash

                            password_hash = generate_password_hash(new_password)
                            db.execute(
                                "UPDATE users SET username = ?, password_hash = ?, role = ?, full_name = ?, is_active = ? WHERE id = ?",
                                (username, password_hash, role, full_name, 1 if is_active else 0, user_id),
                            )
                        else:
                            db.execute(
                                "UPDATE users SET username = ?, role = ?, full_name = ?, is_active = ? WHERE id = ?",
                                (username, role, full_name, 1 if is_active else 0, user_id),
                            )
                        db.commit()
                        flash("کاربر با موفقیت بروزرسانی شد.", "success")

            elif action == "delete":
                # #17: «حذف» is a deactivation. The users row is never erased — activity_logs
                # joins it by username, so a DELETE took the person's history with it.
                user_id = request.form.get("user_id", type=int)
                # نباید کاربر فعلی را حذف/غیرفعال کند
                if not user_id or user_id == g.user["id"]:
                    flash("نمی‌توانید حساب کاربری خودتان را حذف کنید.", "error")
                else:
                    target = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
                    if target:
                        db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
                        db.commit()
                        log_activity(
                            ActionType.USER_DEACTIVATE,
                            ActionCategory.SYSTEM,
                            target_type="user",
                            target_id=user_id,
                            target_name=target["username"],
                        )
                        flash(f"کاربر «{target['username']}» غیرفعال شد.", "success")
                    else:
                        flash("کاربر یافت نشد.", "error")

        elif entity_type == "staff":
            if action == "add":
                full_name = request.form.get("full_name", "").strip()
                staff_type = request.form.get("staff_type", "doctor")

                if staff_type not in ("doctor", "nurse"):
                    flash("نوع کادر درمان نامعتبر است.", "error")
                elif full_name:
                    db.execute(
                        "INSERT INTO medical_staff (full_name, staff_type, is_active) VALUES (?, ?, 1)",
                        (full_name, staff_type),
                    )
                    db.commit()
                    flash(
                        f'{"پزشک" if staff_type == "doctor" else "پرستار"} "{full_name}" با موفقیت اضافه شد.', "success"
                    )

            elif action == "update":
                staff_id = request.form.get("staff_id")
                full_name = request.form.get("full_name", "").strip()
                staff_type = request.form.get("staff_type", "doctor")
                is_active = request.form.get("is_active") == "1"

                if staff_id and full_name:
                    db.execute(
                        "UPDATE medical_staff SET full_name = ?, staff_type = ?, is_active = ? WHERE id = ?",
                        (full_name, staff_type, 1 if is_active else 0, staff_id),
                    )
                    db.commit()
                    flash("اطلاعات کادر درمان با موفقیت بروزرسانی شد.", "success")

            elif action == "delete":
                staff_id = request.form.get("staff_id")
                if staff_id:
                    db.execute("DELETE FROM medical_staff WHERE id = ?", (staff_id,))
                    db.commit()
                    flash("کادر درمان با موفقیت حذف شد.", "success")

        return redirect(url_for("manager.users_management"))

    # دریافت لیست‌ها
    now_text = iran_now().strftime("%Y-%m-%d %H:%M:%S")
    month_ago = (iran_now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    ops = {
        r["username"]: r["n"]
        for r in db.execute(
            "SELECT username, COUNT(*) AS n FROM activity_logs WHERE created_at >= ? AND action_category != 'auth' GROUP BY username",
            (month_ago,),
        )
    }
    users = []
    for row in db.execute("SELECT * FROM users ORDER BY role, full_name").fetchall():
        u = dict(row)
        u["is_locked"] = bool(u["locked_until"]) and str(u["locked_until"]).replace("T", " ") > now_text
        u["last_login_fa"] = format_jalali_datetime(u["last_login"]) if u["last_login"] else "—"
        u["ops_30d"] = ops.get(u["username"], 0)
        users.append(u)
    staff = db.execute("SELECT * FROM medical_staff ORDER BY staff_type, full_name").fetchall()

    managers = [u for u in users if u["role"] == "manager"]
    receptions = [u for u in users if u["role"] == "reception"]
    doctors = [s for s in staff if s["staff_type"] == "doctor"]
    nurses = [s for s in staff if s["staff_type"] == "nurse"]

    return render_template(
        "manager/users.html", users=users, managers=managers, receptions=receptions, doctors=doctors, nurses=nurses
    )
