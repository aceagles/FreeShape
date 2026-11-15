"""freeshape/svgimport.py: SVG text to neutral primitives (and on into a sketch through sketchimport)."""
import math
import os
import sys

import numpy as np
import pytest

pytest.importorskip("fontTools.svgLib.path", reason="fontTools is not installed")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from freeshape import svgimport as SV  # noqa: E402

PX = 25.4 / 96


def svg(body, attrs='width="1000mm" height="1000mm" viewBox="0 0 1000 1000"'):
    """An SVG with scale 1 (mm per user unit) unless attrs say otherwise."""
    return f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" {attrs}>{body}</svg>'


def flip(p):
    return (p[0], -p[1])


def rd(body, attrs=None, **kw):
    return SV.read(svg(body) if attrs is None else svg(body, attrs), **kw)


def types(prims):
    out = {}
    for p in prims:
        out[p["type"]] = out.get(p["type"], 0) + 1
    return out


def ends(prims):
    return [(p["p1"], p["p2"]) for p in prims if p["type"] == "line"]


# ------------------------------------------------------------------------------------------ path grammar
def test_closed_square_four_lines_with_y_flip():
    prims, rep = rd('<path d="M0 0h10v10h-10z"/>')
    assert types(prims) == {"line": 4}
    assert ends(prims) == [((0, 0), (10, 0)), ((10, 0), (10, -10)), ((10, -10), (0, -10)), ((0, -10), (0, 0))]
    assert rep["scale"] == 1


def test_relative_absolute_and_implicit_repeats():
    prims, _ = rd('<path d="M1 1 l2 0 3 0 L 10 10 20 20 H30 v-5"/>')
    pts = [p["p2"] for p in prims]
    assert pts == [(3, -1), (6, -1), (10, -10), (20, -20), (30, -20), (30, -15)]


def test_odd_number_formats():
    prims, _ = rd('<path d="M0 0 L1e-3 .5.5 1 L-.5-1.5e1"/>')
    got = [p["p2"] for p in prims]
    assert got == [pytest.approx(w) for w in [(1e-3, -0.5), (0.5, -1), (-0.5, 15)]]
    prims2, _ = rd('<path d="M.5.5L1.5.25"/>')
    assert prims2[0]["p1"] == pytest.approx((0.5, -0.5)) and prims2[0]["p2"] == pytest.approx((1.5, -0.25))


def test_compact_arc_flags():
    prims, _ = rd('<path d="M0 0a5 5 0 01 10 0"/>')
    assert types(prims) == {"arc": 1}
    a = prims[0]
    assert a["c"] == pytest.approx((5, 0))
    assert {tuple(round(v, 9) for v in a["p1"]), tuple(round(v, 9) for v in a["p2"])} == {(0, 0), (10, 0)}


# ------------------------------------------------------------------------------------------ arcs
def test_arc_centre_and_radius():
    r = SV._arc_params((10, 0), 10, 10, 0, 0, 1, (0, 10))
    assert r[1] == pytest.approx((0, 0)) and r[2] == pytest.approx(10)
    prims, _ = rd('<path d="M10 0 A10 10 0 0 1 0 10"/>')
    a = prims[0]
    assert a["type"] == "arc" and a["c"] == pytest.approx((0, 0))
    assert math.hypot(*a["p1"]) == pytest.approx(10)


def test_four_flag_combinations():
    exp = {(0, 0): ((10, 10), -1), (0, 1): ((0, 0), 1), (1, 0): ((0, 0), -1), (1, 1): ((10, 10), 1)}
    for (large, sweep), (centre, sign) in exp.items():
        r = SV._arc_params((10, 0), 10, 10, 0, large, sweep, (0, 10))
        assert r[1] == pytest.approx(centre), (large, sweep)
        assert np.sign(r[6]) == sign, (large, sweep)
        assert abs(abs(r[6]) - (math.pi / 2 if not large else 3 * math.pi / 2)) < 1e-9


def test_radius_too_small_is_scaled_and_zero_radius_is_a_line():
    r = SV._arc_params((0, 0), 1, 1, 0, 0, 1, (10, 0))
    assert r[1] == pytest.approx((5, 0)) and r[2] == pytest.approx(5) and abs(r[6]) == pytest.approx(math.pi)
    prims, _ = rd('<path d="M0 0 A0 5 0 0 1 10 0"/>')
    assert types(prims) == {"line": 1}
    prims, _ = rd('<path d="M0 0 A2 2 0 0 1 10 0"/>')
    assert types(prims) == {"arc": 1} and prims[0]["c"] == pytest.approx((5, 0))


def test_quadratic_equals_raised_cubic():
    prims, _ = rd('<path d="M0 0 Q5 10 10 0"/>')
    assert types(prims) == {"bezier": 1}
    P = np.array(prims[0]["pts"], float)
    Q = np.array([(0, 0), (5, -10), (10, 0)], float)
    for t in np.linspace(0, 1, 9):
        cub = (1 - t) ** 3 * P[0] + 3 * (1 - t) ** 2 * t * P[1] + 3 * (1 - t) * t ** 2 * P[2] + t ** 3 * P[3]
        quad = (1 - t) ** 2 * Q[0] + 2 * (1 - t) * t * Q[1] + t ** 2 * Q[2]
        assert np.abs(cub - quad).max() < 1e-12


# ------------------------------------------------------------------------------------------ transforms
def corners(prims):
    return [p["p1"] for p in prims]


def test_transform_stack_on_unit_square():
    prims, _ = rd('<path transform="translate(10,0) rotate(90) scale(2)" d="M0 0h1v1h-1z"/>')
    # scale 2, rotate 90 ((x, y) -> (-y, x)), translate 10 in x, then the y flip
    want = [(10, 0), (10, -2), (8, -2), (8, 0)]
    assert corners(prims) == [pytest.approx(w) for w in want]


def test_other_transform_forms():
    prims, _ = rd('<path transform="rotate(90 5 5)" d="M0 0L10 0"/>')
    assert prims[0]["p1"] == pytest.approx((10, -0)) and prims[0]["p2"] == pytest.approx((10, -10))
    prims, _ = rd('<path transform="matrix(1 0 0 1 3 4)" d="M0 0L1 0"/>')
    assert prims[0]["p1"] == pytest.approx((3, -4))
    prims, _ = rd('<path transform="skewX(45)" d="M0 0L0 10"/>')
    assert prims[0]["p2"] == pytest.approx((10, -10))
    prims, _ = rd('<path transform="skewY(45)" d="M0 0L10 0"/>')
    assert prims[0]["p2"] == pytest.approx((10, -10))
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        rd('<path transform="spin(3)" d="M0 0L1 0"/>')


def test_nested_groups_multiply_in_order():
    prims, _ = rd('<g transform="translate(10,0)"><g transform="scale(2)"><path d="M1 0L2 0"/></g></g>')
    assert prims[0]["p1"] == pytest.approx((12, 0)) and prims[0]["p2"] == pytest.approx((14, 0))
    prims, _ = rd('<g transform="scale(2)"><g transform="translate(10,0)"><path d="M1 0L2 0"/></g></g>')
    assert prims[0]["p1"] == pytest.approx((22, 0))


def test_use_with_xy_and_own_transform():
    body = ('<defs><path id="a" d="M0 0L1 0"/></defs>'
            '<g transform="translate(100,0)"><use xlink:href="#a" x="5" y="7" transform="scale(2)"/></g>')
    prims, rep = rd(body)
    # composed matrix: translate(100,0) scale(2) translate(5,7)
    assert prims[0]["p1"] == pytest.approx((110, -14)) and prims[0]["p2"] == pytest.approx((112, -14))
    assert rep["skipped"] == {}


def test_use_cycle_and_depth_errors():
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        rd('<g id="a"><use href="#a"/></g>')
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        rd('<defs><g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g></defs><use href="#a"/>')
    chain = "".join(f'<g id="g{i}"><use href="#g{i + 1}"/></g>' for i in range(40))
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        rd(f'<defs>{chain}<path id="g40" d="M0 0L1 0"/></defs><use href="#g0"/>')


# ------------------------------------------------------------------------------------------ units
def test_units():
    _, rep = SV.read(svg('<path d="M0 0L1 0"/>', 'width="100mm" height="50mm" viewBox="0 0 200 100"'))
    assert rep["scale"] == pytest.approx(0.5) and rep["unit_source"] == "viewBox"
    _, rep = SV.read(svg('<path d="M0 0L1 0"/>', ""))
    assert rep["scale"] == pytest.approx(25.4 / 96)
    prims, _ = SV.read(svg('<path d="M0 0L96 0"/>', ""))
    assert prims[0]["p2"][0] == pytest.approx(25.4)
    _, rep = SV.read(svg('<path d="M0 0L1 0"/>', 'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                                                  'inkscape:version="0.91 r13725"'))
    assert rep["scale"] == pytest.approx(25.4 / 90) and rep["ppi"] == 90
    _, rep = SV.read(svg('<path d="M0 0L1 0"/>', 'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                                                  'inkscape:version="1.2.2"'))
    assert rep["scale"] == pytest.approx(25.4 / 96)
    _, rep = SV.read(svg('<path d="M0 0L1 0"/>', ""), ppi=72)
    assert rep["scale"] == pytest.approx(25.4 / 72)
    # pt is 4/3 px
    _, rep = SV.read(svg('<path d="M0 0L1 0"/>', 'width="30pt" height="30pt" viewBox="0 0 30 30"'))
    assert rep["scale"] == pytest.approx(4 / 3 * PX)
    _, rep = SV.read(svg('<path d="M0 0L1 0"/>', 'width="2in" viewBox="0 0 100 100"'))
    assert rep["scale"] == pytest.approx(0.508)


def test_viewbox_origin_is_the_top_left_corner():
    prims, _ = SV.read(svg('<path d="M100 50L110 50"/>', 'width="10mm" height="10mm" viewBox="100 50 10 10"'))
    assert prims[0]["p1"] == pytest.approx((0, 0)) and prims[0]["p2"] == pytest.approx((10, 0))


# ------------------------------------------------------------------------------------------ y flip
def signed_area(prims):
    pts = [p["p1"] for p in prims]
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))


def test_orientation_is_mirrored_by_the_y_flip():
    # The path (0,0) -> (10,0) -> (10,10) -> (0,10) has positive signed area in SVG coordinates (clockwise
    # on a y-down screen). The result has the opposite sign: the same picture, in y-up coordinates.
    prims, _ = rd('<path d="M0 0H10V10H0z"/>')
    assert signed_area(prims) == pytest.approx(-100)
    prims, _ = rd('<path d="M0 0V10H10V0z"/>')
    assert signed_area(prims) == pytest.approx(100)


def test_arc_sweep_flag_lands_on_correct_side_of_chord():
    # Sweep 1 runs clockwise on screen: from (0,0) to (10,0) it passes over the top (SVG y = -5), which is
    # y = +5 in the result. Sweep 0 passes below. The result arc is counter-clockwise from p1 to p2.
    for sweep, want in ((1, 1), (0, -1)):
        prims, _ = rd(f'<path d="M0 0 A5 5 0 0 {sweep} 10 0"/>')
        a = prims[0]
        c = np.array(a["c"])
        a1 = math.atan2(*(np.array(a["p1"]) - c)[::-1])
        a2 = math.atan2(*(np.array(a["p2"]) - c)[::-1])
        d = (a2 - a1) % (2 * math.pi)
        assert d == pytest.approx(math.pi)
        assert np.sign(c[1] + 5 * math.sin(a1 + d / 2)) == want


def test_arc_ends_are_ccw_after_flip_for_both_sweeps():
    for sweep in (0, 1):
        prims, _ = rd(f'<path d="M10 0 A10 10 0 0 {sweep} 0 10"/>')
        a = prims[0]
        c = np.array(a["c"])
        a1 = math.atan2(*(np.array(a["p1"]) - c)[::-1])
        a2 = math.atan2(*(np.array(a["p2"]) - c)[::-1])
        assert (a2 - a1) % (2 * math.pi) == pytest.approx(math.pi / 2)


# ------------------------------------------------------------------------------------------ shapes
def test_rounded_rect_is_four_lines_and_four_native_arcs():
    prims, _ = rd('<rect x="0" y="0" width="40" height="30" rx="5"/>')
    assert types(prims) == {"line": 4, "arc": 4}
    for a in (p for p in prims if p["type"] == "arc"):
        c = np.array(a["c"])
        assert np.hypot(*(np.array(a["p1"]) - c)) == pytest.approx(5)
        assert np.hypot(*(np.array(a["p2"]) - c)) == pytest.approx(5)
        d = (math.atan2(*(np.array(a["p2"]) - c)[::-1]) - math.atan2(*(np.array(a["p1"]) - c)[::-1])) % (2 * math.pi)
        assert d == pytest.approx(math.pi / 2)


def test_plain_shapes():
    prims, rep = rd('<rect width="10" height="5"/><circle cx="3" cy="4" r="2"/><line x1="0" y1="0" x2="1" y2="1"/>'
                    '<polyline points="0,0 1,0 1,1"/><polygon points="0,0 1,0 1,1"/>')
    assert types(prims) == {"line": 4 + 1 + 2 + 3, "circle": 1}
    c = next(p for p in prims if p["type"] == "circle")
    assert c["c"] == pytest.approx((3, -4)) and c["r"] == pytest.approx(2)
    assert rep["counts"]["polygon"] == 1


def test_circle_under_non_uniform_scale_is_an_ellipse():
    prims, _ = rd('<circle transform="scale(2 1)" cx="0" cy="0" r="5"/>')
    e = prims[0]
    assert e["type"] == "ellipse" and e["r"] == pytest.approx(5)
    assert math.hypot(*e["a"]) == pytest.approx(10)


def test_ellipse_under_rotation_is_one_ellipse_of_the_right_area():
    prims, _ = rd('<ellipse transform="rotate(30)" cx="0" cy="0" rx="10" ry="4"/>')
    assert types(prims) == {"ellipse": 1}
    e = prims[0]
    a = math.hypot(e["a"][0] - e["c"][0], e["a"][1] - e["c"][1])
    assert a == pytest.approx(10) and e["r"] == pytest.approx(4)
    assert math.pi * a * e["r"] == pytest.approx(math.pi * 40)
    # the major axis points 30 degrees on screen, so -30 after the flip
    ang = math.degrees(math.atan2(e["a"][1], e["a"][0])) % 180
    assert ang == pytest.approx(150)


def test_arc_under_non_similarity_becomes_beziers_on_the_ellipse():
    prims, _ = rd('<path transform="scale(2 1)" d="M10 0 A10 10 0 0 1 0 10"/>')
    assert set(types(prims)) == {"bezier"} and len(prims) == 2
    for p in prims:
        for q in np.array(p["pts"])[[0, 3]]:
            assert (q[0] / 2) ** 2 + q[1] ** 2 == pytest.approx(100, rel=1e-9)
    mid = np.array(prims[0]["pts"][3])
    assert (mid[0] / 2) ** 2 + mid[1] ** 2 == pytest.approx(100, rel=1e-9)
    # a point part-way along a piece stays within 4.2e-6 r
    P = np.array(prims[0]["pts"])
    t = 0.5
    q = (1 - t) ** 3 * P[0] + 3 * (1 - t) ** 2 * t * P[1] + 3 * (1 - t) * t ** 2 * P[2] + t ** 3 * P[3]
    assert abs(math.sqrt((q[0] / 2) ** 2 + q[1] ** 2) - 10) < 1e-4


def test_layers_come_from_top_level_groups():
    prims, _ = rd('<g id="cut"><path d="M0 0L1 0"/></g><g id="eng"><g id="inner"><path d="M0 0L2 0"/></g></g>'
                  '<path d="M0 0L3 0"/>')
    assert [p["layer"] for p in prims] == ["cut", "eng", "0"]


# ------------------------------------------------------------------------------------------ skipped and errors
def test_skipped_elements_are_counted_and_the_rest_is_read():
    body = ('<text x="0" y="0">hi</text><image href="a.png" width="1" height="1"/>'
            '<defs><clipPath id="c"><rect width="1" height="1"/></clipPath><linearGradient id="g"/>'
            '<path id="used" d="M0 0L1 0"/><path id="unused" d="M0 0L2 0"/></defs>'
            '<path d="M0 0L5 0" display="none"/><path style="fill:red;display:none" d="M0 0L6 0"/>'
            '<use href="#used"/><path d="M0 0L7 0"/>')
    prims, rep = rd(body)
    assert [p["p2"][0] for p in prims] == [1, 7]
    assert rep["skipped"] == {"text": 1, "image": 1, "clipPath": 1, "gradient": 1, "display:none": 2, "defs": 1}


def test_stroke_only_file_is_reported():
    _, rep = rd('<path fill="none" stroke="black" d="M0 0L1 0"/>')
    assert rep["stroke_only"] and any("centre line" in n for n in rep["notes"])
    _, rep = rd('<path style="fill:none;stroke:#000" d="M0 0L1 0"/><path d="M0 0L2 0"/>')
    assert not rep["stroke_only"]


def test_unreadable_files_raise_value_error():
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        SV.read('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 0"/>')
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        SV.read("this is not xml")
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        SV.read("<html/>")
    with pytest.raises(ValueError, match="Not a readable SVG file"):
        SV.read('<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>')


def test_bad_path_data_is_skipped_and_counted():
    prims, rep = rd('<path d="L 1 2"/><path d="M0 0L1 0"/>')
    assert len(prims) == 1 and rep["skipped"] == {"bad path data": 1}


# ------------------------------------------------------------------------------------------ into a sketch
def _bez_pts(P, n=4000):
    P = np.asarray(P, float)
    t = np.linspace(0, 1, n + 1)[:, None]
    return (1 - t) ** 3 * P[0] + 3 * (1 - t) ** 2 * t * P[1] + 3 * (1 - t) * t ** 2 * P[2] + t ** 3 * P[3]


def _loop(cx, cy, rx, ry):
    """A closed path of four cubics around an ellipse-ish blob, as path data (no arcs)."""
    k = 0.5523
    return (f"M{cx + rx} {cy}C{cx + rx} {cy + k * ry} {cx + k * rx} {cy + ry} {cx} {cy + ry}"
            f"C{cx - k * rx} {cy + ry} {cx - rx} {cy + k * ry} {cx - rx} {cy}"
            f"C{cx - rx} {cy - k * ry} {cx - k * rx} {cy - ry} {cx} {cy - ry}"
            f"C{cx + k * rx} {cy - ry} {cx + rx} {cy - k * ry} {cx + rx} {cy}Z")


def test_logo_of_three_bezier_subpaths_makes_three_regions():
    pytest.importorskip("OCP")
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Pnt
    from freeshape import kernel as K, sketch as S, sketchimport as SI

    outer = _loop(100, 100, 60, 40)
    holes = _loop(80, 100, 12, 10) + _loop(120, 100, 10, 15)
    prims, rep = SV.read(svg(f'<path d="{outer}{holes}"/>'))
    assert types(prims) == {"bezier": 12}
    sk = S.Sketch()
    ids, irep = SI.insert(sk, prims, use_file_origin=True)
    plane = K.std_plane("Top")
    faces = K.sketch_regions(plane, sk.curves())
    assert len(faces) == 3

    def area(f):
        g = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, g)
        return g.Mass()

    # analytic area of the outer loop (Green's theorem on the dense Bezier samples); holes sit inside it
    loops = []
    for cx, cy, rx, ry in ((100, 100, 60, 40),):
        k = 0.5523
        quads = [((cx + rx, cy), (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry)),
                 ((cx, cy + ry), (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy)),
                 ((cx - rx, cy), (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry)),
                 ((cx, cy - ry), (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy))]
        loops.append(np.vstack([_bez_pts(q)[:-1] for q in quads]))
    pts = loops[0]
    true_area = 0.5 * abs(np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1]))
    total = sum(area(f) for f in faces)
    assert abs(total - true_area) / true_area < 1e-3

    # every sampled Bezier point lies within 0.01 of a sketch curve (the file's y flip, then centred as the
    # prims are, with use_file_origin the coordinates are the prims' own)
    comp = TopoDS_Compound()
    BRep_Builder().MakeCompound(comp)
    b = BRep_Builder()
    for f in faces:
        ex = TopExp_Explorer(f, TopAbs_EDGE)
        while ex.More():
            b.Add(comp, ex.Current())
            ex.Next()
    worst = 0.0
    for p in prims:
        for q in _bez_pts(p["pts"], 24)[::3]:
            v = BRepBuilderAPI_MakeVertex(gp_Pnt(float(q[0]), float(q[1]), 0.0)).Vertex()
            d = BRepExtrema_DistShapeShape(v, comp)
            d.Perform()
            worst = max(worst, d.Value())
    assert worst < 0.01


# ------------------------------------------------------------------------------------------ packaging
def test_dependency_is_declared():
    root = os.path.join(os.path.dirname(__file__), "..")
    for rel in ("requirements.txt", "pyproject.toml", "packaging/freeshape.spec", "THIRD-PARTY-NOTICES.txt"):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            assert "fonttools" in f.read().lower(), rel
