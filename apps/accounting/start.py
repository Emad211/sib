import sys
import os

# --- FIX: اضافه کردن مسیر فعلی به پایتون تا ماژول src را پیدا کند ---
if getattr(sys, 'frozen', False):
    # اگر فایل EXE بود، مسیر فایل اجرایی را به عنوان ریشه بشناس
    BASE_DIR = os.path.dirname(sys.executable)
    sys.path.insert(0, BASE_DIR)
    # همچنین مسیر _MEIPASS را برای فایل‌های bundled اضافه کن
    if hasattr(sys, '_MEIPASS'):
        sys.path.insert(0, sys._MEIPASS)
else:
    # اگر فایل سورس بود، مسیر جاری را بشناس
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    sys.path.insert(0, BASE_DIR)
# ------------------------------------------------------------------

from src.app import create_app, open_browser
import threading

if __name__ == '__main__':
    # ساخت اپلیکیشن Flask
    app = create_app()
    
    # باز کردن مرورگر با تأخیر کوتاه
    threading.Timer(1.5, open_browser).start()
    
    # پورت 8080 و دسترسی شبکه
    # debug=False و use_reloader=False برای جلوگیری از باز شدن چند پنجره
    app.run(
        debug=False, 
        host='0.0.0.0', 
        port=8080, 
        use_reloader=False,
        threaded=True
    )