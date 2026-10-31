# ممیزی «اسناد در برابر کد» — حسابداری، مهندسی و عملیات

> تاریخ: ۱۴۰۵/۰۸/۰۹ (2026-10-31) · مبنا: `main` در commit `1f1a046` · فقط‌خواندنی؛ هیچ تستی اجرا نشد، به `_data/` دست نخورد.
> دامنه: آنچه مالک در اسناد خواسته، به‌جز جزئیات شمارش کور اپ شیفت. زمان استقرار حسابداری و کد ۶۹ قلم طبق تصمیم مالک خارج از دامنه است (فقط یادآوری می‌شود).
> مسیرها نسبت به ریشهٔ ریپو هستند. `A=apps/accounting/src`، `T=apps/accounting/tests`.
> وضعیت‌ها: **done** (در کد هست و دیده شد) · **partial** (بخشی هست) · **missing** (نیست) · **stale** (سند با واقعیت کد نمی‌خواند؛ توضیح در ستون شاهد).
> شمارش وضعیت‌ها فقط از ستون وضعیت جدول‌ها گرفته شده است.

## خلاصهٔ یک‌نگاه

- هستهٔ حسابداری (فاز ۰ و ۱، بازخورد دور ۱، بخش اول و دوم مدیر، ابطال/برگشت وجه، هزینه‌ها، مهاجرت‌ها تا ۷، پشتیبان‌گیری، قرارداد viewهای اپ شیفت) **واقعاً ساخته شده و در کد دیده شد**.
- چیزهایی که مالک صراحتاً خواسته و **هنوز نیست یا ناقص است**: ذخیرهٔ کد فراگیر/گذرنامهٔ اتباع (اعتبارسنجی می‌شود ولی ذخیره نمی‌شود)، حقوق پذیرش در کاشی حقوق، تنظیم ساعت شیفت و آستانه‌ها از صفحهٔ مدیر، ابطال فاکتور در گزارش «معوقات بیمه»، گزارش واحد (ACC-2.5)، شکستن `reception.py`، مکان دوم پشتیبان، تمرین بازیابی.
- بخش بزرگی از **خود اسناد کهنه شده**: وضعیت فاز ۱ و ۳ نقشهٔ راه، بستهٔ انتشار ۲٫۱٫۰ (می‌گوید دیتابیس نسخهٔ ۴؛ کد نسخهٔ ۷ است)، E04، فهرست `00_README`، A05 (ACC-2.11 و ACC-2.12)، A06 بخش ۷.

---

## ۱. `00_README` · `01_PROJECT_CHARTER` · `02_ROADMAP` · `06_RISK_REGISTER` · `HANDOFF`

| شناسه | چه وعده‌ای داده شد | وضعیت | شاهد |
|---|---|---|---|
| README: نقشهٔ ساختار | `apps/accounting` و `apps/shift` «پیشنهادی، منتظر تأیید ADR-001» | stale | هر دو پوشه وجود دارند؛ ADR-001 پذیرفته است (`docs/03_DECISIONS.md` ADR-001). |
| README: فهرست اسناد | همهٔ اسناد فهرست شوند | stale | `A08_CLIENT_FEEDBACK_R1`، `E04_PROJECT_MAP`، `O04_SHIFT_RUNBOOK` در جدول `docs/00_README.md` نیستند. |
| G1 | بدون قطعی/از دست رفتن داده؛ پشتیبان روزانهٔ سالم | partial | کد: `A/services/backup.py:48-51`، `A/services/scheduler.py`. استقرار انجام نشده، پس سنجهٔ «صفر توقف پس از انتشار» هنوز قابل اندازه‌گیری نیست. |
| G2 | همهٔ مسیرها و اعداد زیر تست؛ پوشش service ≥ ۸۵٪ | partial | ماتریس مسیر: `T/route_matrix.json`؛ ۴۹۱ تست (E04). ولی CI فقط پوشش گزارش می‌دهد و آستانه/ratchet ندارد (`.github/workflows/accounting.yml`، `--cov-report` بدون `--cov-fail-under`)؛ تست حقوق (payroll) وجود ندارد. |
| G3 | هیچ فایل route بالای ۴۰۰ خط؛ صفر SQL در api؛ صفر `except` بی‌لاگ | missing | `A/api/reception.py` ۲٬۱۱۴ خط؛ `manager/reports.py` ۱٬۱۶۰؛ `dashboard.py` ۶۵۸؛ `exports.py` ۶۳۳؛ `tariffs.py` ۵۸۰؛ `arrears.py` ۵۴۹؛ `payroll.py` ۴۸۵. حدود ۲۷۱ فراخوانی `.execute(` داخل `A/api`. ۵۷ `except Exception/except:` که ۷ تایشان فوراً `pass` است. |
| G4 | مدیر در ≤ ۵ دقیقه بفهمد دیروز چه شد | done | داشبورد: `A/api/manager/dashboard.py:33-290`، `A/services/manager_insights.py`. (پذیرش مالک با دادهٔ واقعی هنوز ثبت نشده.) |
| G5 | اپ شیفت | — | خارج از این ممیزی. |
| G6 | دیتابیس فعلی هرگز از کار نیفتد | done (در کد) | `A/adapters/sqlite/migrations/` (۷ مهاجرت افزایشی)، `T/test_migrations.py:47-198` (قدیمی روی جدید، idempotent، rollback). اجرای واقعی روی درمانگاه منتظر مالک. |
| فاز ۰ (۹ کار) | ۸ از ۹ انجام | done | ACC-0.9 هنوز باز است (Q-03). جزئیات در جدول A05. |
| فاز ۱ — وضعیت | «منتظر ادغام PRها» | stale | PRهای #1..#15 ادغام شده‌اند (`docs/HANDOFF.md` §۲). |
| فاز ۱ — دروازه | «نسخهٔ 2.0.0 ≥ ۷ روز در پروداکشن» | stale | نسخه 2.1.0 است (`A/__version__.py`)؛ و شرط (۷ روز پروداکشن) برآورده نشده چون استقرار نشده. |
| فاز ۲ — شکستن manager و reception | هر دو فایل شکسته شوند | partial | `manager.py` شکسته شد (`A/api/manager/*`)؛ `reception.py` نه. |
| فاز ۲ — موتور گزارش واحد | به‌جای ۸ جفت گزارش/CSV | missing | هنوز ۸ route گزارش و ۷ route خروجی جدا: `A/api/manager/reports.py`، `exports.py`. `A/domain/reports.py` و `A/services/manager_service.py` صفر بایت‌اند. |
| فاز ۲ — بازطراحی مدیر طبق A06 | کارت امروز، شیفتی، ممیزی | done (بخش ۱ و ۲) | جدول A06 پایین. |
| فاز ۲ — DATA-06/07 | چک‌سام کد ملی، نوع پرداخت اجباری | done | `A/api/reception.py:631,681,838-847`؛ `A/domain/identity.py`. |
| فاز ۲ — آمادگی شیفت | فهرست انتخابی + viewهای فقط‌خواندنی | done | `A/adapters/sqlite/migrations/m0004_shift_contract.py`، `m0007_shift_contract_voids.py`؛ `T/test_shift_contract.py`. |
| فاز ۳ — وضعیت | «SHF-1 در حال انجام» | stale | SHF-1، 2، 3، 6.1، 7.1 در `main` (`docs/HANDOFF.md` §۲). |
| RK-01 | پشتیبان دستی شبانه از همین حالا؛ ACC-1.2 | partial | ACC-1.2 کد شده؛ پشتیبان بیرونی/دستی شبانه هنوز تصمیم مالک نگرفته (Q-15). |
| RK-04 | جعل نشست مدیر | done | `A/config/settings.py:6,9`، `A/app.py:41-56` (کلید هر نصب در `.hesabdari_secret_key`). |
| RK-10 | هشدار نرم کد ملی (ACC-2.9) | done | `A/domain/identity.py`، `A/api/reception.py:171-224`. |
| HANDOFF | وضعیت حسابداری، «اگزهٔ تازه از `5c46353`، schema ۷» | done | مهاجرت‌ها تا `m0007` موجودند؛ ولی بسته/CHANGELOG هنوز «۴» می‌گویند (پایین). |

---

## ۲. `03_DECISIONS` (ADRهای مربوط به حسابداری و پایه)

| شناسه | چه وعده‌ای داده شد | وضعیت | شاهد |
|---|---|---|---|
| ADR-001 | مونوریپو با تاریخچه | done | `apps/accounting`، `git log`؛ `CLAUDE.md` ریشه. |
| ADR-002 | فقط تغییر افزایشی، `user_version`، پشتیبان پیش از مهاجرت | done | `A/adapters/sqlite/migrations/__init__.py`؛ `tools/schema_diff.py`؛ `T/test_prod_snapshot.py:85` (محلی). |
| ADR-003 | بازسازی تدریجی | partial | `manager.py` شکسته شد؛ `reception.py` نه. |
| ADR-006 | دادهٔ واقعی در گیت/CI نیست | done | `.gitignore` (`_data/`, `*.db`)، گارد CI در `accounting.yml`. اسکن secret در CI نیست (E02 می‌گوید هست). |
| ADR-007 | پایتون ۳٫۱۳ و lock | done | `apps/accounting/requirements.lock`، `requirements-dev.lock`. |
| ADR-008 | backup API، `integrity_check`، روزانه/شروع/پیش از مهاجرت، ۷/۴/۶، محافظ ۵۰۰MB | done | `A/services/backup.py:48-51,89-146`؛ `A/services/scheduler.py`. |
| ADR-013 | حذف نقش پزشک | done | `T/test_retired_doctor_role.py`؛ `USER_ROLES` در `A/services/auth_service.py`. |
| ADR-003 «CI هر شب Playwright» (A04 L8) | — | missing | رجوع شود به A04. |

---

## ۳. `05_OPEN_QUESTIONS`

| شناسه | وضعیت سؤال | وضعیت در کد/سند | شاهد |
|---|---|---|---|
| Q-03 | باز: کدام exe و کدام رایانه | missing | مانع ACC-0.9 و استقرار. |
| Q-04 | باز: ساعت خلوت | missing | — |
| Q-15 | باز: مکان دوم پشتیبان | missing | `A/services/backup.py` فقط یک پوشه (`BACKUP_FOLDER`) می‌شناسد. |
| Q-23 | باز: واحد = تومان؟ | stale | کد و CHANGELOG در عمل «تومان» می‌گیرند (`A/domain/money.py`، `A/services/manager_finance.py`)، ولی پرسش در ۰۵ بی‌جواب مانده. |
| Q-24 | باز: «کد ملی ارائه نشد» | stale | A08 FB-03 این را عملاً حل کرد (اتباع باید شمارهٔ فراگیر/گذرنامه بدهند)؛ ۰۵ هنوز باز نشان می‌دهد. |
| Q-06/Q-07/Q-12/Q-11/Q-13/Q-14/Q-20/Q-21 | باز | missing | پاسخ ندارند. Q-20 (دو کپی اضافهٔ دیتابیس) هنوز بسته نشده. |
| Q-08 | بسته: ابطال در حسابداری | done | `A/api/manager/dashboard.py:357-399`، `m0006_invoice_voids.py`، `m0007…`. |
| تاریخچهٔ ۰۵ | ترتیب نسخه‌ها | stale | سطرهای 1.3 قبل از 1.2 آمده‌اند (جزئی). |

---

## ۴. A01 (ممیزی وضعیت) — یافته‌ها

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| SEC-01 | کلید نشست یکتا | done | `A/app.py:41-56`. |
| SEC-02 | traversal بکاپ | done | `A/services/backup.py:121 safe_backup_path`. |
| SEC-03 | ریست محافظت‌شده | done | `A/api/manager/settings.py:192+` (رمز مدیر، پشتیبان پیش از ریست). |
| SEC-04/09 | `is_active` و نشست ۸ ساعته | done | `A/app.py:56`، `A/services/auth_service.py`، `T/test_auth_security.py`. |
| SEC-05 | CSRF | done | `A/web/csrf.py`، `T/test_csrf.py`. |
| SEC-06 | بررسی نقش در مسیرهای reception | partial | نقش پزشک حذف شد؛ مسیرهای reception هنوز فقط `login_required` دارند (۳۰ مورد، `A/api/reception.py`)، نقشی چک نمی‌شود. |
| SEC-07 | حداقل طول/پیچیدگی رمز | missing | `A/api/manager/users.py:46-68` فقط «خالی نبودن» را می‌سنجد. |
| SEC-08 | ریپو خصوصی | done | ثبت‌شده در ۰۵ Q-22. |
| SEC-10/11 | CSV injection، onclick | done | `A/services/exports.py`، `T/test_exports.py`، `T/test_inline_scripts.py`. |
| BUG-01..08 | باگ‌های مسیر/تعرفه | done | `T/test_bugfixes_phase1.py`، `T/test_tariff_forms.py`. |
| PERF-01 | index و کوئری | done | `m0002_performance_indexes.py`. |
| DATA-01..04 | backup، روزانه، پوشه، مهاجرت | done | بالا. |
| DATA-05 | ستون‌های مرده | partial | حذف نمی‌شوند (درست)؛ ولی کد هنوز `visits.status` را در جاهایی می‌خواند؛ «deprecated» فقط در سند. |
| DATA-06/07 | نوع پرداخت و کد ملی | done | `A/api/reception.py:631,681,838`. ۷۴٪ بیمارِ قدیمی بدون کد ملی همچنان هست (ابزار پاک‌سازی نیست). |
| DATA-09 | تمرکز منطقهٔ زمانی | partial | ۳۹ جا `+3 hours` در SQL/کد (`grep "+3 hours"`). |
| DEBT-01 | شکستن فایل‌های بزرگ | partial | `manager.py` شد، `reception.py` ۲٬۱۱۴ خط مانده. |
| DEBT-02 | گزارش‌های تکراری | missing | ۸ گزارش + ۷ خروجی جدا. |
| DEBT-03 | `except` بی‌لاگ | partial | از ۷۴ به ۵۷؛ ۷ مورد `pass` مستقیم. |
| DEBT-05 | SQL با f-string | partial | `m0004…:88` و ده‌ها مورد در `A/api/manager/*.py`. |
| DEBT-07 | فایل‌های خالی | missing | `A/services/manager_service.py` و `A/domain/reports.py` هنوز صفر بایت‌اند. |
| DEBT-09 | `utcnow` منسوخ | missing | `A/common/utils.py:16`؛ فقط با `filterwarnings` در `pytest.ini` ساکت شده. |
| BIZ-02 / Q-06 | نقد ۰٫۲٪؟ | missing | بی‌پاسخ. |

---

## ۵. A02 (معماری مقصد)

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| لایه‌ها `web/ services/ domain/ adapters/` | web فقط ورودی→سرویس→رندر؛ بدون SQL | partial | `A/web/` فقط `csrf.py` دارد؛ route ها هنوز در `A/api/` و SQL دارند (۲۷۱ `.execute`). E04 خودش می‌نویسد «aspirational». |
| `web/authz.py` و `@require_role` | یک دکوراتور برای همه | partial | به‌جای آن `manager_required`/`manager_api_required` در `A/api/manager/_common.py`؛ reception بدون نقش. |
| تست معماری L9 | قاعدهٔ وابستگی لایه‌ها | missing | نه `import-linter`، نه تست پایتونی (`grep`). |
| `domain/` شامل money, shifts, jalali, national_id | — | done (با نام دیگر) | `A/domain/{money,shifts,jalali,identity,billing}.py`؛ `national_id.py` در `identity.py` ادغام شد. |
| `services/reports/` یک موتور | — | missing | نیست. |
| `config/settings.py`، منطقهٔ زمانی تنها در config | — | partial | `+3:30` پخش است (DATA-09). |
| ساختار پوشه‌ها در سند | — | stale | واقعیت `api/`، `common/`، `adapters/sqlite/*_repo.py` است نه `web/…/repos/`. |

---

## ۶. A03 (سازگاری و مهاجرت)

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| قانون افزایشی | فقط جدول/ستون nullable/index/view | done | `m0002..m0007`. |
| `user_version` + پشتیبان پیش از مهاجرت | — | done | `A/adapters/sqlite/migrations/__init__.py`؛ `T/test_migrations.py:107-155`. |
| «پروداکشن امروز = 0» و «`0001_baseline` فقط staff_id و index» | — | stale | پروداکشن واقعی هنوز 0 است؛ ولی نسخهٔ آماده‌شده اکنون ۷ است (و سند A03 بخش ۳ فقط baseline را شرح می‌دهد). |
| تست‌های اجباری بخش ۴ | ۶ تست | done | CI: `T/test_migrations.py`؛ محلی: `T/test_prod_snapshot.py` (۶ تست). |
| `tools/schema_diff.py` در CI | «بین baseline و پس از مهاجرت‌ها» | partial | `T/test_migrations.py:11` از آن استفاده می‌کند؛ ولی مرحلهٔ جدا در CI نیست. |
| «زمان مهاجرت روی کپی پروداکشن < ۳۰ ثانیه» | — | partial | تست زمان‌سنجی صریح پیدا نشد. |
| viewهای نسخه‌دار `v_shift_*` | — | done | `m0004` (۷ view) + `m0007` (`v_shift_voids_v1`)؛ `T/test_shift_contract.py`. |

---

## ۷. A04 (راهبرد تست)

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| L1 domain | — | done | `T/test_domain.py`، `T/test_billing_characterization.py` (۲۶ تست). |
| L2 ویژگی (hypothesis) | ناوردایی‌های مالی | missing | `hypothesis` نصب است (`requirements-dev.lock`) ولی هیچ تستی از آن استفاده نمی‌کند (`grep` در `tests/` صفر). |
| L3 repository | — | partial | تست مستقیم repo کم است؛ بیشتر از طریق route. |
| L4 ماتریس مسیر × نقش | `authz_matrix.yaml`، ۶۸ مسیر | done (نام/تعداد فرق دارد) | `T/route_matrix.json` (۸۶ ردیف)، `T/test_route_matrix.py`. نقش `doctor` به `retired_doctor` تبدیل شد. |
| L5 جریان‌های اجباری (۸ جریان) | — | partial | بیمار آزاد/بیمه/حذف آیتم/شیفت شب/پشتیبان/قفل/غیرفعال‌سازی هست (`T/test_payment_rules.py`، `test_backup.py`، `test_auth_security.py`)؛ **جریان ۵ حقوق (payroll) تست ندارد**. |
| L6 مهاجرت | — | done | بالا. |
| L7 گولدن مستر | محلی | done | `tools/golden_master.py`، `T/golden/*.sha256`؛ اجرا نشد. |
| L8 Playwright E2E | ۶ جریان، شبانه | missing | نه در `tests/` نه در `.github/workflows`. |
| L9 معماری | — | missing | بالا. |
| L10 ساخت exe + smoke | — | done | `.github/workflows/accounting.yml` job `build-exe`. |
| ratchet پوشش | «پوشش هرگز پایین نمی‌آید» | missing | CI فقط گزارش می‌کند؛ بدون fail-under. |
| `mypy --strict` (E01) و ruff روی کل کد | — | partial | mypy اصلاً نیست؛ ruff فقط روی فهرست سفید فایل‌ها (`apps/accounting/ruff.toml`). |
| تست جهش | اختیاری | missing | — |
| هدف عددی «۳۰۰+ پایان فاز ۰ / ۸۰۰+ پایان فاز ۲» | — | partial | ۴۹۱ تست (E04). |

---

## ۸. A05 (برنامهٔ کار)

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| ACC-0.1..0.8 | پایه، CI | done | `apps/accounting`, `requirements.lock`, `tools/`, `.github/workflows/accounting.yml`. |
| ACC-0.9 | مقایسه exe با exe پروداکشن | missing | منتظر Q-03. |
| ACC-1.1 | مهاجرت نسخه‌دار | done | `migrations/__init__.py`. |
| ACC-1.2 | پشتیبان؛ نگه‌داری **۱۴+۸+۱۲** | stale | کد و ADR-008/O01 می‌گویند ۷/۴/۶ (`A/services/backup.py:48`)؛ جدول A05 کهنه است. |
| ACC-1.3/1.4 | بازیابی امن، پوشهٔ یکسان | done | `backup.py:121,271`. |
| ACC-1.5 | کلید در `secret_key.txt` | stale | نام واقعی `.hesabdari_secret_key` کنار دیتابیس (`A/config/settings.py:9`). |
| ACC-1.6..1.10 | ریست، `is_active`، لاگ/صفحهٔ خطا، BUG، PERF | done | بالا. |
| ACC-1.11 | بستهٔ ۲٫۱٫۰ | stale | `docs/ops/releases/accounting-2.1.0.md` می‌گوید دیتابیس «نسخهٔ ۴»؛ `CHANGELOG.md` هم «۴» می‌گوید. کد تا ۷ رفته (هزینه‌ها، ابطال، view ابطال) و در CHANGELOG ذکر نشده. نسخهٔ برنامه همچنان 2.1.0 است. |
| ACC-2.1 | CSRF + حذف پزشک انجام؛ `@require_role` مانده | partial | بالا (SEC-06). |
| ACC-2.2 | domain با ۸۱ تست | done | `A/domain/`، `T/test_domain.py`. |
| ACC-2.3/2.4 | شکستن reception | missing | `A/api/reception.py` ۲٬۱۱۴ خط. (شاخهٔ `refactor/acc-2.3-reception-package` کنار گذاشته، HANDOFF §۶.) |
| ACC-2.5 | موتور گزارش واحد | missing | بالا. |
| ACC-2.6 | manager → package | done | `A/api/manager/`، `T/test_manager_authz.py`. |
| ACC-2.7 | payroll: تست مشخصه‌نگاری کامل سپس انتقال | missing | `T/` هیچ تست payroll ندارد؛ `compute_payroll` (`A/api/manager/payroll.py`) در کاشی حقوق (`manager_finance.py:53`) هم استفاده می‌شود. فقط گولدن مستر محلی آن را می‌گیرد. |
| ACC-2.8 بخش ۱ و ۲ | داشبورد/تاریخچه/…| done | A06 پایین. |
| ACC-2.8 بخش ۳ | گزارش واحد با فیلتر مشترک | missing | — |
| ACC-2.9 | نوع پرداخت اجباری، ارقام فارسی | done | `A/api/reception.py:631,681`، `T/test_payment_rules.py`. |
| ACC-2.10 | متدهای مرده/ستون deprecated | partial | بخشی (DATA-05). |
| ACC-2.11 | مهاجرت ۴: `acc_code`, `stock_factor`, `invoice_payment_lines`, view؛ «فقط `v_shift_voids_v1` منتظر Q-08» | stale | `v_shift_voids_v1` در `m0007_shift_contract_voids.py` ساخته شده؛ Q-08 بسته است. وضعیت کار done است. |
| ACC-2.12 | ابطال فاکتور بسته | stale | وضعیت ستون خالی/«اگر Q-08=بله»؛ کد کامل است: `A/api/manager/dashboard.py:357-399`، `m0006`، `T/test_invoice_void.py`. |
| ACC-2.13 | بستهٔ انتشار 2.x | missing | نسخه/بسته هنوز نیامده (`A/__version__.py` = 2.1.0). استقرار خارج از دامنه. |

---

## ۹. A06 (بخش مدیر)

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| ۳.۱ نیاز به توجه | ۶ قاعده + پشتیبان + قفل | done | `A/services/manager_insights.py:200-380`؛ `T/test_manager_insights.py`. |
| ۳.۱ سه شیفت آخر | تفکیک ویزیت/پرستاری/عملی، نقد/کارت | done | `manager_insights.py:101`. |
| ۳.۱ مقایسه با ۴ هفته | رنگ/فلش | done | `manager_insights.py:96-98`. |
| ۳.۱ سلامت سیستم | پشتیبان، حجم، `user_version`، نسخه | done | `manager_insights.py:444`. |
| ۳.۲ گزارش واحد | یک صفحه + redirect | missing | — |
| ۳.۳ ممیزی: فیلتر + تاریخچهٔ فاکتور | — | done | `A/api/manager/logs.py:30-38`، `dashboard.py:300`. |
| ۳.۳ الگوهای مشکوک | حذف پس از پرداخت، حذف زیاد، فاکتور مجدد | partial | حذف زیاد و فاکتور تکراری هست؛ «حذف پس از پرداخت» با دلیل داده رد شد (مستند در A06 §۷). |
| ۳.۴ کاربران | غیرفعال/قطع نشست، بازنشانی رمز، قفل، آخرین ورود، تعداد عملیات | done | `A/api/manager/users.py:70-199`. (حذف کاربر هنوز DELETE واقعی است: `users.py:123-128`.) |
| ۳.۵ تعرفه + تاریخچه | — | done | `A/api/manager/tariffs.py:34`، `m0003_tariff_changes.py`. |
| ۳.۶ حقوق: ریز محاسبه + Excel | — | missing | `A/api/manager/payroll.py` خروجی Excel/CSV ندارد (`grep "table_response"` صفر). |
| ۳.۷ تنظیمات/پشتیبان: «بازیابی با تأیید رمز مدیر» | — | missing | `A/api/manager/settings.py:97-125`: بازیابی و حذف پشتیبان فقط نقش مدیر می‌خواهند (رمز فقط برای ریست دیتابیس). |
| ۳.۷ فهرست پشتیبان «با وضعیت سلامت» | — | partial | نام/حجم/تاریخ نشان داده می‌شود، وضعیت سلامت نه (`settings.py:126-136`). |
| ۳.۷ «پشتیبان الان» + هشدار قدیمی | — | done | `settings.py:104`; `manager_insights.py:390-418`. |
| §۴/§۶ آستانه‌ها قابل تغییر | `anomaly_delete_threshold`, `stale_open_hours` | partial | در جدول `settings` خوانده می‌شوند (`manager_insights.py:25,43`) ولی **هیچ فرمی برای تغییرشان نیست** (`grep` در templates/api صفر). |
| Excel و CSV هر دو | — | done | `A/services/exports.py`. |
| مدیر دوم = همان نقش | — | done | `USER_ROLES`. |
| §۷ «برنامه راهی برای بازگشایی فاکتور بسته ندارد» | — | stale | اکنون `invoice_reopen` وجود دارد: `A/api/manager/dashboard.py:401-445`. |
| §۷ «هنوز مانده: گزارش واحد» | — | done (راست) | همان ACC-2.8 بخش ۳. |

---

## ۱۰. A07 (انتشار و استقرار) — استقرار خارج از دامنه، فقط ابزار

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| نسخه‌گذاری، `__version__`، CHANGELOG | — | partial | `A/__version__.py` = 2.1.0؛ `CHANGELOG.md` بخش «بعدی» نسخهٔ ۴ می‌گوید ولی کد ۷ است. تغییرهای دور ۱ (FB-01..28) در CHANGELOG نیامده‌اند. |
| چک‌لیست پیش از انتشار، مراحل استقرار، بازگشت | — | done (سند) | `docs/ops/releases/accounting-2.1.0.md`، `A07` — استقرار منتظر مالک. |
| ساخت exe و smoke در CI | — | done | `accounting.yml`. |

---

## ۱۱. A08 (بازخورد مالک — دور ۱)

| شناسه | خواسته | وضعیت | شاهد |
|---|---|---|---|
| FB-01 | فاکتور باز به شیفت بعد نرسد | done | `A/services/shift_handover.py:40-60`؛ `A/api/reception.py:2066-2100` (۴۰۹ با فهرست blockers). |
| FB-02 | شیفت از ساعت؛ خودکار عوض نشود؛ ساعت‌ها در تنظیمات قابل تغییر | partial | منطق: `A/domain/shifts.py`، `shift_handover.py:18-32`. ولی **هیچ فرم تنظیمی برای `shift_start_*` نیست** (فقط جدول `settings`). |
| FB-03 | اتباع: شمارهٔ فراگیر/گذرنامه + موبایل اجباری | partial | اعتبارسنجی هست (`A/api/reception.py:192-194`، `identity.py:77-88`) اما **مقدار ذخیره نمی‌شود**: `national_id=national_id if not is_foreign else None` (`reception.py:220`). |
| FB-04 | چک تکراری کد ملی/موبایل | partial | `A/services/patient_matching.py`؛ برای اتباع چون شماره ذخیره نمی‌شود، تکراری‌بودن گذرنامه/فراگیر هرگز پیدا نمی‌شود. |
| FB-05/05a | نام حداقل ۲ حرف فارسی | done | `A/domain/identity.py:44-75`. |
| FB-06 | فهرست‌ها به ترتیب پرمصرف ۹۰ روز | done | `A/adapters/sqlite/nursing_services_repo.py:8-25`, `consumable_tariffs_repo.py:13`, `procedure_tariffs_repo.py:14`. |
| FB-07/08 | کار عملی مثل پرستاری؛ انجام‌دهنده پزشک/پرستار شیفت | done | `A/templates/reception/procedures_new.html:385-386`، `A/api/manager/tariffs.py:514`. |
| FB-09 | ×N در فاکتور | done | `A/templates/reception/index.html:1456`. |
| FB-10 | چاپ A5 | done | `A/templates/reception/shift_performance.html:588`. |
| FB-11 | نام بازکننده و بندنده | done | `A/templates/reception/index.html:1937-1939`. |
| FB-20 | کاشی‌ها: تراکنش، هزینه، حقوق، درآمد امروز | partial | `A/services/manager_finance.py`؛ **حقوق پذیرش در عدد نیست** («بعداً» در `index.html:760`) و قاعدهٔ آن هنوز از مالک نخواسته‌شده. |
| FB-20 صفحهٔ هزینه | تاریخ، دسته، مبلغ، توضیح | done | `A/api/manager/expenses.py`، `m0005_expenses.py`، `T/test_manager_finance.py:43-105`. |
| FB-21 | نمودار ۷/۳۰/۹۰ | done | `dashboard.py:463+`؛ `index.html:855-861`. |
| FB-22 | خدمات بر اساس مبلغ | done | `dashboard.py:603`. |
| FB-23 | حذف ۳ کاشی پایین | done | `T/test_manager_finance.py:142`. |
| FB-24 | date picker جلالی | done | `A/static/js/jdatepicker.js`، همهٔ ۱۲ قالب گزارش/لاگ/هزینه. |
| FB-25 | ابطال، برگشت وجه، بازگشایی | partial | `dashboard.py:357-445`؛ گزارش فاکتورها voided را کم می‌کند. **«معوقات بیمه» فاکتور ابطال‌شده را حذف نمی‌کند** (`A/api/manager/arrears.py:128-165,455-485` فاقد فیلتر `status`) — مبلغ مطالبه از بیمه برای فاکتور ابطال‌شده باقی می‌ماند. ثبت ابطال و لاگ‌اش یک تراکنش نیست (`dashboard.py:381-395`). |
| FB-26 | گزارش عملکرد کارکنان | done | `A/api/manager/reports.py:1133`، `A/services/staff_performance.py`. |
| FB-27 | مصرفی به تفکیک شیفت/پذیرش | done | `reports.py:889-901`. |
| FB-28 | گزارش‌ها کوتاه‌تر | done | `T/test_report_dates.py`، قالب `reports_invoices.html`. |

---

## ۱۲. E01–E04 (مهندسی)

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| E01 §۲ | `mypy --strict` روی domain/services، ruff، type hint | partial | mypy نیست؛ ruff فهرست‌سفید (`apps/accounting/ruff.toml`). |
| E01 §۱ | شاخه و PR کوچک ≤۴۰۰ خط | partial | رعایت قابل‌اندازه‌گیری نیست؛ سیاست فرایندی است. |
| E02 §۲ | کپی پروداکشن فقط در `_data/`، `mode=ro` | done | `.gitignore`، `T/test_prod_snapshot.py`. |
| E02 §۳ | اسکن secret و `.db` در CI | partial | فقط گارد `.db` (`accounting.yml`). |
| E02 §۳ | لاگ فعالیت برای خواندن حساس | missing | `ActionType.REPORT_VIEW/REPORT_EXPORT/PATIENT_VIEW_HISTORY` تعریف شده (`A/services/activity_logger.py:43,85-86`) ولی هیچ‌جا ثبت نمی‌شود (`grep` خارج از logger صفر). |
| E02 §۲.۶ / Q-13 / Q-14 | BitLocker، Wi-Fi جدا | missing | از کد قابل‌بررسی نیست؛ بی‌پاسخ. |
| E02 §۲.۵ | حذف کپی‌های قدیمی | partial | قرنطینهٔ `_data/quarantine/…` (HANDOFF §۶)؛ دو کپی Desktop/Downloads (Q-20) تصمیم مالک. |
| E03 | دستورهای `dev.ps1` | done | `apps/accounting/dev.ps1`. |
| E03 | ورود با `doctor1` روی دیتابیس مصنوعی | stale | نقش `doctor` بازنشسته شد (ADR-013). |
| E04 | «No code yet: apps/ contains only accounting» | stale | `apps/shift` وجود دارد. همچنین «ADR-001 تا ADR-013» (اکنون تا ۰۲۰). |

---

## ۱۳. O01–O04 (عملیات)

| شناسه | وعده | وضعیت | شاهد |
|---|---|---|---|
| O01 §۱ | پشتیبان دستی شبانه تا انتشار | partial | فرایند انسانی؛ ثبت اجرا نیست. |
| O01 §۲ | RPO 24h؛ روزانه+شروع+پیش از مهاجرت | done | `scheduler.py`، `backup.py`. |
| O01 §۲ | مکان دوم پشتیبان | missing | Q-15. |
| O01 §۲ | پایش: هشدار پشتیبان قدیمی «⏳ فاز ۲» | stale | انجام شده: `A/services/manager_insights.py:23,390-418` (آستانهٔ ۲۴ ساعت، نه ۲۶). |
| O01 §۳ | تمرین بازیابی ماهانه | missing | جدول ثبت خالی است (`docs/ops/O01…` §۳). |
| O02 | سطح‌ها، برگهٔ کاغذی، یادداشت حادثه | partial | تماس‌ها «(پر شود)» است؛ پوشهٔ `docs/ops/incidents/` نیست. |
| O03 | پیام کارکنان پیش از انتشار | partial | قالب هست؛ پیام ۲٫۱٫۰ نوشته‌شده‌ای پیدا نشد. |
| O03 §۱ | «صفحه‌های پذیرش بی‌تغییر» | stale | فرم باز کردن فاکتور (FB-03/04/05)، شروع شیفت، روش پرداخت اجباری، ×N همه تغییر کرده‌اند؛ باید به پذیرش گفته شود. |
| O04 | راهنمای شیفت | — | خارج از دامنه. |

---

## ۱۴. مواردی که مالک می‌بیند و **نیست** — به ترتیب ارزش برای درمانگاه

| # | مورد | چرا مهم | تلاش |
|---|---|---|---|
| ۱ | ذخیرهٔ کد فراگیر/گذرنامهٔ اتباع (FB-03/04) | مالک گفت «همهٔ اطلاعات باید وارد شود»؛ الان وارد می‌شود ولی دور ریخته می‌شود؛ تکراری‌یابی اتباع کار نمی‌کند. | small (ستون جدید nullable یا استفاده از `national_id`؛ + تست) |
| ۲ | فاکتور ابطال‌شده از «معوقات بیمه» کم شود + ثبت ابطال و لاگ در یک تراکنش | پول: بیمه‌ای که فاکتورش ابطال شده هنوز مطالبه می‌شود. | small |
| ۳ | تست مشخصه‌نگاری حقوق (ACC-2.7) | حقوق پزشک/پرستار و کاشی «حقوق» بدون تست‌اند؛ هر تغییر بی‌صدا می‌تواند عدد را عوض کند. | medium |
| ۴ | حقوق پذیرش در کاشی (FB-20) | کاشی «مانده» ناقص است؛ فرمول را مالک باید بگوید. | small (کد) + نیاز به پاسخ مالک |
| ۵ | فرم تنظیم ساعت شیفت و آستانه‌های «نیاز به توجه» در صفحهٔ تنظیمات | A08 و A06 گفته‌اند «قابل تغییر»؛ فقط با دست‌کاری دیتابیس ممکن است. | small |
| ۶ | پشتیبان در مکان دوم (Q-15) + تمرین بازیابی ماهانه | RK-01: خرابی دیسک = از دست رفتن همه‌چیز؛ هیچ نسخهٔ بیرون از رایانه نیست. | medium (کد کپی به مسیر شبکه/دیسک) + تصمیم مالک |
| ۷ | تأیید رمز مدیر برای بازیابی/حذف پشتیبان (A06 ۳.۷) | بازیابی اشتباه کل داده‌ها را برمی‌گرداند. | small |
| ۸ | گزارش واحد با فیلترهای مشترک (ACC-2.5 / A06 ۳.۲) | وعدهٔ اصلی فاز ۲؛ ۸ گزارش + ۷ خروجی تکراری؛ هر باگ چند جا رفع می‌شود. | large |
| ۹ | خروجی Excel و ریز محاسبهٔ حقوق (A06 ۳.۶) | وعده شده؛ نیست. | small–medium |
| ۱۰ | ثبت خواندن/خروجی گزارش در لاگ فعالیت (E02) | «کارمند کنجکاو»؛ ممیزی خروجی گرفتن از داده‌های بیمار. | small |
| ۱۱ | بررسی نقش در مسیرهای reception + `@require_role` + تست L9 (ACC-2.1، SEC-06) | الان هر کاربر واردشده (مدیر هم) در reception فقط با login وارد می‌شود؛ تست معماری نیست. | medium |
| ۱۲ | حداقل طول رمز (SEC-07) | رمز یک‌حرفی پذیرفته می‌شود. | small |
| ۱۳ | شکستن `reception.py` (ACC-2.3/2.4) و کاهش SQL در api | خطر نگهداشت؛ G3. | large |
| ۱۴ | تست ویژگی (hypothesis)، Playwright، آستانهٔ پوشش، اسکن secret در CI | A04 وعده داده؛ ابزار hypothesis نصب است ولی استفاده نشده. | medium |
| ۱۵ | پاک‌سازی ۷۴٪ بیماران بدون کد ملی (DATA-07) | پیگیری/پاداش اپ شیفت به آن وابسته است؛ فقط از طریق «وقتی بیمار دوباره می‌آید» جلو می‌رود. | medium (ابزار گزارش «بیماران ناقص») |
| ۱۶ | به‌روز کردن CHANGELOG/بستهٔ انتشار برای مهاجرت ۷ و بازخوردهای دور ۱ | مالک و پذیرش باید بدانند چه عوض شده؛ سند الان «نسخهٔ ۴» می‌گوید. | small |
| ۱۷ | حذف کاربر هنوز DELETE واقعی است (`users.py:123`) | برخلاف روح «هیچ حذف واقعی»؛ لاگ‌ها به نام کاربری وصل‌اند. | small |

---

## ۱۵. سؤال‌هایی که واقعاً به مالک برمی‌گردد (به زبان ساده)

۱. **حقوق پذیرش** چطور حساب شود؟ تا نگویید، کاشی «مانده» داشبورد فقط حقوق پزشک و پرستار را کم می‌کند.
۲. **اتباع:** شمارهٔ فراگیر یا گذرنامه را در همان خانهٔ «کد ملی» نگه داریم یا در خانهٔ جدا؟ (برای تکراری‌یابی و گزارش فرقی دارد.)
۳. **نسخهٔ دوم پشتیبان** کجا باشد: رایانهٔ دوم در شبکه، دیسک خارجی، یا هر دو؟ و آیا کسی هر شب پشتیبان دستی روی فلش می‌گیرد؟ (Q-15)
۴. **تمرین بازیابی ماهانه** را چه کسی انجام دهد؟ (O01)
۵. **ساعت شروع شیفت‌ها** همان ۷:۳۰ / ۱۳:۳۰ / ۱۹:۳۰ ثابت بماند یا مدیر خودش بتواند عوض کند؟ همین سؤال برای آستانه‌های «حذف غیرعادی» (۲۰ حذف) و «فاکتور باز قدیمی» (۶ ساعت).
۶. **واحد مبلغ تومان است؟** (Q-23، در عمل فرض شده.)
۷. **نقد ۰٫۲٪ پرداخت‌هاست**: واقعاً نقد کم است یا نقدها کارتی ثبت می‌شوند؟ (Q-06؛ برای صندوق اپ شیفت مهم است.)
۸. **ست سرم/آنژیوکت:** قاعدهٔ «هر خدمت ← این مصرفی» تعریف شود یا پذیرش موظف به ثبت باشد؟ (Q-07)
۹. **تخفیف/کمپین** در حسابداری چطور ثبت شود؟ (Q-12)
۱۰. کدام exe و کدام رایانه در درمانگاه اجراست؟ (Q-03؛ ACC-0.9) — استقرار طبق تصمیم شما بعداً.
۱۱. کپی‌های اضافهٔ دیتابیس واقعی (`Desktop/clinic_new.db` و `Downloads/clinic_new.db`) پاک شوند؟ (Q-20) و BitLocker روشن است؟ (Q-13)
۱۲. حذف کاربر: واقعاً پاک شود، یا فقط غیرفعال بماند (پیشنهاد: فقط غیرفعال)؟

---

## ۱۶. فهرست جمع‌بندی ادعاهای کهنهٔ اسناد (برای به‌روزرسانی)

1. `docs/02_ROADMAP.md`: وضعیت فاز ۱ («منتظر ادغام PR»)، دروازهٔ ۱ («2.0.0»)، وضعیت فاز ۳ («SHF-1 در حال انجام»).
2. `docs/00_README.md`: ساختار «پیشنهادی» و فهرست ناقص (A08, E04, O04).
3. `docs/accounting/A05_WORK_PLAN.md`: ACC-1.2 (۱۴/۸/۱۲)، ACC-1.5 (`secret_key.txt`)، ACC-2.11 (منتظر Q-08)، ACC-2.12 (بدون وضعیت)، سطر «مهاجرت ۴» در بخش وضعیت.
4. `docs/accounting/A06_MANAGER_AREA_SPEC.md` §۷: «راهی برای بازگشایی فاکتور بسته ندارد».
5. `docs/accounting/A02`، `A04`: ساختار پوشه و نام ماتریس/نقش `doctor` با واقعیت فرق دارد.
6. `docs/ops/releases/accounting-2.1.0.md` و `apps/accounting/CHANGELOG.md`: «نسخهٔ دیتابیس ۴»؛ واقعی ۷.
7. `docs/ops/O01_BACKUP_AND_RECOVERY.md` §۲: پایش پشتیبان «⏳ فاز ۲» اما انجام شده.
8. `docs/ops/O03_STAFF_COMMUNICATION_AND_TRAINING.md` §۱: «صفحه‌های پذیرش بی‌تغییر».
9. `docs/engineering/E03_DEV_ENVIRONMENT.md`: کاربر `doctor1`. `docs/engineering/E04_PROJECT_MAP.md`: «No code yet» برای شیفت و «ADR-001..013».
10. `docs/05_OPEN_QUESTIONS.md`: Q-23 و Q-24 عملاً پاسخ داده/حل‌شده‌اند ولی باز نمایش داده می‌شوند؛ ترتیب تاریخچه.
