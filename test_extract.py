"""Tests for the deterministic parts of the pipeline -- the pieces that must
never silently drift, because they are what stops a currency figure from
being wrong by a factor of 1000.

Run with:  python test_extract.py       (no dependencies, no API calls)
       or: pytest test_extract.py

Every string in AMOUNT_CASES below was taken from a real report processed by
this pipeline, so these are regression tests against actual documents rather
than invented examples.
"""

from __future__ import annotations

import sys

from extract import _apply_deterministic_values, _parse_amount
from schemas import CreditReport, FinancialYear, Money

# (raw string as printed in the report, expected numeric value)
AMOUNT_CASES = [
    # Creditsafe (UK/EU) -- K suffixes and plain thousands separators
    ("£2.5K", 2500.0),
    ("£4K", 4000.0),
    ("£248,043", 248043.0),
    ("£233,885", 233885.0),
    ("£100", 100.0),
    ("€60,000", 60000.0),
    ("€2,000,000", 2000000.0),
    # CRIF / international -- currency code before or after the number
    ("3,000 US$", 3000.0),
    ("UAE Dh 150,000", 150000.0),
    ("USD 1,300,000", 1300000.0),
    ("USD 245,000", 245000.0),
    # Word-form multipliers, and a trailing parenthetical that must NOT be
    # mistaken for accounting-style negative notation
    ("4.7 million (US dollars)", 4700000.0),
    # GLADTRUST
    ("USD 27,850,000.00", 27850000.0),
    ("Above USD 1,400,000", 1400000.0),
    ("Above EUR 1,300,000", 1300000.0),
    ("CNY 3,000,000.00", 3000000.0),
    ("CNY 30,000,000.00", 30000000.0),
    # Bare figures from unit-scaled financial tables (the multiplier is
    # applied separately, from FinancialYear.unit_multiplier)
    ("38,016", 38016.0),
    ("663,294", 663294.0),
    ("-106,164", -106164.0),
    # Accounting-style negatives
    ("(1,234)", -1234.0),
    ("$(1,234)", -1234.0),
    # Nothing parseable
    (None, None),
    ("", None),
    ("-", None),
    ("n/a", None),
]

# (raw, unit_multiplier, expected value) -- the 1000x bug this pipeline hit
# in production: a table captioned "UNIT: CNY 1,000" whose cells are printed
# unscaled.
UNIT_CASES = [
    ("663,294", 1000, 663294000.0),   # DuPont, "Assets(UNIT: CNY 1,000)"
    ("800,185", 1000, 800185000.0),
    ("38,016", 10000, 380160000.0),   # DRESSER, "Currency In Ten Thousand CNY"
    ("41,972", 10000, 419720000.0),
    ("58,568,711", 1, 58568711.0),    # CEFLA, no unit label -> already absolute
]


def _check(label, got, expected, failures):
    if got != expected:
        failures.append(f"{label}: got {got!r}, expected {expected!r}")
        return False
    return True


def test_parse_amount():
    failures = []
    for raw, expected in AMOUNT_CASES:
        _check(f"_parse_amount({raw!r})", _parse_amount(raw), expected, failures)
    assert not failures, "\n".join(failures)


def test_unit_multiplier_applied():
    """A financials row's unit_multiplier must scale every money field in
    that row -- this is what prevents the 1000x understatement."""
    failures = []
    for raw, multiplier, expected in UNIT_CASES:
        report = CreditReport(
            financials=[
                FinancialYear(
                    year="2023",
                    unit_multiplier=multiplier,
                    turnover=Money(raw=raw, value=None, currency="CNY"),
                )
            ]
        )
        _apply_deterministic_values(report)
        got = report.financials[0].turnover.value
        _check(f"{raw!r} x{multiplier}", got, expected, failures)
    assert not failures, "\n".join(failures)


def test_top_level_money_not_scaled():
    """Only financials rows carry a unit multiplier. A top-level figure like
    credit_limit is already absolute and must be left alone."""
    report = CreditReport(
        credit_limit=Money(raw="Above USD 1,400,000", value=None, currency="USD"),
        financials=[FinancialYear(year="2023", unit_multiplier=1000)],
    )
    _apply_deterministic_values(report)
    assert report.credit_limit.value == 1400000.0, report.credit_limit.value


def test_model_supplied_value_is_overridden():
    """The whole point of the deterministic pass: whatever arithmetic the
    model did is discarded and recomputed from the printed text."""
    report = CreditReport(
        credit_limit=Money(raw="£2.5K", value=999999.0, currency="GBP"),
    )
    _apply_deterministic_values(report)
    assert report.credit_limit.value == 2500.0, report.credit_limit.value


def test_currency_symbol_preserved():
    """`raw` is the source of truth for what was printed and must never be
    rewritten by the deterministic pass."""
    report = CreditReport(credit_limit=Money(raw="3,000 US$", value=None, currency="USD"))
    _apply_deterministic_values(report)
    assert report.credit_limit.raw == "3,000 US$", report.credit_limit.raw


def main() -> int:
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}\n      " + str(e).replace("\n", "\n      "))
        else:
            print(f"ok    {fn.__name__}")
    total = len(tests)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
