"""The pure domain layer (ACC-2.2): money, identity, shifts, Jalali dates and the billing rules."""

import math
from datetime import date, datetime

import pytest

from src.domain import billing, identity, jalali, money, shifts

# --------------------------------------------------------------------------- money


@pytest.mark.parametrize(
    "text, expected",
    [
        ("120,000", 120000.0),
        ("۱۲۰٬۰۰۰", 120000.0),  # Persian digits + Arabic thousands separator
        ("١٢٠،٠٠٠", 120000.0),  # Arabic-Indic digits + Arabic comma
        (" 5000 ", 5000.0),
        ("0", 0.0),
        ("12.5", 12.5),
        (".5", 0.5),
        (7, 7.0),
        (7.25, 7.25),
        ("1 200 000", 1200000.0),
        ("1‌200", 1200.0),  # zero-width non-joiner typed between digits
    ],
)
def test_parse_amount_understands_what_people_type(text, expected):
    assert money.parse_amount(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "abc", "12abc", "1,2,x", "--5", "-5", "1e5", "nan", "inf", None, True, "٪٪", float("nan"), -1, float("inf")],
)
def test_parse_amount_refuses_non_amounts(text):
    assert money.parse_amount(text) is None
    assert money.parse_amount(text, default=0.0) == 0.0


def test_parse_amount_never_returns_a_non_finite_number():
    for text in ("1" * 400, "9" * 330):
        value = money.parse_amount(text)
        assert value is None or math.isfinite(value)


@pytest.mark.parametrize(
    "value, expected",
    [
        (12000, "12,000"),
        (12000.0, "12,000"),
        (0, "0"),
        (None, "0"),
        ("abc", "0"),
        (1234567.5, "1,234,567.50"),
        ("۱۲۰٬۰۰۰", "120,000"),
    ],
)
def test_format_amount(value, expected):
    assert money.format_amount(value) == expected


def test_normalize_digits():
    assert money.normalize_digits("۰۹۱۲٣٤٥") == "0912345"
    assert money.normalize_digits(None) == ""


# --------------------------------------------------------------------------- identity


@pytest.mark.parametrize("nid", ["2170415981", "2110530979", "0499370899"])
def test_valid_national_ids(nid):
    assert identity.is_valid_national_id(nid)
    assert identity.is_valid_national_id(nid.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")))  # Persian digits


@pytest.mark.parametrize(
    "nid",
    [
        "1234567890",
        "0000000000",
        "1111111111",
        "123456789",
        "12345678901",
        "abcdefghij",
        "",
        None,
        "2170415982",
        "۲۱۷۰۴۱۵۹۸۲",
    ],
)
def test_invalid_national_ids(nid):
    assert not identity.is_valid_national_id(nid)


def test_national_id_with_surrounding_spaces_is_accepted_after_normalising():
    assert identity.is_valid_national_id("  2170415981 ")


def test_every_check_digit_branch_is_exercised():
    """remainder < 2 (check == remainder) and remainder >= 2 (check == 11 - remainder)."""
    seen = set()
    for n in range(1, 4000):
        body = f"{n:09d}"
        if len(set(body)) == 1:
            continue
        total = sum(int(body[i]) * (10 - i) for i in range(9)) % 11
        check = total if total < 2 else 11 - total
        seen.add(total < 2)
        assert identity.is_valid_national_id(body + str(check))
        assert not identity.is_valid_national_id(body + str((check + 1) % 10))
    assert seen == {True, False}


@pytest.mark.parametrize(
    "phone, ok",
    [
        ("09123456789", True),
        ("۰۹۱۲۳۴۵۶۷۸۹", True),
        ("9123456789", False),
        ("02112345678", False),
        ("0912345678", False),
        ("0912345678a", False),
        ("", False),
        (None, False),
    ],
)
def test_phone(phone, ok):
    assert identity.is_valid_phone(phone) is ok


def test_old_validator_module_still_exports_the_same_functions():
    from src.common import validators

    assert validators.validate_iranian_national_id("2170415981") and validators.validate_iranian_phone("09123456789")


# --------------------------------------------------------------------------- shifts


def test_shift_order_label_and_sort_key():
    assert shifts.SHIFTS == ("morning", "evening", "night")
    assert [shifts.shift_rank(s) for s in ("morning", "evening", "night", None, "bogus")] == [0, 1, 2, 3, 3]
    assert shifts.shift_label("evening") == "عصر" and shifts.shift_label(None) == "" and shifts.shift_label("x") == "x"
    pairs = [("2026-09-24", "night"), ("2026-09-23", "night"), ("2026-09-24", "morning"), (None, "morning")]
    assert sorted(pairs, key=lambda p: shifts.sort_key(*p)) == [
        (None, "morning"),
        ("2026-09-23", "night"),
        ("2026-09-24", "morning"),
        ("2026-09-24", "night"),
    ]


# --------------------------------------------------------------------------- jalali


def test_to_jalali_and_text():
    assert jalali.to_jalali("2026-09-24") == (1405, 7, 2)
    assert jalali.to_jalali("2026-09-24 11:40:16") == (1405, 7, 2)
    assert jalali.to_jalali(date(2026, 3, 21)) == (1405, 1, 1)
    assert jalali.to_jalali(datetime(2026, 3, 20, 23, 59)) == (1404, 12, 29)
    assert jalali.to_jalali(None) is None and jalali.to_jalali("") is None and jalali.to_jalali("garbage") is None
    assert (
        jalali.jalali_text("2026-09-24") == "1405/07/02"
        and jalali.jalali_text("bad") == "—"
        and jalali.jalali_text(None, "?") == "?"
    )


@pytest.mark.parametrize("text", ["1405/07/02", "۱۴۰۵/۰۷/۰۲", "1405-07-02", " 1405/7/2 "])
def test_parse_jalali_accepts_common_spellings(text):
    assert jalali.parse_jalali(text) == date(2026, 9, 24)


@pytest.mark.parametrize(
    "text", [None, "", "1405/13/01", "1405/07/32", "1405/07", "a/b/c", "1405/07/02/01", "1404/12/30"]
)
def test_parse_jalali_rejects_impossible_dates(text):
    # 1404 is not a leap year, so 1404/12/30 does not exist
    assert jalali.parse_jalali(text) is None


def test_parse_jalali_roundtrip_over_two_years():
    from datetime import timedelta

    d = date(2025, 3, 1)
    while d < date(2027, 3, 1):
        assert jalali.parse_jalali(jalali.jalali_text(d)) == d
        d += timedelta(days=1)


def test_month_start_and_elapsed_days():
    assert jalali.month_start(date(2026, 9, 24)) == date(2026, 9, 23)  # 1405/07/01
    assert jalali.elapsed_days_in_month(date(2026, 9, 24)) == 2
    assert jalali.elapsed_days_in_month(date(2026, 9, 23)) == 1
    assert jalali.elapsed_days_in_month(date(2027, 3, 20)) == 29  # last day of 1405 (non-leap)


def test_date_range_defaults_swaps_and_parses():
    today = date(2026, 9, 24)
    assert jalali.date_range("1405/07/01", "1405/07/02", 7, today) == (date(2026, 9, 23), date(2026, 9, 24))
    assert jalali.date_range("1405/07/02", "1405/07/01", 7, today) == (date(2026, 9, 23), date(2026, 9, 24))  # swapped
    assert jalali.date_range("", "", 7, today) == (date(2026, 9, 18), today)  # last 7 days
    assert jalali.date_range("x", "1405/07/02", 30, today) == (date(2026, 8, 26), today)  # either side bad -> default
    assert jalali.date_range(None, None, 1, today) == (today, today)


# --------------------------------------------------------------------------- billing


def test_visit_shares_follow_insurance_then_supplementary():
    assert billing.visit_shares(500, None, None) == billing.Shares(500, 500, 0, 0)  # free pay
    assert billing.visit_shares(500, 100, None) == billing.Shares(500, 100, 400, 0)  # insured
    assert billing.visit_shares(500, 100, 20) == billing.Shares(500, 20, 480, 0)  # supplementary wins
    assert billing.visit_shares(500, 0, None) == billing.Shares(500, 0, 500, 1)  # fully covered
    assert billing.visit_shares(0, 0, None) == billing.Shares(0, 0, 0, 0)  # no tariff at all is not 'covered'
    assert billing.visit_shares(100, 300, None) == billing.Shares(
        100, 300, 0, 0
    )  # tariff above base: no insurance share


def test_nursing_coverage_flag_beats_the_legacy_tariff():
    assert billing.nursing_is_covered(True, 999)
    assert not billing.nursing_is_covered(False, 0)  # explicit 'no' wins even if the legacy value is 0
    assert billing.nursing_is_covered(None, 0)
    assert not billing.nursing_is_covered(None, 5)
    assert not billing.nursing_is_covered(None, None)


def test_injection_and_procedure_and_consumable_shares():
    assert billing.injection_shares(100, True, False) == billing.Shares(100, 0, 100, 1)
    assert billing.injection_shares(100, True, True) == billing.Shares(100, 100, 0, 0)  # excluded service
    assert billing.injection_shares(100, False, False) == billing.Shares(100, 100, 0, 0)
    assert billing.injection_shares(None, True, False) == billing.Shares(0, 0, 0, 1)
    assert billing.procedure_shares(100, True, True) == billing.Shares(100, 0, 100, 1)
    assert billing.procedure_shares(100, False, True) == billing.Shares(
        100, 100, 0, 0
    )  # a doctor's procedure is always paid
    assert billing.procedure_shares(100, True, False) == billing.Shares(100, 100, 0, 0)
    assert billing.consumable_shares(30) == billing.Shares(30, 30, 0, 0)


def test_shares_expose_the_item_fields_the_templates_use():
    assert billing.visit_shares(500, 100, None).as_item_fields() == {
        "recorded_price": 500.0,
        "patient_share": 100.0,
        "insurance_share": 400.0,
        "covered_by_insurance": 0,
    }


def test_payment_type_rules():
    assert billing.needs_payment_type(1) and billing.needs_payment_type(0.5)
    assert not billing.needs_payment_type(0) and not billing.needs_payment_type(None)
    assert billing.is_valid_payment_type("card") and billing.is_valid_payment_type("cash")
    assert not any(billing.is_valid_payment_type(x) for x in (None, "", "insurance", "CARD", "check"))
