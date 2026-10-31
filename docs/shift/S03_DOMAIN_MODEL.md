# مدل دامنهٔ اپ شیفت

> نسخه 1.8 · ۱۴۰۵/۰۸/۰۹

## ۱. موجودیت‌های پایه (قابل تنظیم و نسخه‌دار)

| موجودیت | فیلدهای اصلی | نکته |
|---|---|---|
| `staff` | id، نام، موبایل، PIN هش‌شده، فعال | نقش‌ها جدا |
| `staff_role` | staff_id، role | نقش‌ها: reception، nurse، storekeeper، purchaser، accountant، shift_doctor، manager |
| `location` | id، نام | قفسهٔ پذیرش، اتاق CPR، ترالی، اتاق پزشک، فضای عمومی، انبار اصلی |
| `item` | id، نام، نوع (counted، yes_no، reading، photo)، location_id، role، تناوب، واحد، حداقل، `acc_code`، `sort_order` | `acc_code` برای اقلام شمارشی اجباری؛ هر کد فقط در یک محل (پیش‌فرض). `role` مسئول چک‌لیست است (پذیرش/پرستار/انباردار) و خالی‌بودنش یعنی نقش محل؛ `sort_order` ترتیب قفسه است (ADR-022) |
| `checklist` | گروهی از itemهای غیرشمارشی | ترالی، فضا، نظافت |
| `setting_version` | کلید، مقدار، `effective_from_shift` | R-09 |
| `campaign` | نام، خدمات، مقدار، شروع، پایان | تخفیف فقط از اینجا |
| `reward_rate` | نوع کار، مبلغ، ماه | جدول `reward_rates` (مهاجرت `m0013`)، کلید `(work_type, month)`؛ `month` کلید ماه جلالی `1405-08` است و مبلغ به تومان. نوع کارها: `vitals`، `registration`، `manager_task` (S01 §5) |

> **نگاشت کارمند و حسابداری (SHF-5):** ستون خالی‌پذیر `staff.acc_user` کاربر حسابداری (شناسه یا نام کاربری) را به یک کارمند شیفت وصل می‌کند. کارهای حسابداری‌محور (قند و فشار، ثبت بیمار) از این نگاشت پاداش می‌گیرند و کاربر بی‌نگاشت پاداشی نمی‌گیرد و در فهرست «نگاشت‌نشده‌ها»ی صفحهٔ پاداش مدیر می‌آید. نگاشت، تنظیم مدیر است، نه رکورد درمانگاه.

## ۲. شیفت

- `shift_session`: شروع، مسئول صندوق، افراد حاضر، پایان، و `block_id` برای شیفت‌های پشت‌سرهم.
- `handover`: هر تحویل یک رکورد مستقل با `handover_id`؛ فیلدها: `shift_id`، `flow` (`reception`: صندوق + قفسهٔ پذیرش | `nurse`: اتاق CPR + ترالی)، `opened_at`، `signed_at`، و دو امضا. `opened_at` همان برش زمانی (`cutoff_at`) همین جریان است (H-11). دو جریان هم‌زمان و مستقل‌اند و هیچ‌کدام منتظر دیگری نمی‌ماند (H-12، ADR-017).
- شیفت جاری هر کاربر = آخرین `shift_session` باز که در آن حاضر است.

> **برش زمانی و کلید صندوق (ADR-017، ۱۴۰۵/۰۸/۰۹):** هر جریان تحویل برش زمانی خودش را دارد — لحظهٔ شروع همان جریان؛ هر مصرف یا دریافت نقدی که در حسابداری پس از آن ثبت شود، مال شیفت بعد است (H-11، بند ۶). `proj_cash` به ازای هر صندوق (cash box) کلید می‌خورد، نه هر شیفت (بند ۸). کد فعلی هنوز `proj_cash` را با `shift_id` و `ShiftCutoff` را روی شیفت کلید می‌زند؛ اصلاح در SHF-2.

## ۳. رویدادها (فقط‌افزودنی)

همه با: `id` (شماره ترتیبی سرور `seq`)، `client_event_id` / `idempotency_key` (کلید یکتای کلاینت)، `type`، `payload` (JSON با schema نسخه‌دار)، `actor_id`، `device_id`، `shift_id`، `handover_id` (فقط رویدادهای گروه تحویل؛ H-12)، `client_at` (زمان کلاینت)، `server_at` (زمان سرور)، `ref_event_id`.

> **تکرارناپذیری و مدیریت خطای ساعت:**
> - وجود `client_event_id` / `idempotency_key` تضمین می‌کند که بازپخش رویدادها از صف آفلاین گوشی (Outbox) پس از وصل مجدد شبکه هرگز رویداد تکراری درج نکرده و کاملاً تکرارناپذیر (Idempotent) عمل کند (`INSERT ... ON CONFLICT(idempotency_key) DO NOTHING`).
> - تفکیک `client_at` (زمان ثبت در دستگاه پرسنل) از `server_at` (زمان دریافت قطعی در سرور محلی) خطای ساعت گوشی‌ها (Clock Skew) را مهار می‌کند؛ مرجع معتبر برای برش‌های شیفت (`cutoff_at`)، گزارش‌ها و ترتیب بازسازی projectionها منحصراً `server_at` و ترتیبی سرور (`seq`) است.

| گروه | انواع |
|---|---|
| تحویل | `handover.started`، `handover.cash_counted`، `handover.item_counted`، `handover.recount_requested`، `handover.outgoing_counted`، `handover.joint_counted`، `handover.count_settled`، `handover.step_advanced`، `handover.checklist_answered`، `handover.parcel_handed`، `handover.signed`، `handover.early_leave_reported`، `handover.emergency_start_confirmed`؛ دو رکورد داخلیِ برش نقدی: `handover.cash_reference_adjusted` (متادیتا، بی‌اثر) و `handover.late_cash_recorded` (اثر معکوسِ رویداد دیررسیده، تا صندوق دو بار کم نشود) |
| مغایرت | `discrepancy.recorded` (خودکار) |
| رویداد شیفت — پول | `card.transfer_reported`، `refund.requested`، `campaign.discount_applied`، `cash.withdrawn` |
| رویداد شیفت — انبار | `stock.waste_reported`، `stock.shelf_restocked`، `stock.emergency_withdrawal` |
| رویداد شیفت — مراقبت | `patient.item_held`، `patient.item_delivered`، `vitals.recorded`، `vitals.followup_opened`، `cleaning.confirmed` |
| رویداد شیفت — تجهیزات | `equipment.fault_reported`، `equipment.fault_assigned`، `equipment.fault_fixed` |
| تأیید | `event.approved`، `event.rejected` (با `ref_event_id`، بی اثر projection)؛ `changed` (زنجیرهٔ خرید SHF-4) |
| نگه‌داشته (F5) | `event.held` (رکورد نامعتبر زمانی، بی‌اثر و منتظر مدیر)، `event.released` (آزادسازی؛ خودِ رویداد اصلی با همان هویت و همان زمان وقوع اثر را یک‌بار اعمال می‌کند)، `event.hold_rejected` (برای همیشه بیرون) |
| خرید | `order_drafted`، `order_decided`، `order_placed`، `delivery_received`، `delivery_flagged`، `delivery_transferred`، `invoice_digitized`، `payment_recorded`، `vendor_return`، `vendor_credit` |
| انبار | `restock_confirmed`، `stock.weekly_count`، `expiry_flagged` |
| دسترسی | `shift.presence_declared` (ADR-021: «حضور در شیفت» پذیرش دوم یا پزشک شیفت؛ بی‌اثر روی projection و منبعِ مشتق‌شدن حضور در شیفت همراه با ردیف‌های تحویل) |
| پاداش | `reward_earned` (payload: `staff_id`، `work_type`، `amount_toman`، `source` (`vitals` \| `registration`)، `source_ref`، `note`؛ ثبت‌کننده یک نهاد سیستمی است و **گیرندهٔ پاداش** فیلد `staff_id` است؛ اقلام باز آن را مدیر با `event.approved`/`event.rejected` می‌بندد)، `reward_confirmed` (کار ارجاعی مدیر؛ خودِ ثبت، تأیید بستن است و نیازی به تصمیم دوم ندارد، پس گیرنده‌اش هرگز خودِ ثبت‌کننده نیست)، `reward_revoked` (با `reason`، ارجاع به رکورد تأییدشده، یک ابطال برای هر رکورد، بین‌شیفتی) |
| تنظیمات | `setting_changed`، `item_changed`، `staff_changed` |

**همهٔ رویدادهای شیفت** با `requires_shift` ثبت می‌شوند و در صف آفلاین گوشی (Outbox) تکرارناپذیر همگام می‌شوند؛ فقط تحویل شیفت برخط می‌ماند (ADR-016). فقط چهار نوعِ متحرک اثر projection دارند: `cash.withdrawn` روی صندوق (H-02) و `stock.waste_reported`، `stock.shelf_restocked`، `stock.emergency_withdrawal` روی قفسه (H-01)؛ بقیه فقط ثبت می‌شوند و وضعیتشان با تصمیم عوض می‌شود. `patient.item_held` و `patient.item_delivered` بیرون از شمارش‌اند: هیچ اثری روی موجودی ندارند و تحویل با `ref_event_id` به همان امانت وصل می‌شود. پنج نوع (`card.transfer_reported`، `cash.withdrawn`، `stock.waste_reported`، `stock.shelf_restocked` و `equipment.fault_reported`) یک فیلد **اختیاری** `photo_sha256` دارند: هش SHA-256 محتوای عکسی که سرور در پوشهٔ دادهٔ اپ نگه می‌دارد (SHF-7). فیلد فقط متادیتاست و هیچ اثری روی projection ندارد؛ سرور رویدادی را که به هشی بی‌فایل ارجاع بدهد نمی‌پذیرد و عکس را فقط نقش تأییدکننده/مدیر می‌خواند.

نام رویدادهای خرید و تنظیمات هنوز در کد نیستند و تا SHF-4 به همین شکل می‌مانند؛ نام‌های رویداد شیفت، تأیید و پاداش از رجیستری همین شاخه آمده‌اند. رویدادهای پاداش دکمهٔ گوشی پرسنل نیستند (`phone=False` در رجیستری): دکمهٔ پاداش برای خود، درِ خوددادن است. هر کار فقط از **یک** منبع پاداش می‌گیرد — قند و فشار از `v_shift_vitals_services_v1` و ثبت بیمار از `v_shift_patient_registrations_v1` (هر دو از آینهٔ حسابداری)، نه از رویداد `vitals.recorded` گوشی — تا یک خدمت دو بار پاداش نگیرد.

**خرید و انبار (SHF-4):** هر دوازده نام گروه «خرید» و «انبار» در همین جدول اکنون در رجیستری‌اند و payload آن‌ها در `domain/purchase_payloads.py` است. تصمیم مدیر روی سفارش یک رویداد `order_decided` با `decision` برابر `approved` / `rejected` / `changed` است؛ همان «تغییر» این جدول، بدون رویداد جدا. تنها رویداد متحرک این گروه `restock_confirmed` است و اثرش دوطرفه: از `from_location_id` (انبار اصلی) کم و به `to_location_id` (قفسه) زیاد می‌شود. بقیه فقط ثبت می‌شوند: `delivery_received` سند خرید است و موجودی قفسه را جابه‌جا نمی‌کند (ورود کالا به قفسه همان رویداد «و» است، تا یک واقعیت دو بار شمرده نشود)، فاکتور و پرداخت و بستانکاری پول حسابداری‌اند و به صندوق کاری ندارند، و `vendor_return` سند مرجوعی است نه حرکت قفسه. `delivery_flagged` یک kind تأییدشدنی با داورِ مدیر است: تأیید مدیر قفل پرداخت را باز می‌کند و رد او بار را در مسیر مرجوعی نگه می‌دارد. `expiry_flagged` به همان `stock.weekly_count` ارجاع می‌دهد و تاریخ انقضا ذخیره نمی‌شود.

## ۴. ماشین‌های حالت (نمونه)

### شمارش یک قلم در تحویل (H-05)

```text
pending ─count(x)─► [x == E] ─► matched
                    [x != E] ─► recount ─count(y)─► [y == E] ─► matched
                                                    [y != E] ─► outgoing ─count(z)─► [z == E] ─► matched
                                                                                     [z != E] ─► joint ─count(j)─► settled(j)
settled(j): discrepancy.recorded(E − j, به نام خروجی) ؛ مرجع شیفت بعد = j
```

> **Q-09 بسته شد (ADR-017، ۱۴۰۵/۰۸/۰۹):** اگر شمارش کور نفر خروجی (شمارش سوم) با مرجع بخواند، نتیجه «matched» است؛ شمارش مشترک لازم نیست و هیچ مغایرتی ثبت نمی‌شود. نمودار بالا همین را می‌گوید و اکنون تأییدِ مالک را دارد.

### مغایرت صندوق (H-13)

```text
pending ─blind─► a, b ─► [a == b] ─► settled(a)
                     ─► [a ≠ b] ─► joint ─count(j)─► settled(j)   یک بازشماری مشترک، هر دو نفر حاضر
settled(v): عدد توافق‌شده v ثبت می‌شود ؛ اختلاف v با مرجع (اگر بود) ← discrepancy.recorded به نام نفر خروجی، فقط هشدار به مدیر و بدون کسر از حقوق (H-06، H-13)
```

> **ثبت تسویه (ADR-017 بند ۹):** هر بار که زنجیرهٔ صندوق تسویه می‌شود (چه با بازشماری مشترک، چه با دو شمارش کورِ هم‌خوان) یک رویداد `handover.count_settled` با `basis` (`joint` یا `agreeing_blind`) نوشته می‌شود. عدد تسویه‌شده مرجع بعدی است: `expected` و `counted` صندوق روی همان عدد می‌نشینند و `variance` صفر می‌شود تا فاصلهٔ همین شیفت در مرجع شیفت‌های بعد تکرار نشود؛ خودِ فاصله فقط در `discrepancy.recorded` می‌ماند. `handover.joint_counted` معنای دقیق خودش را نگه می‌دارد و مرجع را جابه‌جا نمی‌کند.

### شروع اضطراری تک‌نفره (H-08)

```text
handover.early_leave_reported ─► handover.emergency_start_confirmed (تأییدکنندهٔ تلفنی، server_at) ─► تحویل تک‌نفره
هر discrepancy.recorded این مسیر: بدون نام مسئول، در `open_decisions` برای بررسی مدیر؛
هرگز خودکار به نام نفر غایب نوشته نمی‌شود.
```

### سفارش خرید

```text
draft ─► submitted ─► approved | rejected | changed→submitted
approved ─► placed ─► received_ok | received_flagged(payment_locked)
received_* ─► (in_transit between shifts)* ─► with_storekeeper ─► digitized ─► paid
received_flagged ─manager decision─► unlock | return_to_vendor
```

> **پیاده‌سازی SHF-4:** وضعیت هر سفارش و هر دریافت از لاگ رویداد تاشده می‌شود (`domain/purchase.py`)، نه از جدول وضعیت؛ پس rebuild و بازپخش همان وضعیت را می‌دهد. `order_drafted` سفارش را «منتظر تصمیم» می‌کند (`submitted`)، `order_decided` سه تصمیم را می‌نویسد و «تغییر» را به `draft` برمی‌گرداند تا پیش‌نویس تازه با همان `order_id` دوباره به مدیر برود، `order_placed` فقط روی سفارش تأییدشده پذیرفته می‌شود. `delivery_received` شناسهٔ دریافت را سرور می‌سازد و قیمت ندارد؛ `delivery_flagged` پرداخت را قفل می‌کند تا `event.approved` مدیر روی همان برچسب برسد (رد مدیر قفل را باز نمی‌کند). `delivery_transferred` با `to_kind` برابر `shift` وضعیت «در راه» و با `storekeeper` وضعیت «نزد انباردار» می‌دهد و شناسهٔ بستهٔ تحویل شیفت همین `delivery_id` است. `invoice_digitized` جمع را از خطوط حساب می‌کند (Σ تعداد × نرخ واحد؛ جمع کلاینت پذیرفته نمی‌شود)، `payment_recorded` چند پرداخت را تا ماندهٔ فاکتور می‌پذیرد و «پرداخت‌شده» وقتی جمع پرداخت‌ها به جمع فاکتور برسد، `vendor_return` پیش‌نیاز `vendor_credit` است و بستانکاری از جمع فاکتور کم می‌شود.

### شمارش هفتگی انبار

```text
شنبه ۰۰:۰۰ ─► (شمارش کور هر قلم انبار اصلی) ─► دوشنبه due_time
  داخل بازه و انجام‌شده ─► done ؛ داخل بازه و انجام‌نشده ─► due ؛ پس از دوشنبه و انجام‌نشده ─► overdue
```

> **SHF-4:** پنجرهٔ شمارش از شنبهٔ پیش از `weekly_count_weekday` (پیش‌فرض دوشنبه) تا همان روز است و لحظهٔ هشدار `weekly_count_time` (پیش‌فرض ۰۷:۳۰) از تنظیمات نسخه‌دار می‌آید. شمارش کور است: عدد مرجع در پاسخ هیچ مسیری نمی‌آید و مغایرت (`counted − expected`) فقط سمت سرور ساخته می‌شود، بی‌نام (`culprit_id=None`)، و به همان رویداد شمارش ارجاع می‌دهد.

### برداشت از صندوق

`requested ─► [amount ≤ withdrawal_cap_toman] approved_by_recipient | [amount > withdrawal_cap_toman] waiting_manager ─► approved | rejected`

## ۵. projectionها

| projection | تعریف |
|---|---|
| `stock(item, location)` | از رویدادهای شمارش تأییدشده + شارژ + مصرف حسابداری − برداشت − ضایعات؛ شارژ قفسه (`restock_confirmed`) یک رویداد دوطرفه است: از انبار اصلی کم و به قفسه اضافه می‌شود |
| `cash(cash_box)` | N0 + نقد حسابداری − برداشت‌ها − واریزها؛ کلید: صندوق (cash box)، نه شیفت (ADR-017). N0 از آخرین تسویه (`handover.count_settled`) می‌آید و هر تسویهٔ تازه آن را جابه‌جا می‌کند |
| `open_decisions` | هر رویداد نیازمند تأیید که هنوز `approved/rejected/changed` ندارد؛ داور: نقشِ `approvable_by`، شخصِ `confirmer_field`، یا مدیر — هرگز ثبت‌کننده (F2) و هرگز ذینفعِ ردیف (`beneficiary_field`، SHF-5: پاداشی که خود مدیر کار کرده را خودش نمی‌بندد) |
| `my_queue(user)` | اقلام بازِ همان شخص یا نقش‌هایش، از هر شیفت (شیفت‌های قبل فقط همین اقلام باز، بی‌عدد صندوق — R-01، R-07؛ F2) |
| `reward_ledger(month)` | پاداش‌های تأییدشده در همان ماه منهای ابطال‌هایی که ماه کسرشان همان ماه است (ابطال = ماه بعد از ابطال). مثل `open_decisions` و `my_queue` **محاسباتی** است، نه جدولی: از لاگ خوانده می‌شود و در `rebuild` نیست، پس بازسازی همیشه با وضعیت جاری یکی است |

**فایل عکس (خارج از projectionها):** عکس‌ها در `<SHIFT_PHOTOS_DIR یا کنار دیتابیس>/photos/<دو رقم اول هش>/<هش>.jpg` ذخیره می‌شوند؛ یکی به‌ازای هر هش محتوا، تغییرناپذیر، و رویداد فقط همان هش را دارد. پشتیبان‌گیری، فایل‌های نداشتهٔ هر مقصد را یک‌بار کپی می‌کند و هرگز پاک نمی‌کند (ADR-020). خواندن عکس مسیر جدا و نگهبان جدا دارد: `shift_event.approve` و مدیر. `OpenTarget` (SHF-7) یک خواندن از لاگ است، نه جدول: موردهای بازِ خرابی، امانت و قرائت قند/فشارِ همان شیفت را برای کسی که مجاز به بستنشان است برمی‌گرداند، بی‌عدد صندوق.

## ۶. کارت روزانهٔ مدیر (سند مشتق و ذخیره‌شده)

| ستون | معنا | نکته |
|---|---|---|
| `card_date` | روزی که کارت برای آن است (ISO) | PRIMARY KEY؛ یک کارت در هر روز |
| `covered_from` / `covered_to` | نخستین و آخرین روزی که کارت می‌پوشاند (ISO) | شنبه دو روز |
| `schema_version` | نسخهٔ شکل `payload` (`CARD_SCHEMA_VERSION`، اکنون ۷) | کارت‌های ذخیره‌شدهٔ نسخهٔ ۲ تا ۶ همچنان خوانده می‌شوند (بخش تازه‌ای که ندارند خالی خوانده می‌شود) و کارت تازه با ۷ نوشته می‌شود؛ سند با نسخهٔ ناشناس خوانده نمی‌شود |
| `generated_at_utc` / `generated_at_tehran` | زمان ساخت | تا کارت بدون رمزگشایی سند پیدا شود |
| `payload` | سند کارت به **canonical JSON** | تنها شکل سند؛ صفحه از همین می‌خواند |

مهاجرت `m0011_daily_cards` فقط یک جدول تازه می‌سازد — بی trigger و بی view — و نصب قبلی همهٔ ردیف‌هایش را نگه می‌دارد (ADR-002).

**چرا مشتق است، نه فقط‌افزودنی:** R-03 (ADR-010) روی لاگ رویداد است؛ کارت یک سند است، نه یک واقعیت درمانگاه، و از همان لاگ، جریان‌های تحویل و `audit_log` بازسازی می‌شود. پس اجرای دوبارهٔ یک روز همان یک ردیف را بازنویسی می‌کند و در لاگ رویداد چیزی عوض نمی‌شود. هر بازتولید در `audit_log` با اکشن `daily_card.generated` و `actor` برابر `shift-runtime` ثبت می‌شود و جزئیاتش فقط پنجرهٔ کارت است (تاریخ‌ها و تعداد روز)، نه شمارش یا عدد مرجع. کارت جزو projectionهای `rebuild` نیست؛ راهِ رسمی ساختن و خواندنش `DailyCardService` است.

**واژگان بخش‌ها:** سند امروز هجده بخش دارد، به ترتیب: `emergency_starts`، `over_cap_withdrawals`، `cash_exceptions`، `checklist_answers` (پاسخ «خیر» و هر عدد خوانده‌شدهٔ چک‌لیست، ADR-022)، `discrepancies`، `flows`، `open_flows`، `shift_events`، `open_events`، `rewards`، `campaign_discounts`، `late_cash`، `held_events`، `purchase_orders`، `flagged_deliveries`، `parcels_in_transit`، `weekly_count`، `below_minimum`. دادهٔ SHF-3 رویدادهای شیفت و تصمیم‌های باز را در `shift_events` و `open_events` می‌آورد و تصمیم‌ها ردیف تازه نمی‌سازند، وضعیت ردیف را عوض می‌کنند. SHF-5 دو بخش تازه دارد: `rewards` (پاداش در انتظار تصمیم از هر شیفت، و پاداش تأییدشده و ابطال‌شدهٔ پنجره؛ ساخت کارت چیزی تعلق نمی‌دهد و چیزی تأیید نمی‌کند) و `campaign_discounts` (تخفیف‌های «ت» به تفکیک کمپین، با تعداد و جمع، هم‌عدد با گزارش کمپین). تنخواه صندوق در کلید `cash_float` سند می‌آید و فقط به‌عنوان یادآور کنار شمارش صندوق نمایش داده می‌شود، نه ورودی محاسبه (ADR-022). SHF-4 پنج بخش خرید و انبار دارد: `purchase_orders` (سفارش‌های منتظر تصمیم)، `flagged_deliveries` (بار مشکل‌دار با پرداخت قفل)، `parcels_in_transit` (بار در راه انبار با سن ساعت)، `weekly_count` (وضعیت شمارش هفتگی: انجام‌شده/موعد/گذشته از موعد) و `below_minimum` (اقلام زیر حداقل با موجودی و حداقل)؛ ردیف‌های خرید قیمت و عدد مرجع ندارند (M-19..M-21) و تصمیم مدیر ردیف را از بلوک «منتظر تصمیم» بیرون می‌برد. بخشی که هست ولی آن روز ردیفی ندارد یک خط کم‌رنگ می‌گیرد، بی عدد و بی جای‌خالی. هر بخشی که نوشته می‌شود هنگام خواندن دوباره هم برمی‌گردد: کارت ذخیره‌شده هیچ ردیفی را در رفت‌وبرگشت سند گم نمی‌کند (M-18).

**روزهای پوشش‌داده‌شده:** کارت هر روز کار روز قبل را می‌پوشاند؛ جمعه کارت ندارد و کارت شنبه پنجشنبه و جمعه را می‌پوشاند (ADR-018). مرزها لحظهٔ نیمه‌شب تهران‌اند (UTC+03:30): `start` نیمه‌شب `covered_from` (شامل) و `end` نیمه‌شب روز بعد از `covered_to` (ناشامل). یک جریان تحویل به روزِ `opened_at` خودش تعلق دارد، ولی تحویل باز هر روزی که باز شده باشد در کارت می‌آید.

**سرویس:** `DailyCardService` سطح ساختن و خواندن کارت را می‌دهد: `build` روز را فقط می‌خواند و کارت را برمی‌گرداند (جمعه `None`)، `regenerate` همان کارت را در `daily_cards` می‌نویسد و در `audit_log` ثبتش می‌کند، `generate_due` هر کارتی را که اجرا در آن لحظه بدهکار است به ترتیب تاریخ ذخیره می‌کند (روزهای ازدست‌رفته را تا سقف ۱۴ روز پر می‌کند و در نصب تازه فقط کارت همان روز را می‌سازد)، و `latest`، `history` و `get` کارت‌های ذخیره‌شده را می‌خوانند. زمان اجرا را همچنان scheduler تعیین می‌کند و سازندهٔ کارت را به‌عنوان action به اجرای خودش تزریق می‌کند؛ ساعت کارت از تنظیمات نسخه‌دار می‌آید (R-09) و نبود کارت جمعه قاعدهٔ درمانگاه است، نه تنظیم (ADR-018).


**C1 (نسخهٔ ۷):** سه بخش تازه — `event_counts`، `overdue_faults`، `call_sample` — در `services/daily_card_extras.py` ساخته می‌شوند. ردیف رویداد شناسهٔ خودش را دارد (`event_id`) تا دکمهٔ تصمیم روی کارت به آن اشاره کند، ردیف برداشت بالای سقف وضعیت تصمیم را (`status`) و بار مشکل‌دار شناسهٔ رکورد برچسب را (`flag_event_id`)؛ هر سه اختیاری‌اند و کارت قدیمی‌تر بدون آن‌ها خوانده می‌شود. رویداد `cash.deposited` اکنون یک نوع رویداد شیفت ثبت‌شده است (`domain/shift_events/cash_deposit.py`، کد «وا»، مجوز `cash.deposit.record`) و payload اش `photo_sha256` اختیاری دارد.

## تاریخچه

- 1.0، ۱۴۰۵/۰۸/۰۴: پیش‌نویس اول.
- 1.1، ۱۴۰۵/۰۸/۰۶: افزودن فیلدهای `client_event_id` / `idempotency_key` برای تکرارناپذیری همگام‌سازی آفلاین، و تفکیک `client_at` و `server_at` برای مدیریت خطای اختلاف ساعت (Clock Skew).
- 1.2، ۱۴۰۵/۰۸/۰۹: افزودن موجودیت `handover` با `handover_id` و دو جریان مستقل (H-12)، فیلد `handover_id` روی رویدادهای تحویل، رویداد `emergency_start_confirmed` (H-08)، بستن Q-09 در ماشین حالت شمارش، و ماشین حالت مغایرت صندوق (H-13) — همه طبق ADR-017.
- 1.3، ۱۴۰۵/۰۸/۰۹: برش زمانی به هر جریان تحویل منتقل شد (`opened_at` همان `cutoff_at` جریان، H-11)؛ projection صندوق از `cash(shift)` به `cash(cash_box)` تغییر کرد و اثر باز ADR-017 بسته شد؛ نام مغایرت صندوق پس از بازشماری مشترک در ماشین حالت H-13 ثبت شد (نفر خروجی، فقط هشدار).
- 1.4، ۱۴۰۵/۰۸/۰۹: افزودن رویداد `count_settled` با فیلد `basis` به واژگان تحویل و قاعدهٔ جابه‌جایی N0 روی عدد تسویه‌شده (ADR-017 بند ۹)، رویداد `step_advanced` برای اینکه گام جریان از لاگ بازسازی شود، و قطعی‌شدن زنجیرهٔ شمارش تک‌نفرهٔ H-08 (ADR-017 بند ۱۰).
- 1.5، ۱۴۰۵/۰۸/۰۹: افزودن بخش ۶ برای کارت روزانهٔ مدیر — جدول `daily_cards` (کلید `card_date`، `payload` به canonical JSON، `schema_version`)، دلیل مشتق و بازسازی‌پذیر بودن در برابر فقط‌افزودنی بودن `events` و ثبت بازتولید با اکشن `daily_card.generated` در `audit_log`، واژگان پنج بخش و نبود بخش بی‌داده، قاعدهٔ پوشش (جمعه بی کارت، شنبه دو روز، مرز نیمه‌شب تهران)، سطح `DailyCardService` و تزریق سازندهٔ کارت از سوی scheduler — همه بر پایهٔ کد SHF-6.1.
- 1.6، ۱۴۰۵/۰۸/۰۹: رویدادهای شیفت SHF-3 در بخش ۳ با نام‌های واقعی رجیستری و گروه‌بندی پول/انبار/مراقبت/تجهیزات، رویداد تأیید `event.approved`/`event.rejected` با `ref_event_id` و بی اثر projection، و پاراگراف اثرها (تنها چهار نوع متحرک) و بیرون‌از‌شمارش بودن امانت بیمار؛ بخش ۶ با هفت بخش کارت (`shift_events`، `open_events`) و `CARD_SCHEMA_VERSION` برابر ۳ و خوانا ماندن کارت نسخهٔ ۲.
- 1.7، ۱۴۰۵/۰۸/۰۹: پس از بازبینی SHF-3 — دو رکورد داخلیِ برش نقدی (`handover.cash_reference_adjusted`، `handover.late_cash_recorded`) در گروه تحویل، سه رکورد نگه‌داشتهٔ F5 (`event.held`، `event.released`، `event.hold_rejected`)، معنای تازهٔ `open_decisions`/`my_queue` برای اقلام باز شیفت‌های قبل، و `CARD_SCHEMA_VERSION` برابر ۴ با خوانا ماندن کارت‌های ۲ و ۳.
- 1.8، ۱۴۰۵/۰۸/۰۹: SHF-7 — فیلد اختیاری `photo_sha256` روی پنج نوع رویداد با معنای «هش محتوای عکس»، انبار فایل عکس یکی‌به‌ازای هر هش با دسترسی فقط تأییدکننده/مدیر، کپی یک‌بارهٔ عکس‌ها در پشتیبان (ADR-020)، و خواندن `OpenTarget` برای انتخاب مورد باز در ویزارد. هیچ نوع رویداد تازه‌ای اضافه نشد.
- 1.9، ۱۴۰۵/۰۸/۰۹: SHF-5 — موجودیت `reward_rate` به جدول واقعی `reward_rates` با کلید `(work_type, month)` و نگاشت `staff.acc_user` تبدیل شد؛ سه رویداد پاداش با payload واقعی و تأیید از مسیر `event.approved`، قانون یک‌منبعی هر کار، و بیرون‌داشتن دکمهٔ پاداش از گوشی (`phone=False`)؛ `reward_ledger` محاسباتی شد؛ کارت با دو بخش `rewards` و `campaign_discounts` دوازده‌بخشی و `CARD_SCHEMA_VERSION` برابر ۵ شد و برنگشتن سه بخش نسخهٔ ۴ در خواندن دوبارهٔ سند اصلاح شد.
- 1.10، ۱۴۰۵/۰۸/۰۹: پس از SHF-4 — payloadهای زنجیرهٔ خرید و انبار در `domain/purchase_payloads.py`، اثر دوطرفهٔ `restock_confirmed` و بی‌اثر بودن بقیهٔ گروه، تاشدن وضعیت سفارش و دریافت از لاگ، پنجرهٔ شنبه..دوشنبهٔ شمارش هفتگی، و پنج بخش تازهٔ کارت با `CARD_SCHEMA_VERSION` برابر ۶ و خوانا ماندن کارت‌های ۲ تا ۵ (کارت ۵ بخش‌های خرید را ندارد و آن‌ها خالی خوانده می‌شوند؛ هیچ بخش SHF-5 در رفت‌وبرگشت گم نمی‌شود).
- 1.11، ۱۴۰۵/۰۸/۰۹: رویداد افزایشی shift.presence_declared (ADR-021) در گروه دسترسی: بی‌اثر روی projection، منبع مشتق‌شدن «حضور در شیفت» همراه با ردیف‌های handover_custody.
- 1.12، ۱۴۰۵/۰۸/۱۰: C1 و B2 — `CARD_SCHEMA_VERSION` برابر ۷ با خوانا ماندن کارت‌های ۲ تا ۶، سه بخش تازهٔ کارت، شناسهٔ ردیف برای دکمهٔ تصمیم، و ثبت `cash.deposited` به‌عنوان نوع رویداد شیفت با عکس فیش اختیاری.
