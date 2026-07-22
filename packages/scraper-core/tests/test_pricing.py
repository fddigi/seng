from __future__ import annotations

import pytest

from scraper_core.pricing import parse_price


def test_parse_price_requires_explicit_unit():
    with pytest.raises(ValueError):
        parse_price("100", unit="not-a-real-unit")  # type: ignore[arg-type]


def test_parse_price_danish_free_text_dot_thousands_comma_decimal():
    """Regression test: Reshopper/DBA free text, e.g. "1.234,56 kr."."""
    assert parse_price("1.234,56 kr.", unit="major") == pytest.approx(1234.56)


def test_parse_price_dba_shipping_text():
    assert parse_price("Fragt fra 29,99 kr.", unit="major") == pytest.approx(29.99)


def test_parse_price_vinted_dot_decimal_string():
    """Regression test: Vinted uses "." as the decimal separator - the
    OPPOSITE convention from Reshopper's free text, same currency."""
    assert parse_price("47.26", unit="major") == pytest.approx(47.26)


def test_parse_price_sellpy_minor_units_oere():
    """Regression test: the real production bug - Sellpy returns an integer
    count of oere. Forgetting unit="minor" makes every price 100x too high."""
    assert parse_price(12345, unit="minor") == pytest.approx(123.45)
    assert parse_price(12345, unit="major") == pytest.approx(12345.0)


def test_parse_price_numeric_input_passthrough():
    assert parse_price(349.0, unit="major") == pytest.approx(349.0)


def test_parse_price_none_and_empty_return_none():
    assert parse_price(None, unit="major") is None
    assert parse_price("", unit="major") is None
    assert parse_price("kr.", unit="major") is None


def test_parse_price_forced_decimal_style_overrides_auto():
    # "dot" style treats "," as a thousands separator to strip, not a decimal point.
    assert parse_price("1,234", unit="major", decimal_style="dot") == pytest.approx(1234.0)
    # "comma" style treats "," as the decimal point.
    assert parse_price("1,234", unit="major", decimal_style="comma") == pytest.approx(1.234)


def test_parse_price_auto_cannot_distinguish_thousands_dot_from_decimal_dot():
    """Documents a real, confirmed limitation (Fund 16) rather than a bug to
    fix: "1.234" (German/Danish thousands, no decimal) and "47.26" (Vinted
    decimal) have identical structure - one dot, no comma - so "auto" always
    treats a lone dot as decimal. Callers with a German/Danish-style source
    MUST pass decimal_style="comma" explicitly; "auto" silently gives the
    wrong answer for that convention, as asserted here."""
    assert parse_price("1.234", unit="major", decimal_style="auto") == pytest.approx(1.234)
    assert parse_price("1.234", unit="major", decimal_style="comma") == pytest.approx(1234.0)
    assert parse_price("47.26", unit="major", decimal_style="auto") == pytest.approx(47.26)


def test_parse_price_space_as_thousands_separator():
    """Regression test (Fund 17): Scandinavian/French formatting uses a plain
    or non-breaking space as the thousands separator - previously stopped
    parsing at the first space and silently returned only the first digit
    group (e.g. 8.0 instead of 8500.0)."""
    assert parse_price("8 500 kr", unit="major") == pytest.approx(8500.0)
    assert parse_price("1 234 kr", unit="major") == pytest.approx(1234.0)
    assert parse_price("1\xa0234,56 kr", unit="major") == pytest.approx(1234.56)
