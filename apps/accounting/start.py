import os
import sys

# --- FIX: اضافه کردن مسیر فعلی به پایتون تا ماژول src را پیدا کند ---
if getattr(sys, "frozen", False):
    # اگر فایل EXE بود، مسیر فایل اجرایی را به عنوان ریشه بشناس
    BASE_DIR = os.path.dirname(sys.executable)
    sys.path.insert(0, BASE_DIR)
    # همچنین مسیر _MEIPASS را برای فایل‌های bundled اضافه کن
    if hasattr(sys, "_MEIPASS"):
        sys.path.insert(0, sys._MEIPASS)
else:
    # اگر فایل سورس بود، مسیر جاری را بشناس
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    sys.path.insert(0, BASE_DIR)
# ------------------------------------------------------------------

import logging
import threading

from src.app import create_app, open_browser

APP_TITLE = "حسابداری درمانگاه سیب"


def show_fatal(message: str) -> None:
    """The exe has no console: show fatal startup errors in a Windows dialog (ACC-1.8)."""
    logging.getLogger("hesabdari.start").critical(message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, APP_TITLE, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001 — not on Windows: stderr is all we have
        print(message, file=sys.stderr)


def prepare_database() -> bool:
    """Create/migrate the database before serving. False = do not start (never run half-migrated)."""
    from src.adapters.sqlite.core import ensure_database_ready
    from src.config.logging_setup import configure_logging
    from src.config.settings import Config

    configure_logging(os.path.join(os.path.dirname(os.path.abspath(Config.DATABASE_PATH)), "logs"))
    try:
        report = ensure_database_ready()
    except Exception as e:  # noqa: BLE001 — anything here means the data is not safe to use
        show_fatal(
            "برنامه اجرا نشد چون آماده‌سازی دیتابیس با خطا روبه‌رو شد. هیچ داده‌ای تغییر نکرده است.\n\n"
            f"{e}\n\nلطفاً با پشتیبانی تماس بگیرید و پوشهٔ logs را بفرستید."
        )
        return False
    if report.newer_than_code:
        logging.getLogger("hesabdari.start").warning("database is newer than this program version")
    return True


def main() -> int:
    if not prepare_database():
        return 1
    app = create_app()
    threading.Timer(1.5, open_browser).start()
    # use_reloader=False: a single process (a reloader would open the browser twice)
    app.run(
        debug=False,
        host=("127.0.0.1" if app.config.get("PRODUCTION") else "0.0.0.0"),
        port=8080,
        use_reloader=False,
        threaded=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
