# محیط توسعه

> نسخه 1.2 · ۱۴۰۵/۰۸/۱۰

## ۱. پیش‌نیازها

- ویندوز ۱۰ یا ۱۱، پایتون 3.13 (`py -3.13`)، گیت، و Node 20+ (فقط برای `clinic-landing` و بعداً PWA اپ شیفت).

## ۲. راه‌اندازی (از ACC-0.2)

```powershell
cd apps/accounting
./dev.ps1 setup      # ساخت ../../.venv با پایتون 3.13 و نصب requirements-dev.lock
./dev.ps1 test       # همان چیزی که CI اجرا می‌کند: ruff + pytest روی دادهٔ مصنوعی
./dev.ps1 test-prod  # دروازهٔ کپی پروداکشن (فقط محلی، حدود ۶ دقیقه)
./dev.ps1 golden     # فقط مقایسهٔ گولدن مستر (حدود ۳ دقیقه)
./dev.ps1 synth      # دیتابیس مصنوعی در _data/synthetic.db
./dev.ps1 run        # اجرای برنامه روی دیتابیس مصنوعی، http://127.0.0.1:8080
./dev.ps1 build      # ساخت dist/HesabdariSib.exe
```

- ورود روی دیتابیس مصنوعی: `manager1` (نقش `manager`) و `reception1`/`reception2` (نقش `reception`) با رمز `synthetic-pass` (فقط دیتابیس مصنوعی). `reception_old` غیرفعال است و `doctor1` فقط برای اثبات رد شدن نقش بازنشستهٔ `doctor` ساخته می‌شود، پس هیچ‌کدام نمی‌توانند وارد شوند (ADR-013؛ `USER_ROLES` در `apps/accounting/src/services/auth_service.py` و `apps/accounting/tools/make_synthetic_db.py`).
- `requirements.lock` دقیقاً نسخه‌های داخل exe ساخته‌شده از `origin/main` ریپوی قدیمی را دارد: Flask 3.1.2، Werkzeug 3.1.3، Jinja2 3.1.6، bcrypt 5.0.0 و jdatetime 5.2.0.
- تست‌های `prod_snapshot` به‌طور پیش‌فرض اجرا نمی‌شوند (`pytest.ini`) و در CI هرگز اجرا نمی‌شوند.
- ثبت دوبارهٔ ماتریس مسیرها پس از تغییر آگاهانهٔ دسترسی: `UPDATE_ROUTE_MATRIX=1 pytest tests/test_route_matrix.py`
- ثبت دوبارهٔ گولدن مستر (فقط وقتی اختلاف دقیقاً رفع یک باگ ثبت‌شده است): `python tools/golden_master.py record --db <snapshot>`، سپس commit فایل هش در `tests/golden/`.

## ۳. ریپو و CI

- ریپو: `github.com/Emad211/sib` (خصوصی). CI: `.github/workflows/accounting.yml` روی ویندوز، شامل گارد `.db`، ruff، pytest با پوشش، و روی `main` ساخت exe با smoke.
- `gh` روی این دستگاه: متغیر محیطی `GITHUB_TOKEN` نامعتبر است. دستورها را با `env -u GITHUB_TOKEN gh ...` اجرا کنید، یا آن متغیر را از تنظیمات ویندوز پاک کنید.

## ۴. داده

- کپی پروداکشن: `sib/_data/prod_snapshot/clinic_new_2026-09-24.db`، که از `Desktop/projects/db-darmangah/clinic_new.db` کپی شده است. نسخه‌های دیگر از همان فایل در `Desktop/clinic_new.db` و `Downloads/clinic_new.db` هم هست؛ پیشنهاد: پس از تأیید مالک، این دو کپی اضافه پاک شوند (E02 بند ۵).
- هرگز برنامه را مستقیم روی کپی پروداکشن اجرا نکنید، چون اجرای برنامه مهاجرت‌ها را می‌نویسد. اول یک کپی موقت بسازید.

## تاریخچه

- 1.0، ۱۴۰۵/۰۸/۰۴: نسخهٔ اول.
- 1.1، ۱۴۰۵/۰۸/۰۴: دستورهای واقعی پس از فاز ۰.
- 1.2، ۱۴۰۵/۰۸/۱۰: تصحیح کاربرهای نمونهٔ دیتابیس مصنوعی؛ نقش ورود `doctor` بازنشسته شده است (ADR-013) و `doctor1` فقط برای تست رد شدن آن می‌ماند.
