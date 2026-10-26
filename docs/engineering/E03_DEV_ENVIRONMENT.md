# محیط توسعه

> نسخه 1.0 · ۱۴۰۵/۰۸/۰۴ · این سند پس از ACC-0.1 و ACC-0.2 با دستورهای نهایی به‌روز می‌شود.

## ۱. پیش‌نیازها

- ویندوز ۱۰ یا ۱۱، پایتون 3.13 (`py -3.13`)، گیت، و Node 20+ (فقط برای `clinic-landing` و بعداً PWA اپ شیفت).

## ۲. وضعیت امروز (پیش از ACC-0)

کد حسابداری هنوز در `Desktop/projects/hesabdari_darmangah/webapp` است. اجرای تست‌ها بدون تغییر در آن ریپو:

```bash
cd "C:/Users/Emad Karimi/Desktop/sib"
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install flask werkzeug bcrypt jdatetime pytest
cd "C:/Users/Emad Karimi/Desktop/projects/hesabdari_darmangah/webapp"
PYTHONDONTWRITEBYTECODE=1 ../../../sib/.venv/Scripts/python -m pytest -q -p no:cacheprovider tests
```

نتیجه در ۱۴۰۵/۰۸/۰۴: **54 passed**. نسخه‌های نصب‌شده: Flask 3.1.3، Werkzeug 3.1.9، bcrypt 5.0.0، jdatetime 6.1.1، pytest 9.1.1. این‌ها مبنای اولیهٔ `requirements.lock` هستند و باید با نسخه‌های exe پروداکشن مقایسه شوند (Q-03).

## ۳. پس از ACC-0 (هدف)

```bash
cd apps/accounting
./dev.ps1 setup      # venv + requirements.lock
./dev.ps1 test       # همهٔ تست‌های CI
./dev.ps1 test-prod  # تست‌های prod_snapshot روی _data (فقط محلی)
./dev.ps1 golden     # گولدن مستر
./dev.ps1 run        # اجرای محلی روی دیتابیس مصنوعی، پورت 8080
./dev.ps1 build      # ساخت exe
```

## ۴. داده

- کپی پروداکشن: `sib/_data/prod_snapshot/clinic_new_2026-09-24.db`، که از `Desktop/projects/db-darmangah/clinic_new.db` کپی شده است. نسخه‌های دیگر از همان فایل در `Desktop/clinic_new.db` و `Downloads/clinic_new.db` هم هست؛ پیشنهاد: پس از تأیید مالک، این دو کپی اضافه پاک شوند (E02 بند ۵).
- هرگز برنامه را مستقیم روی کپی پروداکشن اجرا نکنید، چون اجرای برنامه مهاجرت‌ها را می‌نویسد. اول یک کپی موقت بسازید.

## تاریخچه

- 1.0، ۱۴۰۵/۰۸/۰۴: نسخهٔ اول.
