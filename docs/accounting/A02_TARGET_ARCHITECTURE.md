# معماری مقصد حسابداری

> نسخه 1.0 · ۱۴۰۵/۰۸/۰۴ · مبنا: ADR-002، ADR-003

## ۱. اصل

همان فناوری می‌ماند (Flask + SQLite + exe). فقط **جای** کد عوض می‌شود. هر جابه‌جایی پشت تست انجام می‌شود و رفتار بیرونی (HTML، اعداد، URLها، دیتابیس) ثابت می‌ماند، مگر رفع باگی که در A01 شناسه دارد.

## ۲. لایه‌ها

```text
┌────────────────────────────────────────────────────────────┐
│ web/  (Flask blueprints)                                   │
│   فقط: خواندن ورودی ← اعتبارسنجی ← صدا زدن سرویس ← رندر  │
│   هیچ SQL، هیچ محاسبهٔ مالی                                 │
├────────────────────────────────────────────────────────────┤
│ services/  (منطق کار)                                       │
│   billing, invoices, reports, payroll, backup, users, ...  │
│   تابع‌های خالص تا جای ممکن؛ ورودی/خروجی dataclass          │
├────────────────────────────────────────────────────────────┤
│ domain/  (قواعد بدون وابستگی)                               │
│   پول، سهم بیمه، شیفت و work_date، تاریخ جلالی، کد ملی     │
├────────────────────────────────────────────────────────────┤
│ adapters/sqlite/  (فقط SQL)                                │
│   repositoryها، اتصال، migrations/                          │
└────────────────────────────────────────────────────────────┘
```

**امروز (واقعیت کد):** blueprintها در `src/api/` هستند (شامل بستهٔ `api/manager/`) و SQL هم دارند؛ `src/web/` فقط `csrf.py` دارد و `web/authz.py` و `@require_role` هنوز نیست (ACC-2.1). این نمودار مقصد است، نه وضع فعلی؛ جابه‌جایی طبق بخش ۵.

**قاعدهٔ وابستگی:** هر لایه فقط لایهٔ زیرش را می‌شناسد. یک تست معماری (با `import-linter` یا تست سادهٔ پایتون) این قاعده را در CI اجبار می‌کند.

## ۳. ساختار پوشه‌ها

ساختار امروز کد:

```text
apps/accounting/
├── start.py                     ← نقطهٔ ورود exe
├── HesabdariSib.spec
├── requirements.lock
├── src/
│   ├── app.py                   ← create_app، ثبت blueprintها، فیلترهای Jinja، CLI، scheduler
│   ├── config/settings.py       ← مسیرها، نام فایل کلید نشست، حالت production
│   ├── api/                     ← routeها (امروز اینجا هستند و SQL درون‌خطی دارند)
│   │   ├── auth.py              ← /auth و login_required
│   │   ├── dashboard.py         ← /
│   │   ├── reception.py         ← /reception و /reception/api/*
│   │   └── manager/             ← بستهٔ /manager: dashboard, reports, exports, arrears, tariffs, users, payroll, logs, settings, expenses, _common
│   ├── web/csrf.py              ← CSRF؛ authz.py و @require_role هنوز نیست (ACC-2.1)
│   ├── services/                ← auth_service, reception_service, backup, scheduler, exports, activity_logger, manager_finance, manager_insights, shift_handover, staff_performance, tariff_audit, patient_matching (manager_service.py هنوز خالی است)
│   ├── domain/                  ← money.py, billing.py, shifts.py, jalali.py, identity.py (کد ملی همان‌جاست), patients.py, visits.py, user.py (reports.py هنوز خالی است)
│   ├── common/                  ← jalali.py, utils.py (iran_now و کارهای work_date)، validators.py
│   ├── adapters/sqlite/         ← core.py (اتصال و schema)، یک *_repo.py برای هر aggregate، schema.sql
│   │   └── migrations/          ← m0001_baseline.py ... m0007_shift_contract_voids.py (A03)
│   ├── templates/  static/      ← قالب‌ها به تفکیک نقش (auth، reception، manager، errors)
└── tests/                       ← A04
```

## ۴. الگوهای ثابت

- **پول:** عدد صحیح، به همان واحد فعلی دیتابیس (واحد ریال یا تومان در ACC-0.6 تأیید و اینجا ثبت می‌شود). هیچ float در محاسبات مالی. تابع‌های گردکردن یک‌جا.
- **زمان:** فقط `domain/shifts.py` تصمیم می‌گیرد هر لحظه مال کدام `work_date` و کدام شیفت است. `+3:30` فقط در `config`.
- **خطا:** هیچ `except` بی‌لاگ. خطای غیرمنتظره در مسیر وب ← صفحهٔ خطای فارسی + لاگ در فایل چرخشی کنار exe.
- **دسترسی:** هر مسیر یک `@require_role(...)` صریح دارد. تستی شکست می‌خورد اگر مسیری بدون آن ثبت شود.
- **گزارش‌ها:** هر گزارش یک تعریف (فیلترها، ستون‌ها، کوئری) است. HTML و CSV و Excel از همان داده ساخته می‌شوند.
- **لاگ فعالیت:** API فعلی `log_activity` حفظ می‌شود. ثبت آن در همان تراکنش عملیات اصلی انجام می‌شود.

## ۵. روش رسیدن (strangler)

برای هر مسیر به ترتیب A05:

1. تست مشخصه‌نگاری مسیر: وضعیت HTTP، اعداد کلیدی صفحه، تغییرات دیتابیس.
2. انتقال SQL به repository و منطق به service. مسیر قدیمی فقط آن‌ها را صدا می‌زند.
3. تست‌ها سبز ← انتقال مسیر به `web/…` با همان URL.
4. حذف کد مرده. اجرای گولدن مستر روی کپی پروداکشن، بدون هیچ اختلاف.

یک PR = یک مسیر یا یک گروه کوچک مرتبط. هرگز «همه با هم».

## ۶. آنچه عمداً تغییر نمی‌کند (فاز ۲)

- URLها، نام فیلدهای فرم و ظاهر صفحه‌های reception. کاربران پذیرش نباید چیز تازه‌ای یاد بگیرند.
- schema موجود (ADR-002).
- شکل اجرا: exe، پورت 8080، دسترسی شبکه.

## تاریخچه

- 1.0، ۱۴۰۵/۰۸/۰۴: نسخهٔ اول.
