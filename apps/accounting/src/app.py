import os
import sys
import threading
import webbrowser

from flask import Flask
from datetime import datetime, timedelta

from src.config.settings import Config
from src.adapters.sqlite.core import close_connection


def create_app(test_config=None):
    """
    اپلیکیشن Flask را می‌سازد.
    برای حالت سورس و exe (PyInstaller) کار می‌کند.
    """

    # --------- تعیین مسیر templates برای حالت exe و سورس ---------
    if getattr(sys, "frozen", False):
        # وقتی exe هستیم: فایل‌ها در پوشه موقت _MEIPASS اکسترکت می‌شوند
        base_dir = sys._MEIPASS
        template_folder = os.path.join(base_dir, "src", "templates")
        static_folder = os.path.join(base_dir, "src", "static")
    else:
        # وقتی از روی سورس اجرا می‌شود
        base_dir = os.path.abspath(os.path.dirname(__file__))
        template_folder = os.path.join(base_dir, "templates")
        static_folder = os.path.join(base_dir, "static")

    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)

    # --------- تنظیمات کانفیگ ---------
    if test_config is None:
        app.config.from_object(Config)
    else:
        app.config.from_mapping(test_config)
    
        # Diagnostic: log which database file is used and whether essential tables exist.
        try:
            from src.config.settings import Config as _Config
            import sqlite3
            db_path = _Config.DATABASE_PATH
            print(f"[startup] Using database: {db_path}")
            try:
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('payroll_settings','activity_logs')")
                found = [r[0] for r in cur.fetchall()]
                print(f"[startup] Found tables: {found}")
                conn.close()
            except Exception as e:
                print(f"[startup] Error checking DB tables: {e}")
        except Exception:
            pass

    # Respect environment for production vs development.
    # Use FLASK_ENV or APP_ENV to choose 'production' mode; otherwise fall back to config.
    if not app.config.get('TESTING', False):
        env_name = os.environ.get('FLASK_ENV') or os.environ.get('APP_ENV') or app.config.get('ENV', 'development')
        if str(env_name).lower() == 'production':
            app.config['ENV'] = 'production'
            app.config['DEBUG'] = False
            app.jinja_env.auto_reload = False
        else:
            # Development defaults: enable debug if config requests it
            app.config['ENV'] = 'development'
            app.config['DEBUG'] = bool(app.config.get('DEBUG', False))
            app.jinja_env.auto_reload = bool(app.config.get('DEBUG', False))

    # --------- Teardown دیتابیس ---------
    app.teardown_appcontext(close_connection)

    # --------- دستورات CLI (برای خودت روی سرور) ---------
    import click
    from src.adapters.sqlite.core import init_db_command
    from src.services.auth_service import AuthService

    @app.cli.command("init-db")
    def init_db():
        init_db_command()

    @app.cli.command("create-user")
    @click.argument("username")
    @click.argument("password")
    @click.argument("role", default="reception")
    def create_user(username, password, role):
        service = AuthService()
        if service.register_user(username, password, role):
            print(f"User {username} created successfully.")
        else:
            print(f"User {username} already exists or error occurred.")

    # --------- لود کاربر لاگین‌شده ---------
    from flask import session, g
    from src.adapters.sqlite.core import get_db

    @app.before_request
    def load_logged_in_user():
        user_id = session.get("user_id")

        # Initialize shift status with defaults to avoid AttributeErrors
        g.user_shift_status = {
            'active_shift': None,
            'work_date': None,
            'is_overdue': False,
            'should_prompt': False,
            'open_invoices_count': 0
        }

        if user_id is None:
            g.user = None
        else:
            db = get_db()
            g.user = db.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()

    # --------- ثبت Blueprints ---------
    from src.api.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from src.api.dashboard import bp as dashboard_bp
    app.register_blueprint(dashboard_bp)

    from src.api.reception import bp as reception_bp
    app.register_blueprint(reception_bp)

    from src.api.manager import bp as manager_bp
    app.register_blueprint(manager_bp)

    @app.route("/")
    def index():
        from flask import redirect, url_for, g

        if g.user is None:
            return redirect(url_for("auth.login"))
        return redirect(url_for("dashboard.index"))

    # --------- فیلترهای جلالی ---------
    @app.template_filter("jalali_datetime")
    def jalali_datetime_filter(value):
        if not value:
            return ""
        from src.common.utils import format_jalali_datetime
        return format_jalali_datetime(value)

    @app.template_filter("jalali_local")
    def jalali_local_filter(value):
        """Convert datetime to Jalali display."""
        if not value:
            return ""
        from src.common.utils import format_jalali_datetime
        return format_jalali_datetime(value)

    # --------- فیلتر نمایش اعداد فارسی ---------
    @app.template_filter('fa_num')
    def fa_number_filter(value):
        """Format number with thousands separator and Persian digits."""
        if value is None:
            return ''
        try:
            # Ensure numeric
            num = float(value)
        except Exception:
            # If not numeric, just return string (but convert digits if present)
            s = str(value)
            trans = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
            return s.translate(trans)

        # Format with grouping, without decimal when integer
        if float(num).is_integer():
            s = f"{int(num):,}"
        else:
            # Keep two decimal places
            s = f"{num:,.2f}"
        # Replace ASCII digits with Persian digits
        trans = str.maketrans('0123456789,', '۰۱۲۳۴۵۶۷۸۹،')
        return s.translate(trans)

    # --------- زمان‌بند بکاپ ---------
    if not app.config.get("TESTING", False):
        from src.services.scheduler import init_scheduler
        init_scheduler(app)

    return app


def open_browser():
    """برای خود سرور، مرورگر را روی آدرس لوکال باز می‌کند."""
    url = "http://127.0.0.1:8080/"
    try:
        webbrowser.open(url)
    except Exception:
        pass


# Expose a WSGI application callable for production servers (Gunicorn, uWSGI, etc.)
# The server can set FLASK_ENV=production or APP_ENV=production to force production mode.
# Note: این خط فقط برای WSGI سرورها استفاده می‌شود - برای PyInstaller نباید اینجا create_app صدا زده شود
def get_wsgi_app():
    """Get WSGI app for production servers like Gunicorn."""
    return create_app()


# برای سازگاری با WSGI سرورها
app = None


def _ensure_app():
    global app
    if app is None:
        app = create_app()
    return app


if __name__ == "__main__":
    # Only run the built-in server for local development or PyInstaller.
    application = create_app()
    
    # In PyInstaller builds, never enable reloader/debug (prevents double-run -> double tab)
    is_frozen = bool(getattr(sys, 'frozen', False))

    # Open browser after a short delay (once)
    threading.Timer(1.5, open_browser).start()

    port = int(os.environ.get('PORT', 8080))
    application.run(
        debug=False,
        host="0.0.0.0",
        port=port,
        use_reloader=False,
    )