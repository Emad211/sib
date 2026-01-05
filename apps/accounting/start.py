import sys
import os

# --- FIX: اضافه کردن مسیر فعلی به پایتون تا ماژول src را پیدا کند ---
if getattr(sys, 'frozen', False):
    # اگر فایل EXE بود، مسیر فایل اجرایی را به عنوان ریشه بشناس
    sys.path.append(os.path.dirname(sys.executable))
else:
    # اگر فایل سورس بود، مسیر جاری را بشناس
    sys.path.append(os.path.abspath("."))
# ------------------------------------------------------------------

from src.app import create_app, open_browser
import threading

if __name__ == '__main__':
    app = create_app()
    
    threading.Timer(1.5, open_browser).start()
    
    # پورت 8080 و دسترسی شبکه
    app.run(debug=False, host='0.0.0.0', port=8080, use_reloader=False)