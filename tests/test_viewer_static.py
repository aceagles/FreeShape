"""I5.5: the dependency-free browser viewer (freeshape/static/viewer.html), served at `/` by `serve`.

No test here runs the page; these check what can be checked without a browser: it is served with the right
headers, ships as package data, loads nothing from outside, never builds HTML from Document text, and its script
has balanced brackets. A real syntax check (`node --check`) is a manual step."""
import re
from html.parser import HTMLParser
from importlib import resources

import pytest
from test_serve import _serve


def _page():
    return resources.files("freeshape").joinpath("static/viewer.html").read_text(encoding="utf-8")


class _Parts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.styles, self.links, self.srcs, self._in = [], [], [], [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._in = tag
            (self.scripts if tag == "script" else self.styles).append("")
        for k in ("src", "href", "action", "poster", "data"):
            if k in a:
                self.srcs.append((tag, k, a[k]))
        if tag == "link":
            self.links.append(a)

    def handle_endtag(self, tag):
        if tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._in == "script":
            self.scripts[-1] += data
        elif self._in == "style":
            self.styles[-1] += data


def _parse():
    p = _Parts()
    p.feed(_page())
    p.close()
    return p


def balanced(src):
    """True when (), [] and {} balance in JavaScript `src`, skipping strings, template literals (with ${ } nesting),
    comments and regex literals. A regex is taken to start at a `/` after an operator or an opening bracket."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack, i, n, last = [], 0, len(src), ""          # `last` is the last significant character seen
    body = False                                      # True: scanning the text of a template literal

    while i < n:
        c = src[i]
        if body:                                      # inside `...`; "$" on the stack marks an open ${ }
            if c == "\\":
                i += 2
            elif c == "`":
                body, last, i = False, "a", i + 1
            elif src.startswith("${", i):
                stack.append("$")
                body, last, i = False, "{", i + 2
            else:
                i += 1
        elif c in " \t\r\n":
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                return False
            i = j + 2
        elif c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\n":
                    return False
                j += 2 if src[j] == "\\" else 1
            if j >= n:
                return False
            i, last = j + 1, "a"
        elif c == "`":
            body, i = True, i + 1
        elif c == "/" and (last == "" or last in "(,=:[!&|?{};+-*%<>~^"):
            j, in_class = i + 1, False               # a regex literal
            while j < n and (src[j] != "/" or in_class):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "\n":
                    return False
                j += 1
            if j >= n:
                return False
            i, last = j + 1, "a"
        elif c in "([{":
            stack.append(c)
            i, last = i + 1, c
        elif c in ")]}":
            if c == "}" and stack and stack[-1] == "$":      # the end of a ${ } inside a template literal
                stack.pop()
                body, i = True, i + 1
            elif not stack or stack.pop() != pairs[c]:
                return False
            else:
                i, last = i + 1, c
        else:
            i, last = i + 1, c
    if body:
        return False
    return not stack


# ---------------------------------------------------------------- the scanner itself
def test_scanner_skips_strings_comments_regexes_and_templates():
    assert balanced("const a = '}'; // ) ( {\nlet b = /[)}]+\\//.test(x); /* ] */ f(a, [1], {k: 2});")
    assert balanced("const t = `a ${ f({x: `}`}) } ) b`; g(t);")
    assert balanced("if (a / b > c) { d = e / f; }")                 # division is not a regex
    assert not balanced("f(a, [1)")
    assert not balanced("function f() { return '}'; ")
    assert not balanced("x = `${ a `")


# ---------------------------------------------------------------- the file
def test_the_file_ships_as_package_data_and_is_small():
    page = _page()
    assert page.lstrip().lower().startswith("<!doctype html>")
    assert page.count("\n") < 600


def test_it_uses_the_tabs_and_mesh_routes_and_nothing_remote():
    page = _page()
    assert '"/tabs"' in page and "mesh.json" in page
    assert not re.search(r"https?:", page, re.I)
    assert not re.search(r"""["'`(=,]\s*//[\w.-]""", page)           # a protocol-relative URL
    assert not re.search(r"\bimport\s*[({\w*]|\bimportScripts\b|\bimport\s+.*\bfrom\b", _parse().scripts[0])
    assert "sourceMappingURL" not in page and "@import" not in page and "url(" not in page


def test_it_never_builds_html_from_text_or_evaluates_code():
    page = _page()
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                "setTimeout(\"", "setInterval(\"", "srcdoc"):
        assert bad not in page, bad


def test_it_is_one_script_one_style_and_loads_no_other_file():
    p = _parse()
    assert len(p.scripts) == 1 and len(p.styles) == 1 and p.scripts[0].strip() and p.styles[0].strip()
    assert p.srcs == [] and p.links == []


def test_the_script_has_balanced_brackets():
    assert balanced(_parse().scripts[0])
    assert balanced(_parse().styles[0])


# ---------------------------------------------------------------- served
@pytest.fixture(scope="module")
def served(tmp_path_factory):
    s, mp = _serve(tmp_path_factory, "viewer")
    try:
        yield s
    finally:
        s.close()
        mp.undo()


def test_get_root_returns_the_page_with_a_strict_csp(served):
    status, h, body = served.request("GET", "/")
    assert status == 200 and body == _page().encode("utf-8")
    assert h["Content-Type"] == "text/html; charset=utf-8"
    assert h["X-Content-Type-Options"] == "nosniff" and h["Cache-Control"] == "no-store"
    assert h["Content-Security-Policy"] == (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    assert not any(k.lower().startswith("access-control") for k in h)


def test_a_missing_page_is_a_plain_404(served, monkeypatch):
    from freeshape import serve as SV
    monkeypatch.setattr(SV, "_viewer_page", lambda: None)
    status, h, body = served.request("GET", "/")
    assert status == 404 and h["Content-Type"].startswith("text/plain") and b"not installed" in body
