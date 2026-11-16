"""F6: a browser sketch sees the variables before it, as on the desktop. `sketch.open` gives the session the
variables in force at the sketch's position (before an existing sketch feature, before the rollback bar for a new
one), not the end of the tab's. Each test builds what it needs, so each passes alone."""
import asyncio  # noqa: F401
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aiohttp  # noqa: E402
import pytest  # noqa: E402

from conftest import TOP, add, sketch_feature  # noqa: E402
from freeshape.document import Document  # noqa: E402
from test_sketch_session import _ask, _connect, _open_doc, _run, root  # noqa: E402,F401


def _doc_file(r, rollback=None):
    """The Document's tab file replaced by: variable #a = 5, a sketch holding one line, variable #b = 7.
    Returns the sketch's feature id and the line's entity id."""
    doc = Document()
    add(doc, "variable", name="a", expr="5")
    sid = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(10, 0)))
    add(doc, "variable", name="b", expr="7")
    line = next(k for k, e in doc.feature(sid)["params"]["sketch"]["entities"].items() if e["type"] == "line")
    if rollback is not None:
        doc.set_rollback(rollback)
    doc.save(os.path.join(r.root, "Doc", "bracket.fshape"))
    return sid, line


def _opened(r, msg, then):
    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            got = await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", **msg})
            assert got["type"] == "sketch.opened", got
            return await then(ws, got)
    return _run(go())


def test_an_existing_sketch_takes_a_dimension_of_a_variable_before_it_and_refuses_one_after_it(root):
    r = root()
    sid, line = _doc_file(r)
    _open_doc(r)

    async def then(ws, got):
        made = await _ask(ws, {"type": "sketch.dimension", "picks": [line], "value": "#a*2"})
        assert made["type"] == "sketch.changed" and made["made"]["value"] == pytest.approx(10.0)
        cid = made["made"]["constraint"]
        before = await _ask(ws, {"type": "sketch.state"})
        bad = await _ask(ws, {"type": "sketch.dimension.set", "constraint": cid, "value": "#b"})
        assert bad["type"] == "error" and bad["for"] == "sketch.dimension.set" and "value" in bad["message"]
        after = await _ask(ws, {"type": "sketch.state"})
        assert after["sketch"] == before["sketch"]
    _opened(r, {"feature": sid}, then)


async def _sees(ws, line):
    """Which of #a and #b a dimension on the open sketch accepts."""
    seen = set()
    for name in ("a", "b"):
        got = await _ask(ws, {"type": "sketch.dimension", "picks": [line], "value": f"#{name}"})
        if got["type"] == "sketch.changed":
            seen.add(name)
            await _ask(ws, {"type": "sketch.undo"})
    return seen


def _new_sketch_sees(root, rollback):
    r = root()
    _doc_file(r, rollback)
    _open_doc(r)

    async def then(ws, got):
        made = await _ask(ws, {"type": "sketch.add", "shape": "line", "picks": [[0.0, 0.0], [10.0, 0.0]]})
        assert made["type"] == "sketch.changed", made
        return await _sees(ws, made["made"]["entities"][0])
    return _opened(r, {"plane": TOP}, then)


def test_a_new_sketch_with_the_bar_at_the_end_sees_both_variables(root):
    assert _new_sketch_sees(root, None) == {"a", "b"}


def test_a_new_sketch_with_the_bar_between_them_sees_only_the_first(root):
    assert _new_sketch_sees(root, 2) == {"a"}
