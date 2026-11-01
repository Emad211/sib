"""What the purchasing chain's rules say when they refuse, and the error they raise (S01 §4).

Split out of `purchase.py` so the main store's actions (`store_actions.py`) and the chain share one error
type without importing each other. Every message is one short Persian sentence with no figure in it: the
count behind a blind check never reaches the person who made it (R-02, P-11).
"""

from shift.domain.store_payloads import MAIN_STORE as MAIN_STORE


class PurchaseRuleError(ValueError):
    """A purchase action the clinic's rules refuse; the route answers 422 with this message."""


NO_PHOTO = "عکس فاکتور را بگیرید؛ بدون آن دریافت بار ثبت نمی‌شود."
PHOTO_NOT_STORED = "این عکس روی سرور ذخیره نشده است؛ دوباره آن را بگیرید."
NOT_AWAITING = "این بار منتظر شمارش شما نیست."
NOT_THE_RECEIVER = "فقط کسی که بار به او سپرده شده است می‌تواند شمارش و تأیید کند."
# Both sides confirm (owner document §4, line «هر دو طرف»): the door's count and the store's count are two
# people's work, so the same person can never hold both halves — even when they hold both roles.
TO_SELF = "بار را نمی‌توانید به خودتان تحویل دهید؛ تحویل بار دو طرف دارد و طرف دیگر باید کس دیگری باشد."
CONFIRM_OWN = "شمردن و تأیید باری که خودتان در پذیرش تحویل گرفته‌اید ممکن نیست؛ تحویل بار دو طرف دارد."
CONFIRM_RETURNED = "این بار به فروشنده برگشته است و به انبار نمی‌رود."
CONFIRM_LINES = "شمارش باید برای همهٔ اقلام همین بار و هر قلم یک بار باشد."
CONFIRMER_IS_GIVER = "تأییدکنندهٔ شارژ قفسه باید کس دیگری جز خود شما باشد."
UNKNOWN_OFFER = "این شارژ قفسه پیدا نشد."
OFFER_CLOSED = "این شارژ قفسه قبلاً تأیید شده است."
NOT_THE_CONFIRMER = "فقط کسی که برای تأیید این شارژ نام‌برده شده است می‌تواند آن را تأیید کند."
OFFER_COUNT = "تعداد با شارژ ثبت‌شده نمی‌خواند؛ انباردار باید دوباره ثبت کند."
WEEKLY_ROLE = "شمارش هفتگی انبار را فقط انباردار انجام می‌دهد."
BASELINE_ROLE = "شمارش اولیهٔ انبار را انباردار (یا مدیر) ثبت می‌کند."
BASELINE_DONE = "شمارش اولیهٔ انبار قبلاً ثبت شده است."
BASELINE_ITEMS = "شمارش اولیه باید همهٔ اقلام انبار را دربر بگیرد، هر قلم یک بار."
NEEDS_BASELINE = "اول شمارش اولیهٔ انبار را ثبت کنید؛ بدون آن شمارش هفتگی معنی ندارد."
SAMPLE_ROLE = "نمونه‌شماری ماهانه را حسابدار انجام می‌دهد."
NOT_IN_SAMPLE = "این قلم در نمونهٔ این ماه نیست."
SAMPLE_DONE = "این قلم در این ماه شمرده شده است."

BAD_DATA = "دادهٔ این کار کامل یا درست نیست."
SAME_LOCATION = "مبدأ و مقصد شارژ قفسه نمی‌توانند یکی باشند."
UNKNOWN_LOCATION = "محل مبدأ یا مقصد در کاتالوگ نیست."
ITEM_NOT_THERE = "این قلم در قفسهٔ مقصد در کاتالوگ نیست."
NOT_A_COUNTED_ITEM = "این قلم در این محل قلم شمارشی نیست."
NOT_THE_STORE = "شمارش هفتگی فقط برای انبار اصلی ثبت می‌شود."
RESTOCK_FROM_STORE = "شارژ قفسه فقط از انبار اصلی ثبت می‌شود."
RETURN_TO_STORE = "برگشت از قفسه فقط به انبار اصلی ثبت می‌شود."
ITEM_NOT_ON_SHELF = "این قلم در قفسهٔ مبدأ در کاتالوگ نیست."
UNKNOWN_CONFIRMER = "تأییدکننده باید از کارکنان فعال درمانگاه باشد."
