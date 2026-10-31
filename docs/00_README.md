# اسناد پروژهٔ درمانگاه شبانه‌روزی سیب

> نسخه 1.0 · ۱۴۰۵/۰۸/۰۴ (2026-10-26) · مالک: دکتر کریمی (مدیر و پزشک درمانگاه)
> این پوشه **نقشهٔ راه رسمی** است. هر کاری که در این اسناد نیست، اول اینجا اضافه می‌شود و بعد انجام می‌شود.

## ترتیب خواندن

| # | سند | برای چه کسی | خلاصه |
|---|---|---|---|
| 1 | [01_PROJECT_CHARTER](01_PROJECT_CHARTER.md) | همه | چرا، چه چیزی، معیار موفقیت |
| 2 | [02_ROADMAP](02_ROADMAP.md) | همه | فازها، ترتیب کار، دروازه‌های عبور |
| 3 | [03_DECISIONS](03_DECISIONS.md) | فنی + مالک | همهٔ تصمیم‌های معماری (ADR) |
| 4 | [04_GLOSSARY](04_GLOSSARY.md) | همه | واژه‌نامه |
| 5 | [05_OPEN_QUESTIONS](05_OPEN_QUESTIONS.md) | مالک | سؤال‌هایی که جوابشان کار را عوض می‌کند |
| 6 | [06_RISK_REGISTER](06_RISK_REGISTER.md) | مالک + فنی | ریسک‌ها و راه کنترل |

### حسابداری (فاز ۱ و ۲)

| سند | خلاصه |
|---|---|
| [A01_CURRENT_STATE_AUDIT](accounting/A01_CURRENT_STATE_AUDIT.md) | وضعیت فعلی کد و داده، همهٔ یافته‌ها با شناسه |
| [A02_TARGET_ARCHITECTURE](accounting/A02_TARGET_ARCHITECTURE.md) | ساختار مقصد کد و روش رسیدن به آن |
| [A03_DATABASE_COMPATIBILITY_AND_MIGRATIONS](accounting/A03_DATABASE_COMPATIBILITY_AND_MIGRATIONS.md) | قانون سازگاری با دیتابیس پروداکشن |
| [A04_TEST_STRATEGY](accounting/A04_TEST_STRATEGY.md) | لایه‌های تست و دروازه‌های رگرسیون |
| [A05_WORK_PLAN](accounting/A05_WORK_PLAN.md) | بک‌لاگ مرحله‌به‌مرحله با معیار «انجام شد» |
| [A06_MANAGER_AREA_SPEC](accounting/A06_MANAGER_AREA_SPEC.md) | بازطراحی بخش مدیر |
| [A07_RELEASE_AND_DEPLOYMENT](accounting/A07_RELEASE_AND_DEPLOYMENT.md) | ساخت exe، انتشار، بازگشت |
| [A08_CLIENT_FEEDBACK_R1](accounting/A08_CLIENT_FEEDBACK_R1.md) | بازخورد مالک (دور ۱) و وضعیت هر مورد |

### اپ شیفت (فاز ۳)

| سند | خلاصه |
|---|---|
| [S01_PRODUCT_LOGIC](shift/S01_PRODUCT_LOGIC.md) | ارجاع به سند منطق نهایی و قواعد استخراج‌شده |
| [S02_ARCHITECTURE](shift/S02_ARCHITECTURE.md) | معماری اپ شیفت |
| [S03_DOMAIN_MODEL](shift/S03_DOMAIN_MODEL.md) | موجودیت‌ها، رویدادها، ماشین‌های حالت |
| [S04_ACCOUNTING_INTEGRATION_CONTRACT](shift/S04_ACCOUNTING_INTEGRATION_CONTRACT.md) | قرارداد خواندن از حسابداری |
| [S05_DELIVERY_PLAN](shift/S05_DELIVERY_PLAN.md) | مراحل ساخت و پایلوت |

### مهندسی و عملیات

| سند | خلاصه |
|---|---|
| [E01_ENGINEERING_STANDARDS](engineering/E01_ENGINEERING_STANDARDS.md) | کدنویسی، گیت، بازبینی، تعریف «انجام شد» |
| [E02_SECURITY_AND_PRIVACY](engineering/E02_SECURITY_AND_PRIVACY.md) | دادهٔ بیمار، تهدیدها، قواعد |
| [E03_DEV_ENVIRONMENT](engineering/E03_DEV_ENVIRONMENT.md) | راه‌اندازی محیط و دستورها |
| [E04_PROJECT_MAP](engineering/E04_PROJECT_MAP.md) | نقشهٔ کد و اسناد: ساختار پوشه‌ها، لایه‌ها، دستورهای تست و لینت |
| [O01_BACKUP_AND_RECOVERY](ops/O01_BACKUP_AND_RECOVERY.md) | پشتیبان‌گیری و بازیابی |
| [O02_INCIDENT_RUNBOOK](ops/O02_INCIDENT_RUNBOOK.md) | وقتی چیزی خراب شد |
| [O03_STAFF_COMMUNICATION_AND_TRAINING](ops/O03_STAFF_COMMUNICATION_AND_TRAINING.md) | اطلاع‌رسانی و آموزش کارکنان |
| [O04_SHIFT_RUNBOOK](ops/O04_SHIFT_RUNBOOK.md) | راه‌اندازی و بهره‌برداری اپ شیفت روی رایانهٔ درمانگاه |

## قواعد نگهداری اسناد

- هر سند در سرآیندش نسخه و تاریخ دارد. تغییر معنادار = نسخهٔ جدید + یک خط در «تاریخچه» پایین همان سند.
- شناسه‌ها پایدارند: یافته‌ها (`SEC-01`، `BUG-03` …)، تصمیم‌ها (`ADR-007`)، کارها (`ACC-1.3`). شناسه هرگز دوباره استفاده نمی‌شود.
- هر PR که رفتار را تغییر می‌دهد، شناسهٔ کار نقشهٔ راه را در عنوانش دارد و اسناد مرتبط را همان‌جا به‌روز می‌کند.
- منبع حقیقت منطق اپ شیفت سند مالک است: [`../اپ شیفت درمانگاه سیب — نسخه نهایی منطق.md`](../اپ%20شیفت%20درمانگاه%20سیب%20—%20نسخه%20نهایی%20منطق.md). اسناد `shift/` آن را تفسیر می‌کنند و جایگزینش نیستند.

## ساختار پوشهٔ سیب

```text
sib/
├── CLAUDE.md                ← قواعد سخت کار
├── docs/                    ← همین اسناد (نقشهٔ راه)
├── clinic-landing/          ← سایت معرفی (روی Vercel)
├── _data/                   ← کپی‌های دیتابیس واقعی — بیرون از گیت، هرگز commit نشود
├── apps/accounting/         ← حسابداری، منتقل‌شده از ریپوی قدیمی
└── apps/shift/              ← اپ شیفت
```

این ساختار همان چیزی است که در ADR-001 پذیرفته شد و ساخته شده است؛ `_data/` بیرون از گیت می‌ماند و هرگز commit نمی‌شود.
