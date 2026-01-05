import functools
from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for
)

from src.services.auth_service import AuthService
from src.services.activity_logger import log_activity, ActionType, ActionCategory
from src.adapters.sqlite.shift_staff_repo import ShiftStaffRepository


bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=("GET", "POST"))
def login():
    service = AuthService()

    # For reception role we want a dropdown of active reception users
    reception_users = service.get_reception_users()

    if request.method == "POST":
        role = request.form.get("role", "reception")  # "manager" or "reception"
        password = request.form.get("password", "")
        error = None

        if role == "manager":
            username = request.form.get("username", "")
            user = service.validate_manager(username, password)
        else:
            username = request.form.get("reception_username", "")
            user = service.validate_reception(username, password)

        if user is None:
            error = "نام کاربری یا رمز عبور نادرست است، یا حساب شما موقتا قفل شده است."  # similar message as desktop behavior

        if error is None:
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            
            # لاگ ورود کاربر
            log_activity(
                action_type=ActionType.LOGIN,
                action_category=ActionCategory.AUTH,
                description=f'ورود {user["role"]} - {user["username"]}',
                user_id=user["id"],
                username=user["username"]
            )
            
            # Redirect based on role
            if user["role"] == "manager":
                return redirect(url_for("manager.index"))
            else:
                return redirect(url_for("reception.index"))

        flash(error)

    return render_template("auth/login.html", reception_users=reception_users)


@bp.route("/logout")
def logout():
    # لاگ خروج کاربر
    if g.user:
        log_activity(
            action_type=ActionType.LOGOUT,
            action_category=ActionCategory.AUTH,
            description=f'خروج {g.user["role"]} - {g.user["username"]}',
            user_id=g.user["id"],
            username=g.user["username"]
        )
    
    session.clear()
    return redirect(url_for("auth.login"))


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))

        return view(**kwargs)

    return wrapped_view
