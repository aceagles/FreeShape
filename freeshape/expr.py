"""Numbers that accept expressions, units and variables, like Onshape's fields:
"25/2", "1 in", "3*4 mm", "#wall * 2 + 1".  No Qt here — the geometry engine uses it too."""
from __future__ import annotations

import ast
import math
import operator
import re

_UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, '"': 25.4, "ft": 304.8,
          "deg": 1.0, "°": 1.0, "rad": 180.0 / math.pi, "um": 0.001}

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda d: math.sin(math.radians(d)), "cos": lambda d: math.cos(math.radians(d)),
          "tan": lambda d: math.tan(math.radians(d)), "abs": abs, "min": min, "max": max, "round": round}


def _domain(name, fn, ok):
    """Wrap a math function so an argument outside its domain raises a plain ExprError."""
    def call(*args):
        if not ok(*args):
            raise ExprError(f"{name}() is not defined for {', '.join(f'{a:g}' for a in args)}")
        return fn(*args)
    return call


_FUNCS.update({
    "asin": _domain("asin", lambda x: math.degrees(math.asin(x)), lambda x: -1 <= x <= 1),
    "acos": _domain("acos", lambda x: math.degrees(math.acos(x)), lambda x: -1 <= x <= 1),
    "atan": lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "floor": math.floor, "ceil": math.ceil,
    "log": _domain("log", math.log, lambda x: x > 0),
    "log10": _domain("log10", math.log10, lambda x: x > 0),
    "exp": math.exp,
})
_CONST = {"pi": math.pi, "e": math.e}


class ExprError(ValueError):
    """An expression couldn't be evaluated; the message is meant to be shown to the user as-is."""


class ExprLimitError(ExprError):
    """Raised when evaluating an expression would be too expensive: an exponent or a result beyond the
    fixed caps below. Caught the same way as any other ExprError -- it exists as its own type only so a
    caller that wants to explain *why* (rather than just report failure) can tell the two apart."""


MAX_EXPONENT = 1000      # 9 ** 9 ** 9 is rejected on its outer '**' (exponent 387420489), well before a
                          # `pow` call would ever start chewing through a number that large
MAX_MAGNITUDE = 1e12      # generous for any real CAD length (mm) or angle (deg); catches runaway results


def _cap(v):
    if math.isnan(v) or abs(v) > MAX_MAGNITUDE:
        raise ExprLimitError(f"result is too large (over {MAX_MAGNITUDE:g})")
    return v


def _apply(fn, *args):
    """Call fn(*args), turning the OverflowError a too-large-but-finite-exponent float op can still raise
    (e.g. 1000 ** 300) into the same ExprLimitError a too-large result gets from _cap."""
    try:
        return fn(*args)
    except OverflowError:
        raise ExprLimitError(f"result is too large (over {MAX_MAGNITUDE:g})") from None


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return _cap(float(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ExprLimitError(f"exponent is too large (over {MAX_EXPONENT})")
        return _cap(_apply(_OPS[type(node.op)], left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _cap(_apply(_OPS[type(node.op)], _eval(node.operand)))
    if isinstance(node, ast.Name) and node.id in _CONST:
        return _CONST[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _cap(_apply(lambda *a: float(_FUNCS[node.func.id](*a)), *[_eval(a) for a in node.args]))
    raise ValueError("bad expression")


VAR_RE = re.compile(r"#([A-Za-z_][A-Za-z0-9_]*)")


def has_vars(text) -> bool:
    return isinstance(text, str) and bool(VAR_RE.search(text))


def var_names(text):
    return VAR_RE.findall(text) if isinstance(text, str) else []


def _split_terms(t):
    """[(+1|-1, term), ...] split at + / - outside parentheses (not exponent signs)."""
    out, depth, start, sign = [], 0, 0, 1
    i = 0
    while i < len(t):
        ch = t[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch in "+-" and depth == 0:
            prev = t[:i].rstrip()
            if prev and prev[-1] not in "*/(+-eE":
                out.append((sign, t[start:i]))
                sign = 1 if ch == "+" else -1
                start = i + 1
        i += 1
    out.append((sign, t[start:]))
    return [(sg, term.strip()) for sg, term in out if term.strip()] if len(out) > 1 else [(1, t)]


def _decimal_commas(t):
    """A comma is a decimal separator ("1,5 mm") except inside a function call's parentheses, where it
    separates arguments ("atan2(1, 1)")."""
    out, stack = [], []
    for i, ch in enumerate(t):
        if ch == "(":
            prev = t[:i].rstrip()
            stack.append(bool(prev) and (prev[-1].isalnum() or prev[-1] == "_"))
        elif ch == ")" and stack:
            stack.pop()
        out.append("." if ch == "," and not any(stack) else ch)
    return "".join(out)


def _parse(text, angle=False, variables=None):
    """Raising core of parse_number: returns a float in mm (or degrees), or raises ExprError (including
    ExprLimitError for the cost caps above) naming why."""
    if isinstance(text, (int, float)):
        return float(text)
    if text is None or not str(text).strip():
        raise ExprError("enter a value or expression")
    t = _decimal_commas(str(text).strip()).replace("×", "*")
    if "#" in t:
        missing = []

        def sub(m):
            v = (variables or {}).get(m.group(1))
            if v is None:
                missing.append(m.group(1))
                return "0"
            return f"({float(v)!r})"
        t = VAR_RE.sub(sub, t)
        if missing:
            raise ExprError(f"unknown variable #{missing[0]}")
    # split at top-level + and - so a unit belongs to its own term: "#wall + 1/2 in"
    terms = _split_terms(t)
    if len(terms) > 1:
        total = 0.0
        for sign, term in terms:
            total += sign * _parse(term, angle, variables)
        return total
    units_alt = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
    # a single trailing unit applies to the whole expression: "1/2 in" -> (1/2)*25.4
    m = re.match(r"^(.*?)\s*(" + units_alt + r")$", t)
    if m and m.group(1) and not re.search(r"\d\s*(" + units_alt + r")(?![A-Za-z])", m.group(1)):
        return _parse(m.group(1), angle, variables) * _UNITS[m.group(2)]

    # otherwise apply units attached to individual numbers: "1in + 5mm"
    def unit_sub(m):
        num, unit = m.group(1), m.group(2)
        return f"({num}*{_UNITS[unit]!r})"
    pattern = r"(\d+(?:\.\d*)?|\.\d+)\s*(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")(?![A-Za-z])"
    t = re.sub(pattern, unit_sub, t)
    try:
        return _eval(ast.parse(t, mode="eval"))
    except ExprLimitError:
        raise
    except Exception as e:
        raise ExprError(str(e) or "bad expression") from e


def parse_number(text, angle=False, variables=None, *, strict=False):
    """Returns a float in mm (or degrees), or None if the text can't be parsed
    (including when it uses a variable that isn't defined). Pass strict=True to raise ExprError (with a
    human-readable message, see _parse) instead of returning None -- for callers that want to report why
    evaluation failed, such as Variable Studio naming the row."""
    try:
        return _parse(text, angle, variables)
    except ExprError:
        if strict:
            raise
        return None


def fmt(v, digits=4) -> str:
    if v is None:
        return ""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s
