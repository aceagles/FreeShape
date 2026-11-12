"""The measure panel (freeshape.measure): single items, pairs and multiple selections."""
import math
import re

import pytest

from conftest import ANALYTIC, add, assert_golden, block_doc, only_part, sketch_feature
from freeshape import kernel as K
from freeshape import measure as M

PI = math.pi


def nums(s):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s)]


def rows(m):
    return dict(m["rows"])


@pytest.fixture(scope="module")
def holed():
    """Block 40x20x10 with Ø6 through holes at (10,10) and (30,10)."""
    doc = block_doc(40, 20, 10)
    for x in (10, 30):
        hs = sketch_feature(doc, lambda s, x=x: s.add_point_entity(s.add_point(x, 10)))
        add(doc, "hole", sketch=hs, standard="custom", diameter=6.0, end="through", style="simple")
    return only_part(doc).shape


def face(shape, pred):
    return next(f for f in K.faces(shape) if pred(K.face_info(f)))


def edge(shape, pred):
    return next(e for e in K.edges(shape) if pred(K.edge_info(e)))


def top(i):
    return i.get("n") is not None and i["n"][2] > 0.99


def bottom(i):
    return i.get("n") is not None and i["n"][2] < -0.99


def side(i):
    return i.get("n") is not None and i["n"][0] > 0.99


def bore(x):
    return lambda i: abs(i.get("r", 0) - 3) < 1e-9 and abs(i["axis_p"][0] - x) < 1e-6


def rim(x):
    return lambda i: abs(i.get("r", 0) - 3) < 1e-9 and abs(i["center"][0] - x) < 1e-6 and abs(i["center"][2] - 10) < 1e-6


def long_edge(i):
    return "d" in i and abs(abs(i["d"][0]) - 1) < 1e-9 and abs(i["c"][1]) < 1e-9 and abs(i["c"][2]) < 1e-9


def test_part(holed):
    m = M.measure([("part", holed)], ["Plate"])
    r = rows(m)
    assert m["title"] == "Plate"
    v = 8000 - 2 * 9 * PI * 10
    assert abs(nums(r["Volume"])[0] - v) < 0.01
    assert abs(nums(r["Surface area"])[0] - (2800 - 4 * 9 * PI + 2 * 6 * PI * 10)) < 0.01
    assert nums(r["Size"]) == [40, 20, 10]
    assert nums(r["Centre of mass"]) == [20, 10, 5]
    assert abs(nums(r["Mass"])[0] - v * M.PLA) < 0.05


def test_single_faces_and_edges(holed):
    r = rows(M.measure([("face", face(holed, top))]))
    assert abs(nums(r["Area"])[0] - (800 - 18 * PI)) < 1e-3 and nums(r["Normal"]) == [0, 0, 1]
    r = rows(M.measure([("face", face(holed, bore(10)))]))
    assert nums(r["Diameter"]) == [6] and nums(r["Radius"]) == [3]
    r = rows(M.measure([("edge", edge(holed, long_edge))]))
    assert nums(r["Length"]) == [40] and nums(r["ΔX · ΔY · ΔZ"]) == [40, 0, 0]
    r = rows(M.measure([("edge", edge(holed, rim(30)))]))
    assert nums(r["Diameter"]) == [6] and nums(r["Centre"]) == [30, 10, 10]
    assert M.measure([("region", face(holed, top))])["title"] == "Sketch region"


def test_pairs(holed):
    r = rows(M.measure([("face", face(holed, top)), ("face", face(holed, bottom))]))
    assert nums(r["Minimum distance"]) == [10] and nums(r["Angle"]) == [0] and nums(r["Plane to plane"]) == [10]
    r = rows(M.measure([("face", face(holed, top)), ("face", face(holed, side))]))
    assert nums(r["Angle"]) == [90] and nums(r["Minimum distance"]) == [0]
    m = M.measure([("edge", edge(holed, rim(10))), ("edge", edge(holed, rim(30)))])
    assert nums(rows(m)["Centre to centre"]) == [20] and m["line"] is not None
    r = rows(M.measure([("face", face(holed, bore(10))), ("face", face(holed, bore(30)))]))
    assert nums(r["Axis to axis"]) == [20] and nums(r["Minimum distance"]) == [14]
    r = rows(M.measure([("face", face(holed, top)), ("edge", edge(holed, long_edge))]))
    assert nums(r["Angle"]) == [0]                          # an edge lying in a parallel plane


def test_multiple(holed):
    fs = [("face", face(holed, p)) for p in (top, bottom, side)]
    r = rows(M.measure(fs))
    assert abs(nums(r["Total area"])[0] - (2 * (800 - 18 * PI) + 200)) < 1e-3
    es = [("edge", edge(holed, rim(10))), ("edge", edge(holed, rim(30))), ("edge", edge(holed, long_edge))]
    assert abs(nums(rows(M.measure(es))["Total length"])[0] - (2 * 6 * PI + 40)) < 1e-3
    m = M.measure([("part", holed)] * 3)
    assert m["title"] == "3 parts"
    assert M.measure([("part", holed), ("face", face(holed, top)), ("edge", edge(holed, long_edge))])["rows"] == []
    assert M.measure([]) is None and M.measure([("part", None)]) is None
    assert "<table" in M.to_html(m) and M.to_html(None) == ""


def test_measure_rows_golden(holed):
    """The exact strings shown in the panel for a fixed set of selections."""
    sel = {
        "part": [("part", holed)],
        "top": [("face", face(holed, top))],
        "bore": [("face", face(holed, bore(10)))],
        "rim": [("edge", edge(holed, rim(10)))],
        "top_bottom": [("face", face(holed, top)), ("face", face(holed, bottom))],
        "bores": [("face", face(holed, bore(10))), ("face", face(holed, bore(30)))],
    }
    out = {k: [list(r) for r in M.measure(v)["rows"]] for k, v in sel.items()}
    assert_golden("measure/rows", out, ANALYTIC)
