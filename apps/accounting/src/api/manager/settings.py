import logging

from flask import (
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src.adapters.sqlite.core import get_db
from src.api.auth import login_required
from src.common.jalali import Gregorian
from src.common.utils import iran_now
from src.services.activity_logger import ActionCategory, ActionType, log_activity

from . import bp
from ._common import manager_api_required, manager_required

logger = logging.getLogger(__name__)


def get_network_info():
    """Get network information for the server."""
    import socket

    from flask import request

    # Get actual port from request or default to 8080
    try:
        current_port = request.host.split(":")[1] if ":" in request.host else "8080"
    except Exception:
        current_port = "8080"

    network_info = {
        "hostname": socket.gethostname(),
        "local_ips": [],
        "port": current_port,
        "access_urls": [],
        "wifi_name": None,
        "connection_type": None,
    }

    try:
        # Get all network interfaces
        hostname = socket.gethostname()

        # Method 1: Get local IPs
        try:
            # Get primary IP by connecting to external server (doesn't actually connect)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
            s.close()
            if primary_ip and primary_ip not in network_info["local_ips"]:
                network_info["local_ips"].append(primary_ip)
        except Exception:
            logger.warning("get_network_info: ignored error", exc_info=True)

        # Method 2: Get all IPs from hostname
        try:
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if ip not in network_info["local_ips"] and not ip.startswith("127."):
                    network_info["local_ips"].append(ip)
        except Exception:
            logger.warning("get_network_info: ignored error", exc_info=True)

        # Method 3: Using getaddrinfo
        try:
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if ip not in network_info["local_ips"] and not ip.startswith("127."):
                    network_info["local_ips"].append(ip)
        except Exception:
            logger.warning("get_network_info: ignored error", exc_info=True)

        # Add localhost as fallback
        network_info["local_ips"].append("127.0.0.1")

        # Generate access URLs
        for ip in network_info["local_ips"]:
            network_info["access_urls"].append(f"http://{ip}:{network_info['port']}")

    except Exception as e:
        network_info["error"] = str(e)

    return network_info


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@manager_required
def settings():
    """System settings and backup management (backups via services.backup — ACC-1.2..1.4)."""

    from src.services import backup as bk

    db = get_db()
    backup_dir = current_app.config.get("BACKUP_FOLDER")
    db_path = current_app.config.get("DATABASE_PATH")

    if request.method == "POST":
        action = request.form.get("action")
        backup_name = request.form.get("backup_name", "")
        if action in ("restore_backup", "delete_backup"):
            # A06 §3.7: restore (and delete) are confirmed with the signed-in manager's
            # password, exactly like reset_database. Checked before close_connection and
            # before bk.restore_backup, so a wrong password creates no safety copy and
            # touches neither the live database nor the backup files.
            from src.services.auth_service import AuthService

            if not AuthService().validate_manager(g.user["username"], request.form.get("password", "")):
                flash("رمز عبور مدیر نادرست است", "error")
                return redirect(url_for("manager.settings"))
        try:
            if action == "create_backup":
                made = bk.create_backup(db_path, backup_dir, bk.KIND_MANUAL)
                log_activity(ActionType.BACKUP_CREATE, ActionCategory.SYSTEM, target_name=made.name)
                flash(f"بکاپ با موفقیت ایجاد شد: {made.name}", "success")
            elif action == "restore_backup":
                # Close this request's connection so the restore is not blocked by it.
                from src.adapters.sqlite.core import close_connection

                close_connection(None)
                safety = bk.restore_backup(db_path, backup_dir, backup_name)
                # An older backup may have an older schema: migrate it now, not at the next restart.
                from src.adapters.sqlite.core import ensure_database_ready

                ensure_database_ready(db_path)
                log_activity(
                    ActionType.BACKUP_RESTORE,
                    ActionCategory.SYSTEM,
                    description=f"بازگردانی دیتابیس از {backup_name} (نسخهٔ قبلی: {safety.name})",
                    target_name=backup_name,
                )
                flash(f"دیتابیس از «{backup_name}» بازگردانی شد. نسخهٔ قبلی در «{safety.name}» ذخیره شد.", "success")
            elif action == "delete_backup":
                bk.delete_backup(backup_dir, backup_name)
                log_activity(ActionType.BACKUP_DELETE, ActionCategory.SYSTEM, target_name=backup_name)
                flash(f"بکاپ حذف شد: {backup_name}", "success")
            elif action == "save_settings":
                clinic_name = request.form.get("clinic_name", "")
                clinic_phone = request.form.get("clinic_phone", "")
                clinic_address = request.form.get("clinic_address", "")
                auto_backup = request.form.get("auto_backup", "0")
                db.execute(
                    "DELETE FROM settings WHERE key IN ('clinic_name', 'clinic_phone', 'clinic_address', 'auto_backup')"
                )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("clinic_name", clinic_name))
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("clinic_phone", clinic_phone))
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("clinic_address", clinic_address))
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("auto_backup", auto_backup))
                db.commit()
                log_activity(ActionType.SETTINGS_CHANGE, ActionCategory.SYSTEM, new_value=f"auto_backup={auto_backup}")
                flash("تنظیمات ذخیره شد", "success")
        except bk.BackupError as e:
            flash(str(e), "error")
        return redirect(url_for("manager.settings"))

    backups = []
    for b in bk.list_backups(backup_dir):
        backups.append(
            {
                "name": b.name,
                "size": round(b.size_bytes / 1024, 1),  # KB
                "date": f"{Gregorian(b.created_at.date()).persian_string()} - {b.created_at.strftime('%H:%M:%S')}",
                "timestamp": b.created_at.timestamp(),
            }
        )

    settings_data = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings").fetchall()}

    db_stats = {
        "patients": db.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        "invoices": db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
        "visits": db.execute("SELECT COUNT(*) FROM visits").fetchone()[0],
        "injections": db.execute("SELECT COUNT(*) FROM injections").fetchone()[0],
        "procedures": db.execute("SELECT COUNT(*) FROM procedures").fetchone()[0],
        "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
    }

    from pathlib import Path

    p = Path(db_path)
    db_size = round(p.stat().st_size / (1024 * 1024), 2) if p.exists() else 0

    return render_template(
        "manager/settings.html",
        backups=backups,
        settings=settings_data,
        db_stats=db_stats,
        db_size=db_size,
        schema_version=db.execute("PRAGMA user_version").fetchone()[0],
        network_info=get_network_info(),
    )


@bp.route("/settings/reset-database", methods=["POST"])
@login_required
@manager_api_required(body={"success": False, "error": "دسترسی غیرمجاز"})
def reset_database():
    """ریست کامل دیتابیس (ACC-1.6، SEC-03).

    شرط‌ها: نقش مدیر، عبارت تأیید، و رمز عبور همین مدیر. پیش از هر چیز یک پشتیبان کامل
    گرفته می‌شود. دیتابیس تازه در فایل موقت ساخته و با backup API جایگزین می‌شود؛ تنها کاربر
    دیتابیس تازه همین مدیر با همان رمز است (دیگر admin/admin ساخته نمی‌شود).
    """
    if request.form.get("confirm_code", "") != "DELETE_ALL_DATA":
        return jsonify({"success": False, "error": "کد تایید نامعتبر است"}), 400

    from src.services.auth_service import AuthService

    if not AuthService().validate_manager(g.user["username"], request.form.get("password", "")):
        return jsonify({"success": False, "error": "رمز عبور مدیر نادرست است"}), 403

    import sqlite3
    from pathlib import Path

    from src.adapters.sqlite import core
    from src.adapters.sqlite.migrations import MigrationError, run_migrations
    from src.services import backup as bk

    keep = {k: g.user[k] for k in ("username", "password_hash", "role", "full_name")}
    db_path = current_app.config.get("DATABASE_PATH")
    backup_dir = current_app.config.get("BACKUP_FOLDER")
    try:
        core.close_connection(None)
        safety = bk.create_backup(db_path, backup_dir, bk.KIND_PRE_RESET)
        import secrets

        fresh = Path(backup_dir) / f".reset_{iran_now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.tmp"
        conn = sqlite3.connect(fresh)
        try:
            core._load_schema_and_initialize(conn)
            run_migrations(conn)
            conn.execute(
                "INSERT INTO users (username, password_hash, role, full_name, is_active) VALUES (?, ?, ?, ?, 1)",
                (keep["username"], keep["password_hash"], keep["role"], keep["full_name"]),
            )
            conn.commit()
            bk.copy_database(bk.ro_uri(fresh), db_path, bk.DEFAULT_LOCK_TIMEOUT)
        finally:
            conn.close()  # before unlink: Windows cannot delete an open file
            fresh.unlink(missing_ok=True)
        bk.verify_database_file(db_path)
    except bk.BackupError as e:
        return jsonify({"success": False, "error": str(e)}), 500
    except (sqlite3.Error, MigrationError, OSError):
        current_app.logger.exception("database reset failed")
        return jsonify(
            {"success": False, "error": "ریست انجام نشد. دیتابیس فعلی دست نخورده است و پشتیبان پیش از ریست موجود است."}
        ), 500

    log_activity(
        ActionType.DATABASE_RESET,
        ActionCategory.SYSTEM,
        description=f"ریست کامل دیتابیس؛ نسخهٔ قبلی در {safety.name}",
        target_name=safety.name,
    )
    session.clear()
    return jsonify(
        {"success": True, "message": f"دیتابیس ریست شد. نسخهٔ قبلی در «{safety.name}» ذخیره شد. لطفاً دوباره وارد شوید."}
    )


@bp.route("/settings/download/<backup_name>")
@login_required
@manager_required
def download_backup(backup_name):
    """دانلود فایل بکاپ — فقط از پوشهٔ بکاپ و فقط با نام معتبر (SEC-02، DATA-03)."""

    from flask import send_file

    from src.services import backup as bk

    try:
        path = bk.safe_backup_path(current_app.config.get("BACKUP_FOLDER"), backup_name)
    except bk.BackupError:
        path = None
    if path is None or not path.exists():
        flash("فایل بکاپ یافت نشد", "error")
        return redirect(url_for("manager.settings"))
    log_activity(ActionType.BACKUP_DOWNLOAD, ActionCategory.SYSTEM, target_name=path.name)
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.route("/settings/upload", methods=["POST"])
@login_required
@manager_required
def upload_backup():
    """آپلود فایل بکاپ — فقط دیتابیس سالم حسابداری پذیرفته می‌شود."""

    from src.services import backup as bk

    file = request.files.get("backup_file")
    if file is None or not file.filename:
        flash("فایلی انتخاب نشده", "error")
        return redirect(url_for("manager.settings"))
    if not file.filename.lower().endswith(".db"):
        flash("فقط فایل‌های .db مجاز هستند", "error")
        return redirect(url_for("manager.settings"))
    try:
        saved = bk.save_uploaded_backup(current_app.config.get("BACKUP_FOLDER"), file, size_hint=request.content_length)
        log_activity(ActionType.BACKUP_UPLOAD, ActionCategory.SYSTEM, target_name=saved.name)
        flash(f"فایل بکاپ آپلود شد: {saved.name}", "success")
    except bk.BackupError as e:
        flash(str(e), "error")
    return redirect(url_for("manager.settings"))
