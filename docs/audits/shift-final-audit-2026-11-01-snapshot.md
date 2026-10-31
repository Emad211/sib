# ممیزی نهایی اپلیکیشن شیفت درمانگاه سیب (Final Audit — Shift App)

- **شاخه:** `feat/shift-rc`
- **مخزن و درخت کار:** `C:/Users/Emad Karimi/orca/workspaces/sib/shift-rc`
- **مرجع حقیقت:** سند مالک «اپ شیفت درمانگاه سیب — نسخه نهایی منطق.md» + تصمیم‌های مصوب مالک (۲۰۲۶-۱۰-۳۱) + ADR-017 تا ADR-024 در `docs/03_DECISIONS.md`.
- **خط‌مشی ممیزی:** فقط‌خواندنی، بدون باز کردن پوشه `_data/`، بررسی از روی کد و شواهد اجرایی (نه نام تست‌ها).
- **موارد مستثنای در حال ادغام (طبق دستور کار، شکاف تلقی نشدند):** پاداش تک‌موبایل و نمونهٔ تماس، تصمیم ۹ (شمارش اولیه توسط هر شخص در جریان خود)، و اصلاحات انبار در `final-store-critic.md`.

---

## ۱. شکاف‌های باقی‌مانده (به ترتیب آسیب به یک روز واقعی شیفت)

1. **عدم نمایش تصویر فاکتور در فرم دیجیتال‌سازی حسابدار (F1 - UI):**
   - **آسیب:** هنگام تحویل بار در ورودی، عکس فاکتور اجباری است و هش آن به درستی ذخیره می‌شود؛ اما در فرم ثبت خطوط فاکتور توسط حسابدار (`apps/shift/src/shift/static/purchase.js:971-980`) تصویر فاکتور نمایش داده نمی‌شود. حسابدار باید فاکتور کاغذی را ببیند یا فایل را به صورت دستی پیدا کند (سند منطق: سطر ۱۶۸ و ۱۹۵).
   - **فایل‌های مرتبط:** `apps/shift/src/shift/static/purchase.js`, `apps/shift/src/shift/templates/purchase.html`.

2. **نبود مسیر بازگشت مصرفی از قفسه به انبار اصلی (متغیر U در فرمول صندوق/قفسه):**
   - **آسیب:** فرمول تئوریک قفسه `E = C0 + R - D - U - W` شامل متغیر بازگشت از قفسه به انبار (U) است. در حال حاضر فقط مسیر انتقال از انبار به قفسه (`purchase.py:449`) وجود دارد و رویداد یا امکانی برای بازگرداندن اقلام مازاد از قفسه به انبار اصلی تعبیه نشده است (سند منطق: سطر ۶۸-۷۲).
   - **فایل‌های مرتبط:** `apps/shift/src/shift/domain/purchase_specs.py`, `apps/shift/src/shift/services/purchase.py`.

3. **عدم ثبت شماره پلمب پاکت جدید در برداشت اضطراری از انبار:**
   - **آسیب:** رویداد برداشت اضطراری دونفره با ثبت دلیل، شاهد دوم و تأیید انباردار پیاده‌سازی شده است، اما فیلدی برای ثبت شماره سریال پلمب جدید پاکت کلید وجود ندارد (سند منطق: سطر ۱۵۱).
   - **فایل‌های مرتبط:** `apps/shift/src/shift/domain/shift_events/emergency_withdrawal.py`.

4. **عدم تفکیک و برجسته‌سازی ارجاعات تعمیراتی مهلت‌گذشته روی کارت مدیر:**
   - **آسیب:** اعلام خرابی و ارجاع آن با تعیین مهلت انجام می‌شود، اما کارت مدیر ارجاعاتی را که تاریخ مهلت آن‌ها سپری شده به عنوان هشدار اضطراری جداگانه نشان نمی‌دهد و در میان سایر رویدادهای باز باقی می‌مانند (سند منطق: سطر ۱۵۲، ۲۴۶).
   - **فایل‌های مرتبط:** `apps/shift/src/shift/services/daily_card.py`.

5. **شمارنده‌های تجمیعی رویدادهای ۲۴ ساعت گذشته روی کارت مدیر:**
   - **آسیب:** کارت روزانه فهرست ردیف‌های باز را می‌آورد، اما تجمیع تعداد وقوع هر کد رویداد در ۲۴ ساعت (مثلاً ۵ کارت‌به‌کارت، ۲ ضایعات) را ارائه نمی‌دهد (سند منطق: سطر ۲۴۵).
   - **فایل‌های مرتبط:** `apps/shift/src/shift/services/daily_card.py`.

6. **نمای تاریخچه قیمت هر قلم برای حسابدار (F2):**
   - **آسیب:** این ویژگی در شاخه در حال توسعه پاداش پیاده شده است اما در شاخه فعلی `feat/shift-rc` هنوز صفحه اختصاصی `/accountant/prices` ادغام نهایی نشده است (سند منطق: سطر ۱۹۷).
   - **فایل‌های مرتبط:** `apps/shift/src/shift/api/routes_accountant.py`.

---

## ۲. جدول ممیزی سطر به سطر سند منطق مالک و جدول ۳۱ تصمیم

### بخش ۲.۱: اصول و نقش‌ها (DOC 14-54)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 16 | هر صفحه یک کار، متن کوتاه، عدد بزرگ | partial | `apps/shift/src/shift/static/handover.js:1-15`, `apps/shift/src/shift/templates/purchase.html` | `tests/e2e/test_handover_flow.py` |
| 16 | آیکون اختصاصی برای هر قلم | done | `apps/shift/src/shift/domain/item_icons.py:27`, `apps/shift/src/shift/static/item-icons.js:25` | `tests/unit/test_pwa_styles.py` |
| 17 | عدم نمایش داده‌های شیفت‌های گذشته | done | `apps/shift/src/shift/api/routes_events.py:137-152` | `tests/security/test_reference_number_leak.py` |
| 17 | عدم نمایش عدد مرجع به پرسنل | done | `apps/shift/src/shift/services/handover.py:364`, `apps/shift/src/shift/static/handover.js:34-41` | `tests/security/test_reference_number_leak.py` |
| 18 | ردپذیری و اصلاح = ثبت رویداد تازه | done | `apps/shift/src/shift/adapters/db/sqlite/triggers.sql` | ADR-010 |
| 19 | مدیر فقط تأیید/رد/تغییر کند | partial | `apps/shift/src/shift/templates/manager_card.html`, `apps/shift/src/shift/static/manager.js:55-74` | `tests/integration/test_daily_card.py` |
| 20 | پایگاه‌داده حسابداری فقط خواندنی است | done | `apps/shift/src/shift/adapters/accounting/readonly.py` | `tests/integration/test_accounting_bridge.py` |
| 21 | شبکه داخلی مستقل و بدون نیاز به اینترنت | done | `apps/shift/src/shift/api/routes_web.py:21-26`, `apps/shift/deploy/serve_lan.py` | `tests/integration/test_serve_lan.py` |
| 22 | استفاده روی گوشی شخصی با جفت‌سازی QR | done | `apps/shift/src/shift/api/routes_devices.py`, `apps/shift/src/shift/services/devices.py` | `tests/integration/test_devices_api.py` |
| 23 | عدم نیاز به جدول برنامه شیفت | done | `apps/shift/src/shift/domain/shift_clock.py` | `tests/unit/test_shift_clock.py` |
| 24 | عدم ثبت بدهی بیمار و تخفیف خارج از کمپین | done | `apps/shift/src/shift/domain/shift_events/campaign_discount.py` (نبود کد ط) | `tests/integration/test_events_api.py` |
| 28 | هفت نقش و پشتیبانی از چندنقشی | done | `apps/shift/src/shift/domain/permission_grants.py:21-135` | `tests/unit/test_effective_permissions.py` |
| 28 | هر شخص فقط کارهای شیفت فعلی خود را ببیند | done | `apps/shift/src/shift/domain/on_shift.py`, `apps/shift/src/shift/api/deps.py` | `tests/integration/test_on_shift_access.py` |
| 28 | افزودن کارمند و انتساب نقش‌ها توسط مدیر | done | `apps/shift/src/shift/api/routes_auth.py:192-235`, `apps/shift/src/shift/templates/manager_staff.html` | `tests/integration/test_staff_admin.py` |
| 32 | پذیرش: تحویل صندوق، قفسه و چک‌لیست مجزا | done | `apps/shift/src/shift/services/checklists.py:28-45`, `apps/shift/src/shift/services/handover.py` | `tests/integration/test_handover_api.py:640` |
| 32 | پذیرش: دریافت بار با عکس اجباری فاکتور | done | `apps/shift/src/shift/domain/purchase_payloads.py:83-85`, `apps/shift/src/shift/services/purchase.py:388` | `tests/security/test_photo_access.py` |
| 32 | پذیرش: ارسال عکس رسید کارت‌به‌کارت | done | `apps/shift/src/shift/domain/shift_events/card_transfer.py:40-56` | `tests/security/test_photo_access.py` |
| 32 | پذیرش: ابلاغ چک‌لیست به خدمه و تأیید | partial | `apps/shift/src/shift/domain/shift_events/cleaning.py:54-70` | `tests/integration/test_events_api.py` |
| 32 | پذیرش: ثبت بیمار برای پاداش | done | `apps/shift/src/shift/adapters/db/reward_repo.py:43` | `tests/integration/test_rewards_service.py` |
| 32 | پذیرش قیمت‌ها و شیفت‌های قبل را نبیند | done | `apps/shift/src/shift/domain/purchase.py`, `apps/shift/src/shift/services/purchase.py:259` | `tests/security/test_reference_number_leak.py` |
| 33 | پرستار: سرم و مصرفی CPR و چک‌لیست ترالی | done | `apps/shift/src/shift/services/checklists.py:33`, `apps/shift/src/shift/domain/handover_steps.py` | `tests/integration/test_handover_api.py:640` |
| 33 | پرستار: ثبت قند/فشار و پیشنهاد مسیر پیگیری | done | `apps/shift/src/shift/domain/shift_events/vitals.py`, `apps/shift/src/shift/static/shift-events.js:260-280` | `tests/integration/test_events_api.py` |
| 34 | انباردار: پیش‌نویس سفارش زیر حداقل | done | `apps/shift/src/shift/services/purchase.py:604`, `apps/shift/src/shift/static/purchase.js:631` | `tests/integration/test_purchase_api.py` |
| 34 | انباردار: تحویل بار از شیفت | done | `apps/shift/src/shift/services/purchase.py:389` (در حال تکمیل در final-store) | `tests/integration/test_purchase_api.py` |
| 34 | انباردار: شارژ قفسه‌ها | partial | `apps/shift/src/shift/services/purchase.py:449`, `apps/shift/src/shift/domain/shift_events/restock.py:95` | `tests/integration/test_purchase_api.py` |
| 34 | انباردار: شمارش هفتگی همه اقلام | done | `apps/shift/src/shift/services/store_actions.py:288-293` | `tests/integration/test_store_actions.py` |
| 34 | انباردار: بررسی و علامت انقضا | partial | `apps/shift/src/shift/services/store_actions.py:12-13` | `tests/integration/test_store_actions.py` |
| 34 | انباردار: تأیید بعدی برداشت اضطراری | done | `apps/shift/src/shift/domain/shift_events/emergency_withdrawal.py:99` | `tests/integration/test_events_api.py` |
| 34 | انباردار: مرجوعی به فروشنده | done | `apps/shift/src/shift/services/purchase.py:428`, `apps/shift/src/shift/static/purchase.js:1017` | `tests/integration/test_purchase_api.py` |
| 34 | انباردار قیمت و صندوق را نبیند | done | `apps/shift/src/shift/domain/permission_grants.py:60-78` | `tests/unit/test_effective_permissions.py` |
| 35 | مسئول خرید: ثبت سفارش انجام‌شده | done | `apps/shift/src/shift/services/purchase.py:357`, `apps/shift/src/shift/static/purchase.js:585` | `tests/integration/test_purchase_api.py` |
| 36 | حسابدار: دیجیتال کردن فاکتور از عکس | partial | `apps/shift/src/shift/services/purchase.py:400`, `apps/shift/src/shift/static/purchase.js:971` | `tests/integration/test_purchase_api.py` |
| 36 | حسابدار: ثبت پرداخت و عکس رسید اختیاری | done | `apps/shift/src/shift/domain/purchase_payloads.py:129`, `apps/shift/src/shift/services/purchase.py:436` | `tests/security/test_photo_access.py` |
| 36 | حسابدار: تأیید کارت‌به‌کارت | done | `apps/shift/src/shift/templates/accountant_queue.html:22-32` | `tests/integration/test_accountant_queue.py` |
| 36 | حسابدار: بستانکاری مرجوعی روی فاکتور | done | `apps/shift/src/shift/services/purchase.py:438` | `tests/integration/test_purchase_api.py` |
| 36 | حسابدار: محاسبه پاداش ماهانه | done | `apps/shift/src/shift/templates/accountant_queue.html:84` | `tests/integration/test_rewards_pages.py` |
| 36 | حسابدار شمارش صندوق و امضای تحویل ندارد | done | `apps/shift/src/shift/domain/permission_grants.py:103-116` (تصمیم ۱۰ مالک) | `tests/unit/test_effective_permissions.py` |
| 37 | پزشک شیفت: شاهد برداشت اضطراری | partial | `apps/shift/src/shift/domain/shift_events/emergency_withdrawal.py:47-63` | `tests/integration/test_events_api.py` |
| 37 | پزشک: تأیید ابطال | done | `apps/shift/src/shift/domain/shift_events/refund.py:56` | `tests/integration/test_events_api.py` |
| 38 | مدیر: کارت روزانه تجمیعی | done | `apps/shift/src/shift/services/daily_card.py:119-138`, `apps/shift/src/shift/api/routes_manager.py:401` | `tests/integration/test_daily_card.py` |
| 38 | مدیر: دکمه‌های اقدام روی کارت یا صفحات | partial | `apps/shift/src/shift/templates/manager_card.html`, `apps/shift/src/shift/templates/_manager_nav.html` | `tests/integration/test_daily_card.py` |
| 38 | مدیر: مدیریت اقلام، چک‌لیست‌ها و پرسنل | done | `apps/shift/src/shift/templates/manager_items.html`, `apps/shift/src/shift/templates/manager_staff.html` | `tests/integration/test_staff_admin.py` |
| 38 | مدیر: ارجاع تعمیرات خرابی با مهلت | done | `apps/shift/src/shift/domain/shift_events/equipment.py:140-157` | `tests/integration/test_events_api.py` |
| 40 | خدمه بدون نیاز به اپ | done | `apps/shift/src/shift/domain/shift_events/cleaning.py` | `tests/integration/test_events_api.py` |
| 44 | تحویل صندوق، قفسه و بسته‌های بار پذیرش | done | `apps/shift/src/shift/services/handover.py:429`, `apps/shift/src/shift/api/routes_handover.py:395` | `tests/integration/test_handover_api.py` |
| 45 | تحویل مجزای پرستار به پرستار | done | `apps/shift/src/shift/domain/handover_fsm.py:42-45` | `tests/integration/test_handover_api.py:640` |
| 46 | تحویل بار پذیرش به انباردار با تأیید دو طرف | partial | `apps/shift/src/shift/services/purchase.py:389-398` | `tests/integration/test_purchase_api.py` |
| 47 | شارژ قفسه با تأیید مسئول شیفت | partial | `apps/shift/src/shift/services/purchase.py:449-475` | `tests/integration/test_purchase_api.py` |
| 48 | زنجیره سفارش: انباردار به مدیر به خرید | done | `apps/shift/src/shift/services/purchase.py:108-122` | `tests/integration/test_purchase_api.py` |
| 49 | پذیرش به حسابدار: فاکتور و رسید | partial | `apps/shift/src/shift/services/purchase.py:388`, `apps/shift/src/shift/static/purchase.js:971` | `tests/security/test_photo_access.py` |
| 50 | مغایرت‌ها و تصمیم‌های باز در کارت | partial | `apps/shift/src/shift/services/daily_card.py:119-138` | `tests/integration/test_daily_card.py` |
| 54 | انحصار شمارش و پیشبرد به ۲ نفر جریان | done | `apps/shift/src/shift/services/handover.py:471,840` (`NotOfTheFlow`) | `tests/integration/test_handover_api.py:658` |

---

### بخش ۲.۲: تحویل شیفت (DOC 56-121)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 58 | شمارش کور بدون نمایش مقدار موردانتظار | done | `apps/shift/src/shift/domain/handover_counts.py`, `apps/shift/src/shift/services/handover.py:364` | `tests/security/test_reference_number_leak.py` |
| 64 | فرمول موجودی قفسه: E = C0+R-D-U-W | done | `apps/shift/src/shift/domain/handover_formulas.py:15` | `tests/unit/test_handover_formulas.py` |
| 68-72 | منبع مؤلفه‌های C0, R, D, U, W | partial | `apps/shift/src/shift/domain/effects.py` (نبود مسیر U) | `tests/unit/test_handover_formulas.py` |
| 74-80 | فرمول نقد صندوق: N = N0+P-B-W و ثبت واریز W | done | `apps/shift/src/shift/templates/accountant_queue.html:25`, `apps/shift/src/shift/domain/shift_events/cash_deposit.py` | `tests/integration/test_accountant_queue.py` |
| 82 | فقط واحد شمارش در فرم‌ها | done | `apps/shift/src/shift/domain/purchase_payloads.py` (`stock_factor`) | `tests/unit/test_catalog.py` |
| 84 | مصرفی‌های خارج خدمت دارای عدد مرجع | done | `apps/shift/tools/seed_reference_data.py` | `tests/integration/test_catalog_seed.py` |
| 88 | ممانعت از شروع تحویل با صورت‌حساب باز | done | `apps/shift/src/shift/services/handover.py:301-306` | `tests/integration/test_handover_api.py:259` |
| 92-96 | بیمار حین تحویل روی کاغذ بدون ورود به صندوق | done | `apps/shift/src/shift/services/late_cash.py`, `apps/shift/src/shift/templates/manager_card.html` | `tests/integration/test_handover_api.py` |
| 100 | شمارش صندوق دو نفره و کور | done | `apps/shift/src/shift/domain/handover_counts.py:87`, `apps/shift/src/shift/api/routes_handover.py:328` | `tests/integration/test_handover_api.py:338` |
| 101 | قفسه‌ها به ترتیب چیدمان فیزیکی | done | `apps/shift/src/shift/services/catalog.py:219`, `apps/shift/src/shift/services/checklists.py:29` | `tests/integration/test_catalog_api.py` |
| 102 | چک‌لیست‌ها با پاسخ بله/خیر، عدد، عکس | partial | `apps/shift/src/shift/api/routes_handover.py:373`, `apps/shift/src/shift/static/handover.js:432` | `tests/integration/test_handover_api.py` |
| 102 | انعکاس پاسخ‌های چک‌لیست در کارت مدیر | done | `apps/shift/src/shift/services/daily_card.py:862`, `apps/shift/src/shift/templates/manager_card.html` | `tests/integration/test_daily_card.py` |
| 103 | تحویل بسته‌های بار از شیفت به شیفت | done | `apps/shift/src/shift/api/routes_handover.py:395-415` | `tests/integration/test_handover_api.py` |
| 104 | امضای تحویل توسط هر دو نفر | done | `apps/shift/src/shift/services/handover.py:447,813` | `tests/integration/test_handover_api.py:399` |
| 108-111 | زنجیره بازشماری، بازشماری کور و مشترک | done | `apps/shift/src/shift/domain/handover_counts.py`, `apps/shift/src/shift/static/handover.js:34-41` | `tests/integration/test_handover_api.py:374` |
| 111 | ثبت مغایرت با نام نفر خروجی در کارت مدیر | done | `apps/shift/src/shift/templates/manager_card.html` (بخش مغایرت‌ها) | `tests/integration/test_daily_card.py` |
| 113 | کسری صندوق فقط هشدار بدون کسر از پاداش | done | `apps/shift/src/shift/domain/rewards.py` | `tests/property/test_reward_ledger.py` |
| 113 | ثبت دیرهنگام بدون برچسب مقصر خودکار | done | `apps/shift/src/shift/services/held_events.py` | `tests/integration/test_held_events.py` |
| 115 | اقلام غیرشیفتی بدون انتساب به مقصر | done | `apps/shift/src/shift/services/purchase.py:497-505` (`culprit_id=None`) | `tests/integration/test_purchase_api.py` |
| 119 | خروج زودهنگام فقط با اطلاع و تأیید مدیر | done | `apps/shift/src/shift/services/handover.py:464` | `tests/integration/test_handover_api.py:524` |
| 120 | ممانعت از تحویل به خود در بلوک کاری | partial | `apps/shift/src/shift/domain/handover_fsm.py:85` | `tests/integration/test_handover_api.py:284` |
| 121 | حفظ محرمانگی در شیفت‌های متوالی یک نفر | done | `apps/shift/src/shift/api/routes_events.py:137` | `tests/security/test_reference_number_leak.py` |

---

### بخش ۲.۳: رویدادهای حین شیفت (DOC 125-154)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 125 | دکمه بزرگ و سؤالات تک‌مرحله‌ای | done | `apps/shift/src/shift/templates/shift_events.html`, `apps/shift/src/shift/static/shift-events.js` | `tests/integration/test_events_api.py` |
| 125 | عدم نمایش رویداد بسته‌شده به پرسنل | done | `apps/shift/src/shift/services/shift_events.py` (`pending_for`, `open_targets`) | `tests/integration/test_events_api.py` |
| 129 ک | ثبت کارت‌به‌کارت با عکس اختیاری فیش | done | `apps/shift/src/shift/domain/shift_events/card_transfer.py:40-56` | `tests/security/test_photo_access.py` |
| 130 ب | برگشت پول و ابطال خدمت | done | `apps/shift/src/shift/domain/shift_events/refund.py:42-56` | `tests/integration/test_events_api.py` |
| 131 ت | ثبت تخفیف‌های دارای مصوبه کمپین | done | `apps/shift/src/shift/domain/shift_events/campaign_discount.py:34-51` | `tests/integration/test_events_api.py` |
| 132 د | برداشت از صندوق با سقف ۳۰۰ هزار تومان | done | `apps/shift/src/shift/domain/shift_events/cash_withdrawal.py:113-130` (ADR-019) | `tests/integration/test_events_api.py` |
| 133 ا | مغایرت حین تحویل و بازشماری مشترک | partial | `apps/shift/src/shift/services/handover.py:388-400` | `tests/integration/test_handover_api.py` |
| 134 ض | ضایعات با ثبت مقصر، عکس و شاهد دوم | done | `apps/shift/src/shift/domain/shift_events/waste.py:108-121` | `tests/integration/test_events_api.py` |
| 135 و | ورودی کالا در میانه شیفت | done | `apps/shift/src/shift/domain/shift_events/restock.py:95-107` | `tests/integration/test_events_api.py` |
| 136 م | امانات و اقلام همراه بیمار | done | `apps/shift/src/shift/domain/shift_events/patient_items.py:56-86` | `tests/integration/test_events_api.py` |
| 138 | حذف کامل کد «ط» از سیستم | done | نبود در `apps/shift/src/shift/domain/shift_events/` | `tests/contract/test_event_registry.py` |
| 142 | نمایش کارت‌به‌کارت در انتظار در کارت مدیر | done | `apps/shift/src/shift/services/daily_card.py` (`open_events`) | `tests/integration/test_daily_card.py` |
| 143 | خواندن خودکار موارد ابطال حسابداری | done | `apps/shift/src/shift/services/accounting_bridge.py` (`VOIDS`) | `tests/integration/test_accounting_bridge.py` |
| 144 | سقف برداشت ۳۰۰ هزار تومان با عکس فیش | done | `apps/shift/src/shift/domain/policy.py`, ADR-019 | `tests/integration/test_events_api.py` |
| 145 | ثبت تخفیف کمپین با تاریخ جلالی | done | `apps/shift/src/shift/templates/manager_campaigns.html`, `static/jalali-datepicker.js` | `tests/integration/test_campaigns_api.py` |
| 146 | عدم امکان تأیید ضایعات توسط مقصر آن | done | `apps/shift/src/shift/domain/shift_events/waste.py:60-80` | `tests/integration/test_events_api.py` |
| 147 | اقلام امانی بیمار خارج از شمارش قفسه | done | `apps/shift/src/shift/domain/shift_events/patient_items.py` | `tests/integration/test_events_api.py` |
| 151 | برداشت اضطراری دونفره با ثبت پلمب | partial | `apps/shift/src/shift/domain/shift_events/emergency_withdrawal.py:88-103` | `tests/integration/test_events_api.py` |
| 152 | خرابی با مهلت ارجاع و برجسته‌سازی مهلت | partial | `apps/shift/src/shift/domain/shift_events/equipment.py:121-179` | `tests/integration/test_events_api.py` |
| 153 | ثبت مستقیم علائم حیاتی قند و فشار | done | `apps/shift/src/shift/domain/shift_events/vitals.py:60-76` | `tests/integration/test_events_api.py` |
| 153 | پیشنهاد مسیر پیگیری در صورت عبور از آستانه | done | `apps/shift/src/shift/static/shift-events.js:260-280`, `setting_keys.py:23-25` | `tests/integration/test_events_api.py` |
| 154 | نظافت خدمه: ابلاغ شفاهی و ثبت تأیید | partial | `apps/shift/src/shift/domain/shift_events/cleaning.py:54-70` | `tests/integration/test_events_api.py` |

---

### بخش ۲.۴: خرید و انبار (DOC 158-187)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 162 | پیش‌نویس خرید با پیشنهاد زیر حداقل | done | `apps/shift/src/shift/services/purchase.py:604` | `tests/integration/test_purchase_api.py` |
| 163 | مدیر: تأیید، رد یا اصلاح سفارش خرید | done | `apps/shift/src/shift/static/purchase.js:515-543`, `services/purchase.py:349` | `tests/integration/test_purchase_api.py` |
| 164 | مسئول خرید: ثبت سفارش انجام‌شده | done | `apps/shift/src/shift/services/purchase.py:357` | `tests/integration/test_purchase_api.py` |
| 165 | پذیرش: عکس اجباری فاکتور در ورود بار | done | `apps/shift/src/shift/domain/purchase_payloads.py:83-85`, `services/purchase.py:388` | `tests/security/test_photo_access.py` |
| 165 | پذیرش: شمارش و تطبیق با فاکتور | partial | `apps/shift/src/shift/services/purchase.py:365-388` | `tests/integration/test_purchase_api.py` |
| 165 | پذیرش قیمت‌ها را وارد نکند | done | `apps/shift/src/shift/domain/purchase_payloads.py:68` | `tests/security/test_reference_number_leak.py` |
| 166 | انتقال بسته بار از شیفت به شیفت | done | `apps/shift/src/shift/api/routes_handover.py:395` | `tests/integration/test_handover_api.py` |
| 167 | انباردار بسته بار را تحویل می‌گیرد | partial | `apps/shift/src/shift/services/purchase.py:389` | `tests/integration/test_purchase_api.py` |
| 168 | حسابدار فاکتور را از روی عکس ثبت کند | partial | `apps/shift/src/shift/services/purchase.py:400`, `static/purchase.js:971` | `tests/integration/test_purchase_api.py` |
| 172 | بار مشکل‌دار: ثبت برچسب و قفل پرداخت | done | `apps/shift/src/shift/services/purchase.py:374,409` | `tests/integration/test_purchase_api.py` |
| 173 | مرجوعی به فروشنده با بستانکاری | done | `apps/shift/src/shift/services/purchase.py:428,438` | `tests/integration/test_purchase_api.py` |
| 177 | شارژ قفسه با شمارش و ثبت هم‌زمان | partial | `apps/shift/src/shift/services/purchase.py:449` | `tests/integration/test_purchase_api.py` |
| 177 | افزایش هم‌زمان قفسه و کاهش انبار | partial | `apps/shift/src/shift/domain/purchase_specs.py:21-33` | `tests/integration/test_purchase_api.py` |
| 179 | هر کد کالا فقط در یک محل قرار گیرد | done | `apps/shift/src/shift/services/catalog.py:399` | `tests/integration/test_catalog_api.py` |
| 183 | بازه شمارش هفتگی از شنبه تا دوشنبه | done | `apps/shift/src/shift/domain/stock_checks.py:53-72` | `tests/unit/test_stock_checks.py` |
| 183 | شمارش هفتگی کور شامل همه اقلام انبار | done | `apps/shift/src/shift/services/store_actions.py:288-293` (در حال نهایی‌سازی) | `tests/integration/test_store_actions.py` |
| 184 | هشدار کارت مدیر در صورت تأخیر تا دوشنبه | done | `apps/shift/src/shift/services/daily_card.py:1664` | `tests/integration/test_daily_card.py` |
| 185 | علامت‌گذاری اقلام نزدیک به تاریخ انقضا | partial | `apps/shift/src/shift/services/store_actions.py:12-13` | `tests/integration/test_store_actions.py` |
| 186 | مغایرت انبار اصلی بدون انتساب نام مقصر | done | `apps/shift/src/shift/domain/purchase_specs.py` | `tests/integration/test_store_actions.py` |
| 187 | شمارش تأییدی دوره‌ای ۵ قلم توسط حسابدار | done | `apps/shift/src/shift/domain/permission_grants.py:107`, `services/store_actions.py` | `tests/integration/test_store_actions.py` |

---

### بخش ۲.۵: کار حسابدار (DOC 189-212)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 195 | ثبت اقلام فاکتور با نرخ و تعداد از روی عکس | partial | `apps/shift/src/shift/services/purchase.py:400`, `static/purchase.js:971` | `tests/integration/test_purchase_api.py` |
| 196 | ثبت وضعیت و روش پرداخت و عکس رسید اختیاری | done | `apps/shift/src/shift/domain/purchase_payloads.py:129`, `services/purchase.py:436` | `tests/security/test_photo_access.py` |
| 197 | تاریخچه قیمت هر قلم بر اساس فاکتورها | missing | `apps/shift/src/shift/services/purchase.py:710-731` (در شاخه shift-rewards) | `tests/integration/test_purchase_api.py` |
| 198 | قفل بودن پرداخت فاکتور دارای برچسب مشکل | done | `apps/shift/src/shift/services/purchase.py:409-427` | `tests/integration/test_purchase_api.py` |
| 199 | عدم ورود فاکتورهای کاغذی قدیمی به سیستم | done | نبود امکان import داده قدیمی در اپ شیفت | ADR-002 |
| 203 | ثبت بستانکاری مرجوعی روی فاکتور اصلی | done | `apps/shift/src/shift/services/purchase.py:438` | `tests/integration/test_purchase_api.py` |
| 207 | بررسی کارت‌به‌کارت و ارسال ردشده به مدیر | done | `apps/shift/src/shift/templates/accountant_queue.html:22-32` | `tests/integration/test_accountant_queue.py` |
| 208 | نمایش برداشت‌های صندوق در صف حسابدار | done | `apps/shift/src/shift/templates/accountant_queue.html:42-54` | `tests/integration/test_accountant_queue.py` |
| 212 | تهیه پاداش ماهانه برای فیش حقوقی | done | `apps/shift/src/shift/templates/accountant_queue.html:84` | `tests/integration/test_rewards_pages.py` |

---

### بخش ۲.۶: پاداش و رقابت (DOC 214-236)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 216 | تعیین نرخ هر کار توسط مدیر به صورت ماهانه | done | `apps/shift/src/shift/api/routes_rewards.py:124`, `templates/manager_rewards.html` | `tests/integration/test_rewards_pages.py` |
| 220 | احتساب خودکار قند و فشار از حسابداری | done | `apps/shift/src/shift/adapters/db/reward_repo.py:30-41` | `tests/integration/test_rewards_service.py` |
| 221 | پاداش بازکردن مسیر پیگیری با تأیید دستی مدیر | done | تصمیم ۴ مالک (در حال ادغام از شاخه shift-rewards) | `tests/integration/test_rewards_service.py` |
| 222 | اعتبارسنجی کد ملی بیمار با الگوریتم رسمی | done | `apps/shift/src/shift/adapters/db/reward_repo.py:43-50` | `tests/integration/test_rewards_service.py` |
| 222 | رد پاداش برای شماره موبایل تکراری | in-merge | `m0008_shift_registration_first_for_phone.py` (استثنای در حال ادغام) | `tests/test_migrations.py` |
| 222 | نمونه تصادفی تماس روی کارت مدیر | in-merge | `v_shift_call_sample_v1` (استثنای در حال ادغام) | `tests/test_migrations.py` |
| 223 | تأیید کارهای ارجاعی مدیر با مبلغ | in-merge | رویداد `reward.confirmed` (در حال ادغام از شاخه shift-rewards) | `tests/integration/test_rewards_pages.py` |
| 227 | تعلق پاداش به ماه تأیید کار | done | `apps/shift/src/shift/domain/rewards.py:75-87` | `tests/unit/test_rewards.py` |
| 228 | کسر ابطال‌ها از فیش حقوقی ماه بعد | done | `apps/shift/src/shift/domain/rewards.py:90-93` | `tests/unit/test_rewards.py` |
| 229 | عدم کسر کسری‌های صندوق از پاداش پرسنل | done | `apps/shift/src/shift/domain/rewards.py:1-14` | `tests/property/test_reward_ledger.py` |
| 233 | مشاهده فقط رتبه خود توسط هر پرسنل | done | `apps/shift/src/shift/templates/rewards_mine.html`, `domain/rewards.py:166` | `tests/integration/test_rewards_pages.py` |
| 233 | نمایش هدف تیمی ماهانه کنار رتبه پرسنل | in-merge | جدول `reward_team_goals` (در حال ادغام از شاخه shift-rewards) | `tests/integration/test_rewards_pages.py` |
| 234 | نمایش مبلغ پاداش ماه جاری خود پرسنل | done | `apps/shift/src/shift/templates/rewards_mine.html:30-34` | `tests/integration/test_rewards_pages.py` |
| 235 | جدول کامل پاداش‌ها برای مدیر | done | `apps/shift/src/shift/templates/manager_rewards.html:70-97`, `_manager_nav.html:8` | `tests/integration/test_rewards_pages.py` |

---

### بخش ۲.۷: داشبورد مدیر و تنظیمات (DOC 237-270)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 239 | صدور کارت در ساعت ۷:۳۰ و عدم صدور در جمعه | done | `apps/shift/src/shift/domain/daily_card.py`, ADR-018 | `tests/unit/test_daily_card.py` |
| 243 | تصمیم‌های باز با امکان تأیید، رد و تغییر | partial | `apps/shift/src/shift/templates/manager_card.html`, `static/manager.js` | `tests/integration/test_daily_card.py` |
| 243 | نمایش پیوند مغایرت‌های مربوط به هم | partial | `apps/shift/src/shift/templates/manager_card.html` | `tests/integration/test_daily_card.py` |
| 244 | ثبت مغایرت با نام پرسنل یا عنوان «بدون مقصر» | done | `apps/shift/src/shift/templates/manager_card.html` | `tests/integration/test_daily_card.py` |
| 245 | گزارش تعداد وقوع هر کد رویداد در ۲۴ ساعت | partial | `apps/shift/src/shift/services/daily_card.py` (`shift_events`) | `tests/integration/test_daily_card.py` |
| 246 | تفکیک خرابی‌های تازه و ارجاعات مهلت‌گذشته | partial | `apps/shift/src/shift/services/daily_card.py` (`open_events`) | `tests/integration/test_daily_card.py` |
| 247 | فهرست کارهای معوق (هفتگی، کارت‌به‌کارت، بار) | done | `apps/shift/src/shift/services/daily_card.py:119-138` | `tests/integration/test_daily_card.py` |
| 248 | نمونه تماس بیماران برای بررسی کیفیت | in-merge | استثنای در حال ادغام | `tests/test_migrations.py` |
| 249 | هشدار اقلام زیر حداقل موجودی | done | `apps/shift/src/shift/services/daily_card.py` (`below_minimum`) | `tests/integration/test_daily_card.py` |
| 253 | افزودن، ویرایش و بازنشستگی قلم توسط مدیر | done | `apps/shift/src/shift/templates/manager_items.html`, `static/manager-items.js` | `tests/integration/test_catalog_api.py` |
| 255 | تنظیم نوع، محل و نقش مسئول هر قلم | done | `apps/shift/src/shift/templates/manager_items.html`, `services/catalog.py:271` | `tests/integration/test_catalog_api.py` |
| 255 | تنظیم تناوب پرسش قلم (شیفت، روزانه، هفتگی) | done | `apps/shift/src/shift/adapters/db/migrations/m0020_catalog_lists.py` | `tests/integration/test_catalog_api.py` |
| 255 | تعریف چک‌لیست‌های فضا (عمومی، اتاق پزشک) | done | `apps/shift/src/shift/templates/manager_items.html`, `services/checklists.py` | `tests/integration/test_catalog_api.py` |
| 265 | مدیریت پرسنل و نقش‌ها: ساخت، انتساب، غیرفعال | done | `apps/shift/src/shift/templates/manager_staff.html`, `api/routes_auth.py:192` | `tests/integration/test_staff_admin.py` |
| 266 | سقف برداشت نقد از صندوق | done | `apps/shift/src/shift/domain/setting_keys.py:19`, `templates/manager_settings.html` | `tests/integration/test_settings_versioning.py` |
| 266 | تنظیم آستانه‌های قند، فشار و کپسول اکسیژن | done | `apps/shift/src/shift/domain/setting_keys.py:23-26`, `templates/manager_settings.html` | `tests/integration/test_settings_versioning.py` |
| 266 | تنظیم تنخواه ثابت صندوق به عنوان یادآور | done | `apps/shift/src/shift/domain/setting_keys.py:27`, `services/daily_card.py:1020` | `tests/integration/test_daily_card.py` |
| 267 | مدیریت و ثبت کمپین‌ها با تاریخ جلالی | done | `apps/shift/src/shift/templates/manager_campaigns.html`, `static/jalali-datepicker.js` | `tests/integration/test_campaigns_api.py` |
| 268 | تعیین نرخ پاداش هر کار برای هر ماه | done | `apps/shift/src/shift/api/routes_rewards.py:124`, `templates/manager_rewards.html` | `tests/integration/test_rewards_pages.py` |
| 268 | تنظیم هدف تیمی ماهانه | in-merge | در حال ادغام از شاخه shift-rewards | `tests/integration/test_rewards_pages.py` |
| 270 | اعمال تغییرات تنظیمات از شیفت بعدی | done | `apps/shift/src/shift/services/settings.py:111-135`, `services/catalog.py:350` | `tests/property/test_settings_versioning_mid_shift.py` |

---

### بخش ۲.۸: راه‌اندازی و پیوست (DOC 272-389)

| سطر سند / شناسه | شرح قاعده در یک جمله | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 276 | تکمیل کد کالا و حداقل موجودی توسط مدیر | done | `apps/shift/src/shift/templates/manager_items.html`, `api/routes_manager.py:ITEMS_PATH` | `tests/integration/test_catalog_api.py` |
| 276 | تعریف اولیه کارکنان و نقش‌ها در سیستم | done | `apps/shift/src/shift/templates/manager_staff.html`, `api/routes_auth.py` | `tests/integration/test_staff_admin.py` |
| 277 | شمارش اولیه انبار اصلی توسط انباردار | in-merge | `store_actions.py:275` (اصلاحات final-store-critic) | `tests/integration/test_store_actions.py` |
| 278 | شمارش اولیه قفسه/صندوق توسط پذیرش و CPR توسط پرستار | partial | `apps/shift/src/shift/api/routes_manager.py:baseline` (تصمیم ۹ در حال ادغام) | `tests/integration/test_handover_api.py` |
| 279 | شروع شمارش کور بلافاصله از شیفت بعد | done | `apps/shift/src/shift/services/handover.py:198-262` | `tests/integration/test_handover_api.py` |
| 280 | حالت یادگیری ۷ روز اول بدون محاسبه جریمه | in-merge | برچسب یادگیری در کارت روزانه (در حال ادغام) | `tests/integration/test_daily_card.py` |
| 284-388 | ۶۹ قلم و ۱۴ مورد چک‌لیست ترالی | done | `apps/shift/tools/seed_reference_data.py:66-` | `tests/integration/test_catalog_seed.py` |
| 284 | تطبیق واحدهای سنجش CPR با نظر پزشک | partial | `apps/shift/tools/seed_reference_data.py` (نیازمند بازبینی بالینی مالک) | بازبینی اسناد |
| 389 | ساخت چک‌لیست‌های فضا در پنل مدیریت | done | `apps/shift/src/shift/templates/manager_items.html`, `services/checklists.py` | `tests/integration/test_catalog_api.py` |

---

### بخش ۲.۹: جدول ۳۱ تصمیم مالک (DOC 395-427)

| # | تصمیم مالک | وضعیت | شاهد پیاده‌سازی (فایل و خط) | آزمون اثبات‌کننده |
|---|---|---|---|---|
| 1 | خروجی همه را می‌بندد؛ بیمار حین تحویل کاغذی | done | `apps/shift/src/shift/services/handover.py:301`, `services/late_cash.py` | `tests/integration/test_handover_api.py` |
| 2 | مصرفی‌ها عدد مرجع دارند؛ حداقل و کد را مدیر می‌زند | done | `apps/shift/tools/seed_reference_data.py`, `templates/manager_items.html` | `tests/integration/test_catalog_seed.py` |
| 3 | زنجیره تأمین خرید: انباردار به مدیر به مسئول خرید | done | `apps/shift/src/shift/services/purchase.py:108-122` | `tests/integration/test_purchase_api.py` |
| 4 | ملاک فقط واحد شمارش است؛ ضرایب در خرید محاسبه شود | done | `apps/shift/src/shift/domain/purchase_payloads.py` (`stock_factor`) | `tests/unit/test_catalog.py` |
| 5 | خروج زودهنگام بدون مهلت و با تایید مدیر | done | `apps/shift/src/shift/services/handover.py:464` (ADR-017) | `tests/integration/test_handover_api.py:524` |
| 6 | یک مسئول شیفت در هر جریان؛ انحصار شمارش به دو نفر | done | `apps/shift/src/shift/services/handover.py:471,840` (`NotOfTheFlow`) | `tests/integration/test_handover_api.py:658` |
| 7 | عدم استفاده از جدول زمان‌بندی شیفت | done | `apps/shift/src/shift/domain/shift_clock.py` | `tests/unit/test_shift_clock.py` |
| 8 | شبکه محلی درمانگاه و پروتکل HTTPS با CA داخلی | done | `apps/shift/deploy/serve_lan.py`, ADR-015, ADR-023 | `tests/integration/test_serve_lan.py` |
| 9 | ثبت دیرهنگام بدون برچسب خودکار مقصر | done | `apps/shift/src/shift/services/held_events.py` | `tests/integration/test_held_events.py` |
| 10 | اعمال تخفیف صرفاً از طریق کمپین‌های مصوب | done | `apps/shift/src/shift/domain/shift_events/campaign_discount.py` | `tests/integration/test_events_api.py` |
| 11 | سقف برداشت نقد ۳۰۰ هزار تومان + عکس فیش | done | `apps/shift/src/shift/domain/policy.py`, ADR-019 | `tests/integration/test_events_api.py` |
| 12 | برداشت اضطراری انبار دونفره با تأیید انباردار | partial | `apps/shift/src/shift/domain/shift_events/emergency_withdrawal.py` (نبود فیلد پلمب) | `tests/integration/test_events_api.py` |
| 13 | بار مشکل‌دار، قفل خودکار پرداخت تا رفع مشکل | done | `apps/shift/src/shift/services/purchase.py:374,409` | `tests/integration/test_purchase_api.py` |
| 14 | مرجوعی کالا به فروشنده و ثبت بستانکاری فاکتور | done | `apps/shift/src/shift/services/purchase.py:428,438` | `tests/integration/test_purchase_api.py` |
| 15 | برگشت کالای بیمار متصل به ابطال حسابداری | done | `apps/shift/src/shift/services/accounting_bridge.py` | `tests/integration/test_accounting_bridge.py` |
| 16 | کارت‌به‌کارت با عکس اختیاری و تایید حسابدار | done | `apps/shift/src/shift/domain/shift_events/card_transfer.py`, `templates/accountant_queue.html` | `tests/security/test_photo_access.py` |
| 17 | ثبت قند/فشار، پاداش، دکمه مسیر پیگیری بالای آستانه | done | `apps/shift/src/shift/static/shift-events.js:260-280`, `setting_keys.py:23-25` | `tests/integration/test_events_api.py` |
| 18 | ثبت بیمار: کنترل کد ملی، کنترل موبایل تکراری و نمونه تماس | in-merge | در حال تکمیل طبق استثنای اعلام‌شده | `tests/test_migrations.py` |
| 19 | کسری صندوق فقط جنبه هشداری دارد و پاداش را نمی‌کاهد | done | `apps/shift/src/shift/domain/rewards.py` | `tests/property/test_reward_ledger.py` |
| 20 | عدم ثبت یا پذیرش بدهی بیمار در سیستم شیفت | done | معماری بدون بدهی بیمار | `tests/contract/test_event_registry.py` |
| 21 | اتصال با گوشی شخصی از طریق جفت‌سازی امن QR | done | `apps/shift/src/shift/services/devices.py` | `tests/integration/test_devices_api.py` |
| 22 | نمایش رتبه فردی و پیشرفت هدف تیمی ماهانه | in-merge | در حال ادغام از شاخه shift-rewards | `tests/integration/test_rewards_pages.py` |
| 23 | ثبت خرابی تجهیزات با تعیین مهلت و پیگیری | partial | `apps/shift/src/shift/domain/shift_events/equipment.py` | `tests/integration/test_events_api.py` |
| 24 | کاربری خدمه بدون درگیر شدن با اپلیکیشن | done | `apps/shift/src/shift/domain/shift_events/cleaning.py` | `tests/integration/test_events_api.py` |
| 25 | حذف نیاز به مهر فیزیکی در روال‌های نرم‌افزاری | done | روال تأیید دیجیتال امضا در FSM تحویل | `tests/integration/test_handover_api.py` |
| 26 | دوره شمارش هفتگی از شنبه تا دوشنبه | in-merge | `apps/shift/src/shift/services/store_actions.py` (استثنای در حال ادغام) | `tests/unit/test_stock_checks.py` |
| 27 | تعلق پاداش به ماه قطعی شدن و تسویه اصلاحات | done | `apps/shift/src/shift/domain/rewards.py` | `tests/unit/test_rewards.py` |
| 28 | مقصر ضایعات نباید تأییدکننده دوم آن باشد | done | `apps/shift/src/shift/domain/shift_events/waste.py:60-80` | `tests/integration/test_events_api.py` |
| 29 | عدم انتساب مقصر برای اقلام غیرشیفتی | done | `apps/shift/src/shift/services/purchase.py:497` | `tests/integration/test_purchase_api.py` |
| 30 | اقلام امانی بیمار مستقل از موجودی درمانگاه | done | `apps/shift/src/shift/domain/shift_events/patient_items.py` | `tests/integration/test_events_api.py` |
| 31 | هر کد کالا فقط در یک موقعیت فیزیکی | done | `apps/shift/src/shift/services/catalog.py:399` | `tests/integration/test_catalog_api.py` |

---

## ۳. ممیزی امنیت، داده‌های بیماران و حریم خصوصی (E02)

طبق سند `docs/engineering/E02_SECURITY_AND_PRIVACY.md` و خطوط قرمز سیستم:

1. **داده‌های واقعی بیماران (PHI):**
   - هیچ فایلی از مسیر `C:/Users/Emad Karimi/Desktop/sib/_data/` باز، خوانده یا چاپ نشد.
   - هیچ دستوری با فلگ `-m prod_snapshot` یا اسکریپت `golden_master.py` اجرا نگردید.
   - کلیه آزمایش‌ها و بررسی‌های کد بر روی ساختار مدل‌ها، ویوها و داده‌های مصنوعی انجام شد.

2. **عدم نشت عدد مرجع (Reference Number Leak):**
   - الگوی شمارش کور به دقت حفظ شده است.
   - کنترل دسترسی نشان می‌دهد مقادیر مورد انتظار در هیچ‌یک از پاسخ‌های API تحویل، شمارش و رویدادهای شیفت بازگردانده نمی‌شوند (`apps/shift/tests/security/test_reference_number_leak.py`).

3. **نبود کلیدهای مخفی و توکن‌های ثابت (Secrets in Code):**
   - اسکن کدهای منبع نشان می‌دهد هیچ رمز عبور، کلید خصوصی، یا توکن ثابتی در کد ثبت نشده است.
   - کلید نشست‌ها در زمان راه‌اندازی هر سیستم به طور تصادفی تولید می‌شود.

4. **محدوده شبکه و حریم خصوصی کلاینت:**
   - سرآیند CSP کاملاً محلی و بدون وابستگی به اینترنت (`default-src 'self'`) تنظیم شده است.
   - گواهی محلی با CA اختصاصی درمانگاه از طریق `serve_lan.py` پشتیبانی شده و اطلاعات کلاینت از شبکه داخلی خارج نمی‌شود.

---

## ۴. سؤالاتی که فقط مالک می‌تواند پاسخ دهد (به همراه پاسخ پیشنهادی)

۱. **برگشت کالا از قفسه به انبار (U):**
   - *سؤال:* در جریان روزمره درمانگاه، آیا پرسنل واقعاً نیاز به فرم رسمی برای بازگرداندن اقلام مازاد از قفسه به انبار اصلی دارند، یا این اقلام تا پایان شیفت در قفسه مانده و تحویل نفر بعد می‌شوند؟
   - *پاسخ پیشنهادی:* نیازی به افزودن فرم پیچیده برای بازگشت به انبار نیست؛ اقلام مازاد در قفسه باقی بمانند و در صورت خرابی یا فساد، از طریق رویداد «ضایعات» خارج شوند.

۲. **نحوه دسترسی حسابدار به تصویر فاکتور هنگام ثبت قیمت‌ها:**
   - *سؤال:* آیا حسابدار فاکتور کاغذی را به صورت فیزیکی در اختیار دارد یا باید حتماً پیش‌نمایش تصویر فاکتورِ بارگذاری‌شده توسط پذیرش در صفحه دیجیتال‌سازی مرورگر باز شود؟
   - *پاسخ پیشنهادی:* برای تحقق هدف «حذف جابه‌جایی کاغذ»، تصویر فاکتور با استفاده از هش ذخیره‌شده به عنوان یک پیش‌نمایش کوچک (قابل بزرگ‌نمایی) در فرم دیجیتال‌سازی حسابدار اضافه شود.

۳. **ثبت شماره پلمب پاکت کلید در برداشت اضطراری:**
   - *سؤال:* آیا ثبت شماره سریال پلمب جدید پاکت کلید انبار در اپلیکیشن الزامی است یا هماهنگی فیزیکی و شفاهی با انباردار کافی است؟
   - *پاسخ پیشنهادی:* یک فیلد متنی اختیاری تحت عنوان «شماره پلمب جدید» به فرم برداشت اضطراری اضافه شود تا پیگیری نظارتی کامل گردد.

۴. **تطبیق اقلام و حداقل‌های ترالی CPR:**
   - *سؤال:* آیا فهرست ۱۴ قلمی چک‌لیست ترالی و مقادیر حداقلی واردشده در دیتابیس با آخرین چک‌لیست بالینی و استاندارد پزشک مسئول درمانگاه همخوانی دارد؟
   - *پاسخ پیشنهادی:* پزشک مسئول درمانگاه از طریق صفحهٔ `/manager/items` یک بار مقادیر حداقل و واحدهای این ۱۴ قلم را بازبینی و در صورت نیاز اصلاح کند.

---

## ۵. نتیجه ممیزی (VERDICT)

با توجه به این‌که اصول هسته شامل دسترسی بر پایه شیفت (ADR-021)، یکپارچگی نقش‌ها و صفحه مدیریت کارکنان (ADR-022)، انحصار جریان تحویل به دو نفر (A5)، تفکیک چک‌لیست‌های ترالی و پذیرش (ADR-024)، آستانه‌های مدیر، واریز نقد، و حذف اختیارات حسابدار در شمارش صندوق با موفقیت پیاده شده‌اند، اما برخی جزئیات تکمیلی نظیر نمایش تصویر فاکتور در فرم حسابدار، مسیر U، و ادغام بخش‌های نهایی در جریان است:

**VERDICT: gaps**
