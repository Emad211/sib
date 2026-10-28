# مدل دامنهٔ اپ شیفت

> نسخه 1.1 · ۱۴۰۵/۰۸/۰۶ (پیش‌نویس، در SHF-1 دقیق می‌شود)

## ۱. موجودیت‌های پایه (قابل تنظیم و نسخه‌دار)

| موجودیت | فیلدهای اصلی | نکته |
|---|---|---|
| `staff` | id، نام، موبایل، PIN هش‌شده، فعال | نقش‌ها جدا |
| `staff_role` | staff_id، role | نقش‌ها: reception، nurse، storekeeper، purchaser، accountant، shift_doctor، manager |
| `location` | id، نام | قفسهٔ پذیرش، اتاق CPR، ترالی، اتاق پزشک، فضای عمومی، انبار اصلی |
| `item` | id، نام، نوع (counted، yes_no، reading، photo)، location_id، role، تناوب، واحد، حداقل، `acc_code` | `acc_code` برای اقلام شمارشی اجباری؛ هر کد فقط در یک محل (پیش‌فرض) |
| `checklist` | گروهی از itemهای غیرشمارشی | ترالی، فضا، نظافت |
| `setting_version` | کلید، مقدار، `effective_from_shift` | R-09 |
| `campaign` | نام، خدمات، مقدار، شروع، پایان | تخفیف فقط از اینجا |
| `reward_rate` | نوع کار، مبلغ، ماه | |

## ۲. شیفت

- `shift_session`: شروع (با تحویل گرفتن)، مسئول صندوق، افراد حاضر، `cutoff_at`، پایان (با امضای دو طرف)، و `block_id` برای شیفت‌های پشت‌سرهم.
- شیفت جاری هر کاربر = آخرین `shift_session` باز که در آن حاضر است.

## ۳. رویدادها (فقط‌افزودنی)

همه با: `id` (شماره ترتیبی سرور `seq`)، `client_event_id` / `idempotency_key` (کلید یکتای کلاینت)، `type`، `payload` (JSON با schema نسخه‌دار)، `actor_id`، `device_id`، `shift_id`، `client_at` (زمان کلاینت)، `server_at` (زمان سرور)، `ref_event_id`.

> **تکرارناپذیری و مدیریت خطای ساعت:**
> - وجود `client_event_id` / `idempotency_key` تضمین می‌کند که بازپخش رویدادها از صف آفلاین گوشی (Outbox) پس از وصل مجدد شبکه هرگز رویداد تکراری درج نکرده و کاملاً تکرارناپذیر (Idempotent) عمل کند (`INSERT ... ON CONFLICT(idempotency_key) DO NOTHING`).
> - تفکیک `client_at` (زمان ثبت در دستگاه پرسنل) از `server_at` (زمان دریافت قطعی در سرور محلی) خطای ساعت گوشی‌ها (Clock Skew) را مهار می‌کند؛ مرجع معتبر برای برش‌های شیفت (`cutoff_at`)، گزارش‌ها و ترتیب بازسازی projectionها منحصراً `server_at` و ترتیبی سرور (`seq`) است.

| گروه | انواع |
|---|---|
| تحویل | `handover_started`، `cash_counted`، `item_counted`، `recount_requested`، `outgoing_counted`، `joint_counted`، `checklist_answered`، `parcel_handed`، `handover_signed`، `early_leave_reported` |
| مغایرت | `discrepancy_recorded` (خودکار) |
| رویداد شیفت | `card_transfer_reported`، `refund_requested`، `campaign_discount_applied`، `cash_withdrawn`، `waste_reported`، `patient_item_held`، `patient_item_delivered`، `emergency_withdrawal`، `equipment_fault`، `fault_assigned`، `fault_fixed`، `vitals_recorded`، `followup_opened`، `cleaning_confirmed` |
| تأیید | `approved`، `rejected`، `changed` (همه با `ref_event_id`) |
| خرید | `order_drafted`، `order_decided`، `order_placed`، `delivery_received`، `delivery_flagged`، `delivery_transferred`، `invoice_digitized`، `payment_recorded`، `vendor_return`، `vendor_credit` |
| انبار | `shelf_restocked`، `restock_confirmed`، `weekly_count`، `expiry_flagged` |
| پاداش | `reward_earned`، `reward_confirmed`، `reward_revoked` |
| تنظیمات | `setting_changed`، `item_changed`، `staff_changed` |

## ۴. ماشین‌های حالت (نمونه)

### شمارش یک قلم در تحویل (H-05)

```text
pending ─count(x)─► [x == E] ─► matched
                    [x != E] ─► recount ─count(y)─► [y == E] ─► matched
                                                    [y != E] ─► outgoing ─count(z)─► [z == E] ─► matched
                                                                                     [z != E] ─► joint ─count(j)─► settled(j)
settled(j): discrepancy_recorded(E − j, به نام خروجی) ؛ مرجع شیفت بعد = j
```

> **سؤال Q-09:** اگر شمارش دوم ورودی با مرجع نخواند ولی شمارش کور خروجی بخواند، نتیجه «matched» است یا به شمارش مشترک می‌رود؟ نمودار بالا فرض اول را گرفته است. سند منبع این حالت را صریح نگفته.

### سفارش خرید

```text
draft ─► submitted ─► approved | rejected | changed→submitted
approved ─► placed ─► received_ok | received_flagged(payment_locked)
received_* ─► (in_transit between shifts)* ─► with_storekeeper ─► digitized ─► paid
received_flagged ─manager decision─► unlock | return_to_vendor
```

### برداشت از صندوق

`requested ─► [amount ≤ 300,000] approved_by_recipient | [amount > 300,000] waiting_manager ─► approved | rejected`

## ۵. projectionها

| projection | تعریف |
|---|---|
| `stock(item, location)` | از رویدادهای شمارش تأییدشده + شارژ + مصرف حسابداری − برداشت − ضایعات |
| `cash(shift)` | N0 + نقد حسابداری − برداشت‌ها − واریزها |
| `open_decisions` | هر رویداد نیازمند تأیید مدیر که هنوز `approved/rejected/changed` ندارد |
| `my_queue(user)` | کارهای باز نقش‌های کاربر در شیفت جاری (R-01، R-07) |
| `reward_ledger(month)` | پاداش‌های تأییدشده منهای ابطال‌ها |

## تاریخچه

- 1.0، ۱۴۰۵/۰۸/۰۴: پیش‌نویس اول.
- 1.1، ۱۴۰۵/۰۸/۰۶: افزودن فیلدهای `client_event_id` / `idempotency_key` برای تکرارناپذیری همگام‌سازی آفلاین، و تفکیک `client_at` و `server_at` برای مدیریت خطای اختلاف ساعت (Clock Skew).
