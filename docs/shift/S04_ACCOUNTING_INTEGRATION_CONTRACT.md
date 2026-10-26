# قرارداد خواندن اپ شیفت از حسابداری

> نسخه 1.0 · ۱۴۰۵/۰۸/۰۴ · مبنا: ADR-004، A03 بخش ۶

## ۱. اصل

اپ شیفت **فقط** از viewهای `v_shift_*` می‌خواند، با اتصال `mode=ro` و `PRAGMA query_only=1`. این viewها را مهاجرت‌های حسابداری می‌سازند و نسخه‌دارند (`v_shift_*_v1`). تغییر شکست‌آور = view جدید با پسوند `v2`؛ نسخهٔ قبلی تا مهاجرت اپ شیفت باقی می‌ماند.

## ۲. داده‌های لازم

| نیاز سند منطق | منبع در حسابداری | view |
|---|---|---|
| H-03: فاکتور باز هست؟ | `invoices.status='open'` | `v_shift_open_invoices_v1(invoice_id, opened_at, opened_by, shift, work_date)` |
| D: مصرف هر قلم | `consumables_ledger` (بدون `patient_provided=1`) روی فاکتور بسته، به‌علاوهٔ نگاشت تعرفه به کد کالا | `v_shift_consumption_v1(invoice_id, closed_at, acc_code, quantity)` |
| P_cash: دریافت نقدی | `invoice_item_payments` با `payment_type='cash'` و `is_paid=1` روی فاکتور بسته. **این جدول ستون مبلغ ندارد**؛ مبلغ = سهم بیمار همان آیتم، با همان تابع سرویس billing که گزارش‌ها استفاده می‌کنند | `v_shift_cash_receipts_v1(invoice_id, closed_at, amount)` |
| کارت‌به‌کارت: شمارهٔ پذیرش معتبر | `invoices.id` | `v_shift_invoice_lookup_v1(invoice_id, patient_id, status, total, closed_at)` |
| ابطال (اگر ACC-2.12) | وضعیت `voided` | `v_shift_voids_v1(invoice_id, voided_at, reason)` |
| پاداش قند و فشار | `injections` با نوع «تست قند» و «کنترل فشار» | `v_shift_vitals_services_v1(invoice_id, nurse_id, type, closed_at)` |
| پاداش ثبت بیمار | `patients.created_by`، `created_at`، کد ملی، موبایل | `v_shift_patient_registrations_v1(patient_id, created_by, created_at, has_valid_nid, phone)` |
| کمپین تخفیف | **در حسابداری وجود ندارد** | Q-12 |

> **وضعیت ۱۴۰۵/۰۸/۰۴:** ACC-2.11 پیاده شد (مهاجرت ۴). تفاوت‌ها با جدول بالا: `v_shift_consumption_v1` ستون‌های `ledger_id` و `item_name` هم دارد؛ `v_shift_cash_receipts_v1` جمع نقد هر فاکتور است و `v_shift_payment_lines_v1` ردیف‌های کارت/نقد را می‌دهد؛ مبلغ‌ها از جدول `invoice_payment_lines` می‌آیند که **هنگام بستن فاکتور** پر می‌شود (فقط از زمان استقرار؛ فاکتورهای قدیمی ردیف ندارند)؛ viewهای ثبت بیمار فقط اعتبار کد ملی و موبایل را می‌دهند، نه خود شماره را (حریم خصوصی). `v_shift_voids_v1` منتظر Q-08.

## ۳. کارهای لازم در حسابداری (ACC-2.11)

1. **نگاشت کالا:** ستون nullable `acc_code` (یا جدول `consumable_stock_map`) برای ۸۵ تعرفهٔ مصرفی. این نگاشت را مدیر در صفحهٔ تعرفه پر می‌کند.
2. **قاعدهٔ مقدار:** `consumables_ledger.quantity` به واحد شمارش (آمپول، ویال) است یا بسته؟ باید روی داده تأیید شود. اگر ناهمخوان است، ضریب تبدیل در نگاشت ذخیره می‌شود.
3. **اقلامی که مصرفی ثبت نمی‌شوند** (ست سرم، آنژیوکت، سرنگ همراه خدمت پرستاری): یا قاعدهٔ «هر خدمت X مصرف Y دارد» در اپ شیفت تعریف می‌شود، یا پذیرش موظف به ثبت مصرفی می‌شود (Q-07).
4. viewها با تست قرارداد: روی دیتابیس مصنوعی، شکل ستون‌ها و چند جواب دستی‌محاسبه‌شده.

## ۴. کارایی

- همهٔ viewها روی `invoices.closed_at` و `invoice_id` فیلتر می‌شوند. index لازم روی `invoices(closed_at)` با مهاجرت اضافه می‌شود.
- خواندن دوره‌ای فقط ردیف‌های بعد از watermark را می‌خواند. تجربهٔ پنل پیگیری با همین الگو: p99 حدود ۲٫۵ میلی‌ثانیه.

## تاریخچه

- 1.0، ۱۴۰۵/۰۸/۰۴: نسخهٔ اول.
