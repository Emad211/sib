# ممیزی اپ شیفت در برابر سند مالک

> شاخه `feat/shift-ui` (HEAD `abd42e7`)، `apps/shift`. فقط‌خواندنی؛ هیچ فایلی از `_data/` باز نشد. وضعیت‌ها از روی **کد** سنجیده شد، نه از روی نام تست یا سند.
> اختصار مسیرها: `S/` = `apps/shift/src/shift/`، `T/` = `apps/shift/tests/`، `DOC` = سند منطق مالک در ریشهٔ مخزن.
> وضعیت‌ها: **done** (انجام و قابل‌دسترس)، **partial** (بخشی)، **no UI** (سرویس هست و صفحه یا دکمه‌ای برای آن نقش نیست)، **not wired** (قاعده هست و هیچ‌کس صدایش نمی‌زند)، **missing** (نیست)، **contradicts** (برخلاف سند است).
> تصمیم‌های عمدی مالک (ADR-017 شروع اضطراری تک‌نفره، ADR-019 برداشت بالای سقف و تصمیم بعدی مدیر) علامت‌گذاری نشده‌اند.

## ۰. یافته‌های اصلی (پیش از جدول)

1. **هیچ راهی برای ساختن کارمند یا مدیر نیست.** `staff_repo.insert` و `insert_role` فقط در تست‌ها صدا زده می‌شوند (`S/adapters/db/staff_repo.py:61,90`). نه صفحه، نه API، نه ابزار `tools/`. روی نصب واقعی، ورود ممکن نیست مگر با SQL دستی.
2. **تغییر نقش از API به مجوزها نمی‌رسد.** `PUT /api/v1/catalog/staff/{id}/roles/{role}` (`S/api/routes_catalog.py:217`) فقط جدول `catalog_staff_role_versions` را می‌نویسد؛ ورود و مجوزها از جدول دیگری (`staff_role`) ساخته می‌شود (`S/services/auth.py:517`) و `CatalogService.roles_of` (`S/services/catalog.py:228`) را هیچ‌کس نمی‌خواند.
3. **صفحهٔ تحویل شیفت از هیچ‌جا لینک ندارد.** `home.html` لینک رویدادها، پاداش، صف حسابدار، کارت مدیر و خرید دارد، ولی `/handover` ندارد (`S/templates/home.html`، `S/api/routes_web.py:93-123`). همچنین `/manager/rewards` و `/admin/devices` هیچ لینکی ندارند؛ `/manager/campaigns` فقط از صفحهٔ تنظیمات می‌رسد.
4. **چک‌لیست تحویل برای هر دو جریان یکی است** و ترتیب اقلام الفبای شناسهٔ لاتین است نه چیدمان قفسه (`S/api/routes_handover.py:319-323`، `S/services/catalog.py:219`). پذیرش هم باید ۱۴ مورد ترالی را پر کند.
5. **انبار اصلی مرجع درست ندارد.** دریافت بار از فروشنده، هیچ چیز به انبار اصلی اضافه نمی‌کند (`S/domain/purchase_specs.py:41-53`)، شمارش اولیهٔ انبار اصلی وجود ندارد، و شمارش هفتگی با شمارش «یک قلم» هفته را بسته می‌کند (`S/domain/stock_checks.py:66`). مغایرت هفتگی عملاً نویز است.
6. **پاسخ چک‌لیست (بله/خیر و فشار کپسول) به هیچ‌جا نمی‌رسد.** `handover.checklist_answered` اثر projection ندارد و کارت مدیر آن را نمی‌خواند.
7. **واریز نقد به حساب (W در فرمول صندوق)** رویداد و مجوز دارد، ولی هیچ صفحه‌ای ندارد؛ هر واریز مغایرت کاذب صندوق می‌سازد.
8. شمارش، پیشروی، چک‌لیست و بسته در تحویل به دو نفر همان جریان **بسته نیست**؛ فقط امضا بسته است (`S/services/handover.py:813-819`). پذیرش دوم می‌تواند بشمارد (H-10).

## ۱. جدول سطر به سطر

### اصول و نقش‌ها (DOC 14-54)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 16 | هر صفحه یک کار، متن کوتاه، عدد بزرگ | partial | `S/static/handover.js:1-15`, `S/static/handover.css:197` | ویزارد تحویل و رویداد یک‌سؤالی است؛ صفحهٔ خرید یک صفحهٔ چندبخشی است |
| 16 | آیکون برای هر قلم | missing | `S/static/` (فقط آیکون PWA) | هیچ آیکون قلم یا مرحله نیست |
| 17 | هیچ داده‌ای از شیفت‌های قبل دیده نشود | done | `S/api/routes_events.py:137-152`, `T/security/test_reference_number_leak.py` | فقط اقلام باز خود شخص می‌آید |
| 17 | عدد مرجع هرگز نمایش داده نشود | done | `S/services/handover.py:364`, `S/static/handover.js:34-41` | فقط واژهٔ وضعیت |
| 18 | ردپذیری، اصلاح = ثبت تازه | done | `S/adapters/db/sqlite/triggers.sql`, ADR-010 | trigger ضد UPDATE/DELETE |
| 19 | مدیر فقط تأیید/رد/تغییر کند | partial | `S/templates/manager_card.html`, `S/static/manager.js:55-74` | روی خود کارت فقط رویداد نگه‌داشته‌شده دکمه دارد؛ بقیه جاهای دیگر (ردیف تصمیم‌ها پایین) |
| 20 | حسابداری فقط خوانده می‌شود | done | `S/adapters/accounting/readonly.py` | `mode=ro` |
| 21 | شبکهٔ داخلی، بدون اینترنت | done | `S/api/routes_web.py:21-26` (CSP self)، ADR-015 | فونت و ریسورس محلی |
| 22 | گوشی شخصی | done | `S/api/routes_devices.py`, `S/services/devices.py` | جفت‌سازی QR |
| 23 | بدون جدول شیفت | done | `S/domain/shift_clock.py` | شیفت از ساعت حساب می‌شود |
| 24 | بدون بدهی بیمار و بدون تخفیف خارج از کمپین | done | `S/domain/shift_events/campaign_discount.py`, نبود کد «ط» | |
| 28 | هفت نقش، چندنقشی | done | `S/domain/permission_grants.py:21-135` | اتحاد مجوزها |
| 28 | هر کس فقط کار شیفت فعلی خودش | done | `S/domain/permission_grants.py`, `S/api/deps.py` | |
| 28 | افزودن کارمند و دادن نقش | missing | `S/adapters/db/staff_repo.py:61,90` (فقط تست) | یافتهٔ ۱ و ۲ |
| 32 | پذیرش: تحویل صندوق، قفسه، چک‌لیست | partial | `S/services/handover.py:276-470` | چک‌لیست همهٔ جریان‌ها یکی است |
| 32 | پذیرش: دریافت بار با عکس فاکتور | partial | `S/services/purchase.py:365`, `S/domain/purchase_payloads.py:60-100` | شمارش واحد و شمارهٔ فاکتور هست، عکس نیست |
| 32 | پذیرش: ارسال عکس رسید کارت‌به‌کارت | done | `S/domain/shift_events/card_transfer.py:40-56` | عکس اختیاری (ADR-020) |
| 32 | پذیرش: ابلاغ چک‌لیست به خدمه و تأیید | partial | `S/domain/shift_events/cleaning.py:54-70` | فقط تأیید؛ ابلاغ همان کار شفاهی است |
| 32 | پذیرش: ثبت بیمار برای پاداش | partial | ردیف 222 | ثبت در حسابداری است، نه شیفت |
| 32 | پذیرش قیمت و شیفت‌های قبل را نمی‌بیند | done | `S/domain/purchase.py`, `S/services/purchase.py:259` | state بریده‌شده با مجوز |
| 33 | پرستار: سرم و مصرفی CPR، ترالی | partial | `S/domain/handover_steps.py:11-19` | ترالی بدون «قلم شمارشی»؛ فقط در چک‌لیست مشترک |
| 33 | پرستار: ثبت قند و فشار و بازکردن مسیر پیگیری | partial | `S/domain/shift_events/vitals.py` | ثبت و دکمهٔ پیگیری هست؛ آستانه و پیشنهاد نیست |
| 34 | انباردار: پیش‌نویس سفارش | done | `S/services/purchase.py:338`, `S/static/purchase.js:631` | فهرست زیر حداقل (:604) |
| 34 | انباردار: تحویل بار از شیفت | partial | `S/services/purchase.py:389` | ثبت یک‌طرفه توسط تحویل‌دهنده؛ تأیید گیرنده نیست |
| 34 | انباردار: شارژ قفسه‌ها | partial | `S/services/purchase.py:449`, `S/domain/shift_events/restock.py:95` | دو مسیر موازی (یافتهٔ جدول ۳۰) |
| 34 | انباردار: شمارش هفتگی | partial | `S/services/purchase.py:476` | یافتهٔ ۵ |
| 34 | انباردار: بررسی انقضا | not wired | `S/services/purchase.py:509`, `S/domain/purchase_specs.py:53` | رویداد `expiry_flagged` نوشته می‌شود و هیچ‌کس نمی‌خواند |
| 34 | انباردار: تأیید بعدی برداشت اضطراری | done | `S/domain/shift_events/emergency_withdrawal.py:99` | `approvable_by=STOREKEEPER` |
| 34 | انباردار: مرجوعی به فروشنده | done | `S/services/purchase.py:428`, `S/static/purchase.js:1017` | |
| 34 | انباردار قیمت و صندوق را نمی‌بیند | done | `S/domain/permission_grants.py:60-78` | |
| 35 | مسئول خرید: سفارش داده شد | done | `S/services/purchase.py:357`, `S/static/purchase.js:585` | نقش نه شخص (Q-16) |
| 36 | حسابدار: دیجیتال کردن فاکتور از عکس | partial | `S/services/purchase.py:400` | ثبت خطوط هست؛ عکسی برای نگاه کردن وجود ندارد |
| 36 | حسابدار: ثبت پرداخت و رسید | partial | `S/domain/purchase_payloads.py:118-125` | روش و تاریخ و مبلغ هست؛ عکس رسید نیست، فقط متن مرجع |
| 36 | حسابدار: تأیید کارت‌به‌کارت | done | `S/templates/accountant_queue.html:22-32` | |
| 36 | حسابدار: بستانکاری مرجوعی | done | `S/services/purchase.py:438` | |
| 36 | حسابدار: محاسبهٔ پاداش ماهانه | done | `S/templates/accountant_queue.html:84` | |
| 36 | حسابدار شمارش و عدد مرجع نبیند | partial | `S/domain/permission_grants.py:96-110` | مجوز `HANDOVER_CASH_SUBMIT`، `HANDOVER_SIGN`، `STOCK_COUNT_SUBMIT` را دارد؛ گسترده‌تر از سند |
| 37 | پزشک شیفت: نفر دوم برداشت اضطراری | partial | `S/domain/shift_events/emergency_withdrawal.py:47-63` | نفر دوم هر کارمند فعال است، پزشک الزامی نیست |
| 37 | پزشک: تأیید ابطال | done | `S/domain/shift_events/refund.py:56` | |
| 38 | مدیر: کارت روزانه | done | `S/services/daily_card.py:119-138`, `S/api/routes_manager.py:182` | |
| 38 | مدیر: تأیید/رد/تغییر | partial | `S/static/purchase.js:515-543`, `S/templates/manager_rewards.html` | دکمه‌ها در `/purchase`، `/events`، `/manager/rewards` است نه روی کارت |
| 38 | مدیر: تنظیمات اقلام و نقش‌ها | partial | ردیف‌های 253-270 | |
| 38 | مدیر: ارجاع تعمیرات | done | `S/domain/shift_events/equipment.py:140-157` | از ویزارد `/events` |
| 40 | خدمه بدون اپ | done | `S/domain/shift_events/cleaning.py` | |
| 44 | تحویل صندوق و قفسه، و بسته‌های بار پذیرش به پذیرش | done | `S/services/handover.py:429`, `S/api/routes_handover.py:395` | شناسهٔ واقعی P-12 |
| 45 | تحویل پرستار به پرستار | done | `S/domain/handover_fsm.py:42-45` | جریان nursing |
| 46 | پذیرش ← انباردار با تأیید دو طرف | partial | `S/services/purchase.py:389-398` | یک ثبت، نه دو تأیید |
| 47 | شارژ قفسه با تأیید مسئول شیفت | partial | `S/services/purchase.py:449-475` | `confirmer_id` فقط نام است؛ خود او تأیید نمی‌کند |
| 48 | انباردار ← مدیر ← مسئول خرید | done | `S/services/purchase.py:108-122` | |
| 49 | پذیرش ← حسابدار عکس فاکتور و رسید | partial | همان 32 | عکس رسید کارت‌به‌کارت هست؛ عکس فاکتور نیست |
| 50 | هر مغایرت و تصمیم باز در کارت | partial | `S/services/daily_card.py:119-138` | پاداش‌ها و خرید هست؛ بررسی «نمونهٔ تماس» نیست |
| 54 | یک مسئول شیفت؛ پذیرش دوم فقط رویداد | partial | `T/integration/test_handover_api.py:266`, `S/services/handover.py:364-410` | شروع دوم رد می‌شود ولی شمارش/پیشروی/چک‌لیست با هر پذیرش مجاز است |

### تحویل شیفت (DOC 56-121)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 58 | شمارش کور | done | `S/domain/handover_counts.py`, `S/services/handover.py:364` | |
| 64 | E = C0+R−D−U−W | done | `S/domain/handover_formulas.py:15` | |
| 68-72 | منبع C0، R، D، U، W | partial | `S/domain/effects.py`, `S/services/accounting_bridge.py` | برگشت از قفسه به انبار (U) مسیر ندارد (`purchase.py:455` فقط از انبار به قفسه) |
| 74-80 | N = N0+P−B−W | partial | `S/domain/handover_formulas.py:23`, `S/domain/events.py:93` | واریز به حساب: رویداد هست، صفحه نیست (no UI) |
| 82 | فقط واحد شمارش | done | `S/domain/purchase_payloads.py` (`stock_factor`) | |
| 84 | مصرفی‌های جدا از خدمت عدد مرجع دارند | done | `tools/seed_reference_data.py` (ژیلت، چسب، گوشواره، بخیه) | کد کالا را مدیر باید وارد کند |
| 88 | تحویل با صورت‌حساب باز شروع نشود | done | `S/services/handover.py:301-306`, `S/adapters/db/handover_repo.py:261` | |
| 92-96 | بیمار حین تحویل روی کاغذ، بدون پول | done | `S/services/late_cash.py`, `S/templates/manager_card.html` (بخش نقد ثبت‌شده در تحویل) | روال دستی؛ اپ استثنا را می‌نویسد |
| 100 | صندوق دونفره کور | done | `S/domain/handover_counts.py:87`, `S/api/routes_handover.py:328` | |
| 101 | قفسه‌ها به ترتیب چیدمان | contradicts | `S/services/catalog.py:219` | مرتب بر `item_id` لاتین؛ ستون ترتیب ندارد |
| 102 | چک‌لیست‌ها بله/خیر، عدد، عکس | partial | `S/api/routes_handover.py:373`, `S/static/handover.js:432` | عکس: نه UI (`photo_id` بی‌مصرف)؛ همهٔ چک‌لیست‌ها در هر دو جریان |
| 102 | پاسخ چک‌لیست به مدیر برسد | not wired | `S/services/handover.py:426`, `S/services/daily_card.py` | هیچ خواننده‌ای نیست |
| 103 | بسته‌های بار از شیفت به شیفت | done | `S/api/routes_handover.py:395-415` | |
| 104 | امضای دو طرف | done | `S/services/handover.py:447,813` | |
| 108-111 | زنجیرهٔ دوباره بشمار، خروجی کور، شمارش مشترک | done | `S/domain/handover_counts.py`, `S/static/handover.js:34-41` | |
| 111 | مغایرت با نام خروجی در کارت مدیر | done | `S/templates/manager_card.html` بخش مغایرت‌ها | |
| 113 | کسری فقط هشدار | done | `S/domain/rewards.py` (بدون کسر)، `T/property/test_reward_ledger.py` | |
| 113 | ثبت دیرهنگام بدون برچسب خودکار | done | `S/services/held_events.py` | مدیر تصمیم می‌گیرد |
| 115 | اقلام غیرشیفتی بدون نام مقصر | done | `S/services/purchase.py:497-505` | `culprit_id=None` |
| 119 | خروج زودهنگام: فقط اطلاع به مدیر | done | `S/services/handover.py:464`, `S/templates/manager_card.html` | (ADR-017 مستثنا) |
| 120 | بلوک: تحویل به خود نشود، فقط ابتدا و انتها | partial | `S/domain/handover_fsm.py:85` | رد می‌شود ولی «بلوک» مفهوم ندارد؛ نفر بلوک باید خود به خود تحویل شروع نکند و پیام راهنما ندارد |
| 121 | محرمانگی در بلوک | done | `S/api/routes_events.py:137` | |

### رویدادهای حین شیفت (DOC 125-154)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 125 | دکمهٔ بزرگ و سؤال‌های یکی‌یکی | done | `S/templates/shift_events.html`, `S/static/shift-events.js` | |
| 125 | رویداد بسته‌شده برای کارمند دیده نشود | done | `S/services/shift_events.py` (`pending_for`, `open_targets`) | فقط اقلام باز |
| 129 ک | کارت‌به‌کارت | done | `S/domain/shift_events/card_transfer.py:40-56`, `S/templates/accountant_queue.html:22` | تا تأیید در کارت «در انتظار» |
| 130 ب | برگشت پول/ابطال | done | `S/domain/shift_events/refund.py:42-56` | ابطال را از `v_shift_voids_v1` می‌خواند |
| 131 ت | تخفیف کمپین | done | `S/domain/shift_events/campaign_discount.py:34-51` | |
| 132 د | برداشت از صندوق | done | `S/domain/shift_events/cash_withdrawal.py:113-130` | (ADR-019 مستثنا) |
| 133 ا | اختلاف موقع تحویل، تأیید خروجی | partial | `S/services/handover.py:388-400` | خودکار هست؛ «تأیید دوم خروجی» جدا نیست (همان شمارش مشترک) |
| 134 ض | ضایعات و مقصر و عکس | done | `S/domain/shift_events/waste.py:108-121` | ثبت‌کننده هم تأییدکننده نیست |
| 135 و | ورودی وسط شیفت | done | `S/domain/shift_events/restock.py:95-107` | |
| 136 م | اقلام اضافه‌ی بیمار | done | `S/domain/shift_events/patient_items.py:56-86` | |
| 138 | کد «ط» حذف | done | نبود در `S/domain/shift_events/` | |
| 142 | کارت‌به‌کارت در کارت مدیر «در انتظار» | done | `S/services/daily_card.py` (`open_events`) | |
| 143 | برگشت کالا: ابطال را اپ بخواند | done | `S/services/accounting_bridge.py` (VOIDS, reversal) | |
| 144 | سقف برداشت ۳۰۰٬۰۰۰ + عکس | done | `S/adapters/db/migrations/m0004_settings_and_catalog.py:80`, ADR-019 | |
| 145 | تخفیف فقط کمپین، با نام، خدمات، مقدار، تاریخ | partial | `S/templates/manager_campaigns.html:54-72` | تاریخ‌ها میلادی (`type=date`)؛ سند جلالی می‌خواهد |
| 146 | مقصر ضایعات تأییدکنندهٔ دوم نباشد | done | `S/domain/shift_events/waste.py:60-80` | |
| 147 | امانت بیمار بیرون از شمارش | done | `S/domain/shift_events/patient_items.py` | |
| 151 | برداشت اضطراری دونفره + پاکت پلمب + تأیید انباردار | partial | `S/domain/shift_events/emergency_withdrawal.py:88-103` | دلیل/نفر دوم/تأیید هست؛ ثبت پلمب تازهٔ پاکت نیست |
| 152 | خرابی با عکس، ارجاع با مهلت، «تعمیر شد» | partial | `S/domain/shift_events/equipment.py:121-179` | ارجاع‌های مهلت‌گذشته جدا برجسته نمی‌شوند (`daily_card.py` بدون `due_on`) |
| 153 | قند و فشار ثبت مستقیم | done | `S/domain/shift_events/vitals.py:60-76` | |
| 153 | بالای آستانه پیشنهاد مسیر پیگیری | contradicts | `S/domain/shift_events/vitals.py:1-8`, ADR-020 بند ۶ | تصمیم master (نه مالک): آستانه و پیشنهاد نیست |
| 154 | خدمه: ابلاغ و تأیید مورد به مورد | partial | `S/domain/shift_events/cleaning.py:54-70` | برای محل بی‌قلم (فضای عمومی/اتاق پزشک) قلمی نیست که تیک بخورد |

### خرید و انبار (DOC 158-187)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 162 | پیش‌نویس با پیشنهاد زیر حداقل | done | `S/services/purchase.py:604` | حداقل را مدیر باید پر کند |
| 163 | مدیر: تأیید/رد/اصلاح | done | `S/static/purchase.js:515-543`, `S/services/purchase.py:349` | سه دکمه در `/purchase` |
| 164 | مسئول خرید: سفارش داده شد | done | `S/services/purchase.py:357` | |
| 165 | پذیرش: عکس فاکتور | missing | `S/domain/purchase_payloads.py:60-100` | فیلد عکس نیست |
| 165 | پذیرش: شمارش و تطبیق با فاکتور | partial | `S/services/purchase.py:365-388` | با شمارهٔ فاکتور؛ خطوط فاکتور هنگام دریافت نیست تا مقایسه شود، فقط «برچسب مشکل» |
| 165 | پذیرش قیمت وارد نکند | done | `S/domain/purchase_payloads.py:68` | |
| 166 | دست‌به‌دست بسته در تحویل | done | `S/api/routes_handover.py:395` | |
| 167 | انباردار تحویل می‌گیرد، دو طرف تأیید | partial | `S/services/purchase.py:389` | یک ثبت |
| 168 | حسابدار فاکتور را از عکس دیجیتال کند | partial | `S/services/purchase.py:400` | بدون عکس |
| 172 | بار مشکل‌دار با برچسب و قفل پرداخت | done | `S/services/purchase.py:374,409` | |
| 173 | مرجوعی وصل به فاکتور، بستانکاری | done | `S/services/purchase.py:428,438` | |
| 177 | شارژ قفسه با شمارش و تأیید همان لحظه | partial | `S/services/purchase.py:449` | یافتهٔ دو مسیر |
| 177 | از همان لحظه عدد قفسه+ و انبار− | partial | `S/domain/purchase_specs.py:21-33` | فقط مسیر `restock.record`؛ ورودی «و» با منبع انبار فقط قفسه را زیاد می‌کند (`restock.py`) |
| 179 | هر کد کالا فقط یک محل | done | `S/services/catalog.py:399` | |
| 183 | شمارش هفتگی شنبه تا دوشنبه | done | `S/domain/stock_checks.py:53-72` | |
| 183 | شمارش هفتگی کور کامل انبار | partial | `S/domain/stock_checks.py:66`, `S/services/purchase.py:476` | هر یک قلم هفته را «انجام‌شده» می‌کند |
| 184 | هشدار کارت اگر تا آخر دوشنبه نشد | done | `S/services/daily_card.py:1527-1545` | `S/services/scheduler.py:69` فقط stub لاگ است |
| 185 | علامت نزدیک انقضا | not wired | `S/services/purchase.py:509` | هیچ صفحه/کارتی نشانش نمی‌دهد |
| 186 | مغایرت انبار اصلی بی‌نام | contradicts | `S/domain/purchase_specs.py:41-53` | قاعده درست است ولی مرجع انبار اصلی غلط است: ورود فروشنده انبار را زیاد نمی‌کند، شمارش اولیه نیست |
| 187 | شمارش تأییدی دوره‌ای حسابدار | missing | `S/services/purchase.py:121` | مجوز هست؛ «نمونه چند قلم» نیست و شمارش حسابدار هفتهٔ انباردار را می‌بندد |

### کار حسابدار (DOC 189-212)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 195 | هر قلم با نرخ، تعداد، جمع (از عکس) | partial | `S/services/purchase.py:400`, `S/static/purchase.js:944` | جمع را سرور حساب می‌کند؛ بدون عکس |
| 196 | وضعیت پرداخت، هر پرداخت، عکس رسید | partial | `S/domain/purchase_payloads.py:118-125` | عکس رسید نیست |
| 197 | تاریخچهٔ قیمت هر قلم | missing | `S/services/purchase.py:710-731` | قیمت‌ها در رویداد هست، هیچ نمایی ندارد |
| 198 | فاکتور بار مشکل‌دار قابل پرداخت نیست | done | `S/services/purchase.py:409-427` | |
| 199 | فاکتور کاغذی قدیمی وارد نشود | done | نبود import | |
| 203 | مرجوعی بستانکاری روی فاکتور اصلی | done | `S/services/purchase.py:438` | |
| 207 | تطبیق کارت‌به‌کارت، رد به کارت مدیر | done | `S/templates/accountant_queue.html:22-32` | |
| 208 | برداشت‌های صندوق در صف | done | `S/templates/accountant_queue.html:42-54` | |
| 212 | پاداش ماهانه برای فیش | done | `S/templates/accountant_queue.html:84`, `S/services/rewards.py:384` | |

### پاداش و رقابت (DOC 214-236)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 220 | قند و فشار خودکار از حسابداری | done | `S/adapters/db/reward_repo.py:30-41`, `S/templates/manager_rewards.html` | نیاز به نگاشت کاربر |
| 221 | بازکردن مسیر پیگیری پاداش | missing | `S/domain/rewards.py:26-28` (فقط ۳ نوع)، S01 P-07 | سامانهٔ پیگیری در این مخزن نیست |
| 222 | کد ملی با الگوریتم | done | `apps/accounting/.../m0004_shift_contract.py:23-30`, `S/adapters/db/reward_repo.py:43-50` | در view حسابداری |
| 222 | موبایل تکراری رد شود | missing | `m0004_shift_contract.py:33` (فقط فرمت)، `S/domain/rewards.py:31` (کامنت «unique» بی‌پشتوانه) | یک موبایل برای چند بیمار پاداش تکراری می‌دهد |
| 222 | نمونهٔ تصادفی تماس روی کارت | missing | `S/services/daily_card.py:119-138` | بخشی با این نام نیست |
| 223 | کار ارجاعی مدیر، تأیید بستن | no UI | `S/domain/shift_events/rewards.py:178-218` (`phone=False`)، `S/templates/manager_rewards.html` | رویداد `reward.confirmed` فقط با API خام؛ فرم اعتبار دادن نیست |
| 227 | ماه تأیید | done | `S/domain/rewards.py:75-87` | |
| 228 | ابطال از فیش ماه بعد | done | `S/domain/rewards.py:90-93` | |
| 229 | کسری از پاداش کم نمی‌شود | done | `S/domain/rewards.py:1-14` | |
| 233 | هر کس فقط رتبهٔ خودش | done | `S/templates/rewards_mine.html`, `S/domain/rewards.py:166-195` | |
| 233 | هدف تیمی | partial | `S/domain/rewards.py:176-195` | فقط جمع کارهای تأییدشده؛ «عدد هدف» ماهانه تنظیم نمی‌شود |
| 234 | مبلغ این ماه خودش | done | `S/templates/rewards_mine.html:30-34` | |
| 235 | مدیر جدول کامل | done | `S/templates/manager_rewards.html:70-97` | صفحه بدون لینک (یافتهٔ ۳) |
| 216 | مبلغ هر کار را مدیر تعیین کند | done | `S/api/routes_rewards.py:124`, `S/templates/manager_rewards.html:101` | ماه‌به‌ماه |

### داشبورد مدیر (DOC 237-270)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 239 | کارت ساعت ۷:۳۰، جمعه ندارد | done | `S/domain/daily_card.py`, ADR-018 | |
| 243 | تصمیم‌های باز با دکمهٔ تأیید/رد/تغییر | partial | `S/templates/manager_card.html`, `S/static/manager.js:55-74` | کارت بدون دکمه جز «آزادسازی/رد»؛ سفارش‌ها، بار مشکل‌دار، برداشت بالای سقف و پاداش فقط خوانده می‌شوند |
| 243 | تصمیم: مغایرت‌های مربوط به هم | partial | `S/templates/manager_card.html` | دو مغایرت هر دو ثبت می‌شود؛ جفت کردن/دکمه نیست |
| 244 | مغایرت‌ها با نام یا «بدون مقصر» | done | `S/templates/manager_card.html` | |
| 245 | رویدادهای ۲۴ ساعت، تعداد هر کد | partial | `S/services/daily_card.py` (`shift_events`) | فهرست ردیف‌به‌ردیف است نه تعداد هر کد |
| 246 | خرابی تازه و ارجاع‌های مهلت‌گذشته | partial | `S/services/daily_card.py` (`open_events`) | مهلت‌گذشته جدا نیست |
| 247 | کارهای انجام‌نشده (هفتگی، کارت‌به‌کارت، بار، اضطراری) | done | `S/services/daily_card.py:119-138` | |
| 248 | نمونهٔ تماس | missing | همان | |
| 249 | اقلام زیر حداقل | done | `S/services/daily_card.py` (`below_minimum`) | |
| 253 | افزودن/حذف/تغییر قلم بدون برنامه‌نویس | no UI | `S/api/routes_catalog.py:176`, `S/templates/manager_items.html:28-33` | API کامل؛ صفحه فقط حداقل و کد را ویرایش می‌کند |
| 255 | ویژگی نوع | no UI | `S/api/routes_catalog.py:34-44` | |
| 255 | ویژگی محل | no UI | همان | محل‌ها در `tools/seed_reference_data.py:56-63` |
| 255 | ویژگی نقش مسئول | missing | `S/services/catalog.py:271` | ستون و فیلد ندارد |
| 255 | ویژگی تناوب | missing | همان | |
| 255 | واحد، حداقل، کد کالا | partial | `S/templates/manager_items.html` | حداقل و کد ویرایش‌شدنی؛ واحد no UI |
| 255 | چک‌لیست‌های فضا (چراغ‌ها، اتاق پزشک) | no UI | `tools/seed_reference_data.py:56-63`, `S/domain/shift_events/cleaning.py` | محل هست، قلمی برایش نیست و ساخت قلم صفحه ندارد |
| 265 | نقش‌ها و کارکنان: افزودن، نقش، غیرفعال | missing | یافتهٔ ۱ و ۲ | |
| 266 | سقف برداشت | done | `S/domain/policy.py:23-30`, `S/templates/manager_settings.html` | |
| 266 | آستانهٔ قند و فشار | missing | `S/domain/policy.py:23-30` | کلید تنظیمات نیست |
| 266 | تنخواه ثابت صندوق | missing | همان | |
| 267 | کمپین‌ها | done | `S/api/routes_campaigns.py:136`, `S/templates/manager_campaigns.html` | |
| 268 | نرخ پاداش | done | `S/api/routes_rewards.py:124` | |
| 268 | هدف تیمی هر ماه | missing | `S/domain/rewards.py:176-195` | |
| 270 | تغییر با تاریخ ثبت و از شیفت بعد | done | `S/services/settings.py:111-135`, `S/services/catalog.py:350-390` | |

### راه‌اندازی و پیوست (DOC 272-389)

| سطر | قاعده | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 276 | مدیر فهرست اقلام را با حداقل و کد کامل می‌کند | partial | `S/templates/manager_items.html`, `S/api/routes_manager.py:315` | بدون افزودن؛ کد/حداقل بله |
| 276 | کارکنان و نقش‌ها تعریف شوند | missing | یافتهٔ ۱ | |
| 277 | انباردار انبار اصلی را کامل می‌شمارد | missing | `S/domain/handover_steps.py:11-19`, `S/api/routes_manager.py:224-300` | baseline فقط پذیرش و CPR |
| 278 | پذیرش قفسه+صندوق و پرستار CPR می‌شمارند | partial | `S/api/routes_manager.py:224-312` | مدیر روی گوشی خودش می‌شمارد و نگه‌دار را انتخاب می‌کند |
| 279 | از شیفت بعد شمارش کور | done | `S/services/handover.py:198-262` | |
| 280 | چند روز اول مغایرت فقط یادگیری | missing | (grep «learning» بی‌نتیجه) | |
| 284-388 | ۶۹ قلم و ۱۴ مورد ترالی | done | `tools/seed_reference_data.py:66-` | ترتیب و نام مطابق سند؛ حداقل و کد خالی عمداً |
| 284 | واحدهای CPR باید چک شوند | partial | `tools/seed_reference_data.py` | چک‌شدنش با مالک است |
| 389 | چک‌لیست فضا را مدیر بسازد | no UI | همان 255 | |

### ۳۱ ردیف جدول تصمیم‌ها (DOC 395-427)

| # | تصمیم | وضعیت | شاهد | توضیح |
|---|---|---|---|---|
| 1 | خروجی همه را می‌بندد؛ بیمار کاغذی | done | `S/services/handover.py:301` | |
| 2 | مصرفی‌ها عدد مرجع دارند | done | seed (ژیلت و ...) | کد کالا مانده |
| 3 | زنجیرهٔ خرید | done | `S/services/purchase.py:108-122` | بدون عکس فاکتور |
| 4 | فقط واحد شمارش | done | `S/domain/purchase_payloads.py` | |
| 5 | خروج زودهنگام بدون مهلت | done | `S/services/handover.py:464` | (ADR-017) |
| 6 | یک مسئول شیفت | partial | `S/services/handover.py:304` | بسته‌نبودن شمارش |
| 7 | جدول شیفت نیست | done | — | |
| 8 | شبکهٔ داخلی | done | ADR-015 | |
| 9 | ثبت دیرهنگام بدون برچسب | done | `S/services/held_events.py` | |
| 10 | تخفیف فقط کمپین | done | `S/domain/shift_events/campaign_discount.py` | |
| 11 | سقف برداشت ۳۰۰٬۰۰۰ | done | m0004 | (ADR-019) |
| 12 | برداشت اضطراری انبار | partial | `S/domain/shift_events/emergency_withdrawal.py` | پلمب تازه ثبت نمی‌شود |
| 13 | بار مشکل‌دار، قفل پرداخت | done | `S/services/purchase.py:374,409` | تصمیم مدیر روی `/events`، نه کارت |
| 14 | مرجوعی به فروشنده | done | `S/services/purchase.py:428,438` | |
| 15 | برگشت کالای بیمار | done | `S/services/accounting_bridge.py` | |
| 16 | کارت‌به‌کارت | done | `S/domain/shift_events/card_transfer.py` | |
| 17 | قند و فشار، پاداش، مسیر پیگیری با یک دکمه | partial | `S/domain/shift_events/vitals.py` | آستانه/پیشنهاد نیست (contradicts در ردیف 153) |
| 18 | ثبت بیمار: چک کد ملی، رد موبایل تکراری، نمونهٔ تماس | partial | `S/adapters/db/reward_repo.py:43` | کد ملی بله؛ دو مورد دیگر missing |
| 19 | کسری فقط هشدار | done | `S/domain/rewards.py` | |
| 20 | بدهی بیمار وجود ندارد | done | — | |
| 21 | گوشی شخصی | done | `S/services/devices.py` | |
| 22 | رتبهٔ خود + هدف تیمی | partial | `S/templates/rewards_mine.html` | عدد هدف نیست |
| 23 | خرابی تجهیزات با مهلت | partial | `S/domain/shift_events/equipment.py` | مهلت‌گذشته جدا نیست |
| 24 | خدمه بدون اپ | done | `S/domain/shift_events/cleaning.py` | |
| 25 | مهر لازم نیست | done | — | |
| 26 | شمارش هفتگی شنبه تا دوشنبه | partial | `S/domain/stock_checks.py` | یک قلم هفته را می‌بندد؛ مرجع انبار غلط |
| 27 | ماه پاداش و کسر بعدی | done | `S/domain/rewards.py` | |
| 28 | مقصر ضایعات تأییدکننده نباشد | done | `S/domain/shift_events/waste.py` | |
| 29 | اقلام غیرشیفتی بی‌نام | done | `S/services/purchase.py:497` | |
| 30 | امانت بیمار | done | `S/domain/shift_events/patient_items.py` | |
| 31 | هر کد کالا یک محل | done | `S/services/catalog.py:399` | |

## ۲. شکاف‌ها به ترتیب آسیب به یک روز واقعی شیفت

مقیاس تلاش: **small** حدود یک‌روز یا کمتر، **medium** چند روز، **large** بیش از یک هفته. هر گروه جداگانه قابل‌برداشت است.

### گروه A — روز اول نصب کار نمی‌کند (آسیب: بسته‌کنندهٔ کار)

**A1. مدیریت کارمند و نقش (large→medium).** ساخت کارمند (نام، موبایل، PIN)، دادن/گرفتن نقش، غیرفعال‌کردن؛ و یکی‌کردن منبع نقش. در `login` و `Principal` نقش از `staff_role` می‌آید، اما API نقش در `catalog_staff_role_versions` می‌نویسد. پیشنهاد: یا `auth._principal` نقش مؤثر را از catalog (با `running_shift`) بخواند، یا `set_role` هر دو را به‌روز کند؛ و یک صفحهٔ `/manager/staff` (مجوز `STAFF_MANAGE` از قبل هست) بسازد. شامل راه‌اندازی مدیر اول (فرمان نصب).
فایل‌ها: `S/adapters/db/staff_repo.py`, `S/services/auth.py` (`directory`, `_principal`), `S/services/catalog.py` (`set_role`, `roles_of`), `S/api/routes_auth.py`, `S/api/routes_catalog.py`, قالب و JS جدید، `tools/` (ابزار مدیر اول)، مهاجرت افزایشی در صورت نیاز.

**A2. لینک‌های گم‌شده (small).** `/handover` در خانه برای دارندگان `HANDOVER_COUNT_SUBMIT`؛ `/manager/rewards`، `/manager/campaigns`، `/admin/devices`، `/manager/staff` در ناوبری مدیر (همهٔ صفحه‌های `mg-nav`).
فایل‌ها: `S/templates/home.html`, `S/api/routes_web.py` (پرچم `show_handover_link`)، قالب‌های `manager_*.html`, `rewards_mine.html`.

**A3. چک‌لیست هر جریان، نقش مسئول، تناوب، ترتیب قفسه (medium).** افزودن به قلم: `responsible_role`، `frequency` (هر شیفت/روزانه/هفتگی) و `sort_order`؛ فیلتر چک‌لیست هر جریان بر اساس محل/نقش؛ ترتیب اقلام تحویل بر `sort_order`. ویرایش از صفحهٔ اقلام (A4).
فایل‌ها: مهاجرت `m0014_*` (افزایشی)، `S/adapters/db/catalog_repo.py`, `S/services/catalog.py`, `S/api/routes_catalog.py`, `S/api/routes_handover.py:294-325`, `S/static/handover.js:432`, `S/domain/handover_steps.py`.

**A4. صفحهٔ کامل اقلام و چک‌لیست‌ها (medium).** افزودن، بازنشسته‌کردن، تغییر نام/نوع/محل/واحد، و ساخت چک‌لیست فضا (محل «فضای عمومی»، «اتاق پزشک») و ردیف‌های بله/خیر، عدد خواندنی، عکس.
فایل‌ها: `S/templates/manager_items.html`, `S/static/manager-items.js`, `S/api/routes_manager.py:315`, API موجود `PUT /api/v1/catalog/items/{id}`، `S/domain/shift_events/cleaning.py` (برای قلم‌های فضا).

**A5. بستن شمارش و پیشروی به دو نفر جریان (small).** `count`, `joint`, `advance`, `answer_checklist`, `hand_parcel`, `report_early_leave` فقط برای `incoming_id` یا `outgoing_id` همان جریان (مثل `_signer_of`)؛ و سومین شمارش فقط برای خروجی. تست مشخصه‌نگاری برای پذیرش دوم (H-10).
فایل‌ها: `S/services/handover.py:364-475,813`, `S/api/routes_handover.py:328-445`, `T/integration/test_handover_api.py`.

### گروه B — اشتباه‌های پول و موجودی

**B1. انبار اصلی (medium→large).** شمارش اولیهٔ انبار اصلی در baseline، افزودن به انبار هنگام «انباردار بار را تحویل می‌گیرد»، شمارش هفتگی همهٔ اقلام (نه یک قلم) برای بسته‌شدن هفته، علامت انقضا روی کارت یا صفحهٔ خرید، و برگشت از قفسه به انبار (U). همچنین یکی‌کردن مسیر «و» (منبع انبار) و `restock.record`.
فایل‌ها: `S/domain/purchase_specs.py`, `S/services/purchase.py:365-520`, `S/domain/stock_checks.py:53`, `S/api/routes_manager.py:224-312`, `S/domain/shift_events/restock.py`, `S/services/daily_card.py` (بخش انقضا)، `S/static/purchase.js:1170`. نیاز به پاسخ سؤال ۳ مالک.

**B2. واریز نقد به حساب (small→medium).** دکمه یا فرم برای `cash.deposited` (مجوز `CASH_DEPOSIT_RECORD` را حسابدار و مدیر دارند) با عکس فیش اختیاری و مسیر در صف حسابدار.
فایل‌ها: `S/domain/shift_events/` (kind تازه)، `S/domain/events.py:93`, `S/services/accountant_queue.py`, `S/templates/accountant_queue.html`.

**B3. پاسخ چک‌لیست به کارت مدیر (small).** پاسخ «خیر» یا فشار کپسول خارج از حد به کارت برسد (بخش `checklist_failures`)؛ نیازمند حدّ فشار (سؤال ۱).
فایل‌ها: `S/services/daily_card.py`, `S/domain/daily_card.py`, `S/templates/manager_card.html`, `S/services/handover.py:413`.

### گروه C — تصمیم روی خود کارت

**C1. دکمه‌های تأیید/رد/تغییر روی کارت (medium).** برای سفارش‌ها (`order.decide`)، بار مشکل‌دار، برداشت بالای سقف، پاداش در انتظار و ارجاع خرابی؛ بخش «نمونهٔ تماس» (ثبت‌های بیمار تصادفی)؛ برجسته‌کردن ارجاع‌های مهلت‌گذشته؛ شمار هر کد در ۲۴ ساعت.
فایل‌ها: `S/templates/manager_card.html`, `S/static/manager.js`, `S/services/daily_card.py`, `S/domain/daily_card.py` (CARD_SCHEMA_VERSION بالا برود)، `S/services/purchase.py:349`, `S/services/shift_events.py`.

### گروه D — پاداش

**D1. موبایل تکراری و نمونهٔ تماس (small→medium).** رد پاداش ثبت بیمار با موبایل تکراری (در view حسابداری یا در `reward_repo._REGISTRATION_WORKS`)، و فهرست نمونهٔ تصادفی برای تماس.
فایل‌ها: `S/adapters/db/reward_repo.py:43-50`, `apps/accounting/src/adapters/sqlite/migrations/m0004_shift_contract.py`, `S/services/rewards.py`, `S/services/daily_card.py`.

**D2. اعتبار کار ارجاعی مدیر و هدف تیمی (small).** فرم «اعتبار کار ارجاعی» در `/manager/rewards` (kind `reward.confirmed` آماده است)، و تنظیم عدد هدف ماهانه در کنار نرخ‌ها؛ نمایش «پیشرفت به‌سمت هدف».
فایل‌ها: `S/api/routes_rewards.py`, `S/templates/manager_rewards.html`, `S/templates/rewards_mine.html`, `S/domain/rewards.py:166-195`, `S/services/rewards.py`.

### گروه E — تنظیمات

**E1. آستانهٔ قند و فشار + پیشنهاد مسیر پیگیری + تنخواه صندوق (small)**، به شرط جواب مالک. کلید جدید `SettingKey` + `SETTING_SPECS`، نمایش در `/manager/settings`، و پیشنهاد در ویزارد.
فایل‌ها: `S/domain/policy.py:23-52`, `S/templates/manager_settings.html`, `S/domain/shift_events/vitals.py`, `S/static/shift-events.js`.

**E2. تاریخ جلالی در کمپین (small).** `S/templates/manager_campaigns.html:68-72`, `S/api/routes_campaigns.py`.

### گروه F — عکس و قیمت در زنجیرهٔ خرید

**F1. عکس فاکتور هنگام دریافت و عکس رسید پرداخت (medium).** `photo_sha256` افزایشی روی `DeliveryReceived` و `PaymentRecorded`، دوربین در `/purchase`، نمایش برای حسابدار هنگام دیجیتال‌کردن.
**F2. تاریخچهٔ قیمت هر قلم (small→medium).** نمایی از `invoice_digitized` برای هر قلم و فروشنده در بخش حسابدار.
فایل‌ها: `S/domain/purchase_payloads.py`, `S/services/purchase.py:365,409,710`, `S/static/purchase.js`, `S/api/routes_photos.py`, `S/services/photos.py`.

### گروه G — آموزش و کاربری

**G1. حالت یادگیری روزهای اول (small).** تنظیم «مغایرت‌ها تا تاریخ X فقط یادگیری»: برچسب روی کارت، بدون اثر بر پاداش. فایل‌ها: `S/domain/policy.py`, `S/services/daily_card.py`, `S/templates/manager_card.html`.
**G2. آیکون قلم و مرحله (small→medium).** `S/static/handover.js`, `S/static/shift-events.js`, `S/static/*.css`.
**G3. تأیید تحویل بار به انباردار (small).** تأیید گیرنده در `delivery.transfer` (`S/services/purchase.py:389`) و شارژ قفسه با تأیید `confirmer_id` (:449).

## ۳. سؤال‌هایی که فقط مالک می‌تواند جواب بدهد

۱. **آستانهٔ قند و فشار چند است؟** (قند بالاتر از چه عددی؟ فشار سیستول/دیاستول بالاتر از چه عددی؟ و فشار کپسول اکسیژن کمتر از چه عددی خطر است؟) *توصیه:* پزشک شیفت عدد بدهد؛ تا آن زمان اپ فقط ثبت کند و پیشنهاد ندهد.
۲. **تنخواه ثابت صندوق چقدر است و برای چه کاری باشد؟** *توصیه:* فقط یک عدد در تنظیمات که کارت مدیر کنار شمارش صندوق به‌عنوان یادآور نشان دهد، نه محاسبه.
۳. **کالایی که فروشنده می‌آورد، وقتی انباردار تحویل گرفت خودکار به موجودی انبار اصلی اضافه شود؟** *توصیه:* بله، با شمارش انباردار هنگام تحویل؛ و شمارش اولیهٔ انبار اصلی را انباردار پیش از شروع بزند.
۴. **شمارش هفتگی همهٔ اقلام انبار باشد یا بخشی؟ و نمونهٔ حسابدار چند قلم؟** *توصیه:* هفتگی همهٔ اقلام (هفته با یک قلم بسته نشود)، و حسابدار ماهی یک‌بار ۵ قلم تصادفی.
۵. **واریز نقد به حساب را چه کسی و با چه مدرکی ثبت می‌کند؟** *توصیه:* حسابدار، با عکس فیش اختیاری.
۶. **«بازکردن مسیر پیگیری» پاداش دارد، ولی سامانهٔ پیگیری در این پروژه نیست. فعلاً چه کنیم؟** *توصیه:* فعلاً مدیر دستی هر ماه تأیید کند؛ اتصال خودکار بعد از آمدن سامانهٔ پیگیری.
۷. **موبایل تکراری را کجا رد کنیم؟** (ثبت بیمار در حسابداری انجام می‌شود.) *توصیه:* حسابداری فقط هشدار بدهد، و پاداش برای یک موبایل فقط یک‌بار حساب شود.
۸. **ثبت عکس فاکتور هنگام تحویل بار اجباری باشد؟** *توصیه:* اجباری برای دریافت بار، اختیاری برای رسید پرداخت.
۹. **«هدف تیمی» عددی است که مدیر هر ماه می‌گذارد (مثلاً ۲۰۰ ثبت بیمار)؟** *توصیه:* بله، یک عدد در کنار نرخ پاداش هر ماه.
۱۰. **حالت یادگیری چند روز باشد؟** *توصیه:* ۷ روز اول پس از شمارش اولیه.
۱۱. **چک‌لیست ترالی فقط در تحویل پرستار بیاید و چک‌لیست فضا کدام جریان؟** *توصیه:* ترالی فقط پرستار؛ فضای عمومی و اتاق پزشک با پذیرش.
۱۲. **شمارش اولیه را مدیر روی گوشی خودش بزند (کد فعلی) یا هر نفر (انباردار، پذیرش، پرستار) روی گوشی خودش، همان‌طور که سند نوشته؟** *توصیه:* هر نفر در جریان خودش، چون مرجع شروع به نام همان نفر ثبت می‌شود.
۱۳. **ثبت دیگر مجوزها:** آیا حسابدار واقعاً نباید حق شمارش صندوق و امضای تحویل را داشته باشد؟ (در کد دارد.) *توصیه:* حذف از نقش حسابدار.
