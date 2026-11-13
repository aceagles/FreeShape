"""Caps on the cost of evaluating an expression in freeshape/expr.py: a huge exponent or a huge result
must fail fast with a clear error rather than hang or chew memory -- see freeshape/variable_studio.py,
where a row from a received Document could otherwise contain `9 ** 9 ** 9`."""
import time

import pytest

from freeshape.expr import ExprLimitError, parse_number


def _timed(text):
    start = time.monotonic()
    try:
        return parse_number(text, strict=True), None, time.monotonic() - start
    except Exception as e:
        return None, e, time.monotonic() - start


def test_huge_exponent_chain_fails_fast_with_a_clear_error():
    value, err, elapsed = _timed("9 ** 9 ** 9")
    assert value is None
    assert isinstance(err, ExprLimitError)
    assert "exponent" in str(err)
    assert elapsed < 1.0


def test_huge_power_of_ten_fails_fast_with_a_clear_error():
    value, err, elapsed = _timed("10 ** 100000")
    assert value is None
    assert isinstance(err, ExprLimitError)
    assert elapsed < 1.0


def test_small_exponent_still_evaluates():
    assert parse_number("2 ** 10") == 1024.0


def test_non_strict_mode_still_just_returns_none():
    assert parse_number("9 ** 9 ** 9") is None
    assert parse_number("10 ** 100000") is None


def test_huge_result_from_a_safe_exponent_is_also_rejected():
    # 1000 ** 300 has a within-limit exponent but a result far past MAX_MAGNITUDE
    with pytest.raises(ExprLimitError):
        parse_number("1000 ** 300", strict=True)
