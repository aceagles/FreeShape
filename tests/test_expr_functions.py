import pytest

from freeshape.expr import ExprError, parse_number


def ev(text, **vars):
    return parse_number(text, variables=vars or None, strict=True)


@pytest.mark.parametrize("text,want", [
    ("atan2(1, 1)", 45), ("asin(0.5)", 30), ("acos(0)", 90), ("atan(1)", 45),
    ("floor(2.7)", 2), ("ceil(2.1)", 3), ("exp(log(5))", 5), ("log10(1000)", 3),
    ("500 um", 0.5), ("500um", 0.5), ("1 m", 1000), ("2 mm", 2), ("1m + 500 um", 1000.5),
    ("log10(1000) mm", 3),
])
def test_values(text, want):
    assert ev(text) == pytest.approx(want, abs=1e-9)


@pytest.mark.parametrize("text", ["asin(2)", "acos(-2)", "log(0)", "log10(-1)", "log(-3)"])
def test_domain_errors(text):
    with pytest.raises(ExprError):
        ev(text)
    assert parse_number(text) is None


def test_variables_with_functions():
    assert ev("atan2(#h, #w)", h=3, w=4) == pytest.approx(36.8699, abs=1e-4)


def test_comma_decimal_outside_calls_argument_separator_inside():
    assert ev("1,5 mm") == 1.5
    assert ev("max(1,5) + 1,5") == pytest.approx(6.5)
    assert ev("(1,5)*2") == 3
